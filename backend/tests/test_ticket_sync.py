"""Ticket sync: connection API (admin, no secrets returned), create/sync a case ticket, auto-create, pull of
status/comments, opt-in note push, resolution push, provider payloads, SSRF guard and tenant isolation.

The vendor HTTP layer (ticket_sync._call) is faked; everything else runs against the dev DB and is cleaned up
by id."""
import asyncio
import secrets
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi import HTTPException
from sqlalchemy import delete, select

from app.api import deps
from app.core.security import get_password_hash
from app.db.session import AsyncSessionLocal, engine
from app.main import app
from app.models.audit_log import AuditLog
from app.models.case import Case, CaseLink, CaseSeverity, CaseStatus, TimelineEvent
from app.models.tenant import Tenant
from app.models.ticket_connection import TicketConnection
from app.models.user import User
from app.services import ticket_sync
from app.services.ticket_sync import TicketError


class FakeVendor:
    """Records calls; answers like a tiny Jira."""

    def __init__(self):
        self.calls, self.status, self.comments, self.next_key = [], ("To Do", False), [], 1

    async def __call__(self, conn, allowlist, method, path, *, headers, json_body=None, form=None, url=None):
        self.calls.append((method, path, json_body))
        if method == "POST" and path.endswith("/issue"):
            key = f"SEC-{self.next_key}"
            self.next_key += 1
            return {"id": "100", "key": key}
        if "?fields=status" in path:
            name, done = self.status
            return {"fields": {"status": {"name": name, "statusCategory": {"key": "done" if done else "new"}}}}
        if method == "GET" and "/comment" in path:
            return {"comments": self.comments}
        if method == "POST" and "/comment" in path:
            return {"id": str(9000 + len(self.calls))}
        if method == "GET" and "/project/" in path:
            return {"key": "SEC"}
        raise AssertionError(f"unexpected {method} {path}")


def pushed(fake):
    return [b for m, p, b in fake.calls if m == "POST" and p.endswith("/comment")]


async def _scenario(monkeypatch):
    await engine.dispose()
    fake = FakeVendor()
    monkeypatch.setattr(ticket_sync, "_call", fake)
    h = secrets.token_hex(3)
    role = {"v": "admin"}
    async with AsyncSessionLocal() as db:
        t, t2 = Tenant(name="tk", slug=f"tk-{h}-a"), Tenant(name="tk", slug=f"tk-{h}-b")
        user = User(email=f"tk-{h}@example.test", full_name="Ana Analyst",
                    hashed_password=get_password_hash(secrets.token_hex(8)), is_active=True)
        db.add_all([t, t2, user])
        await db.flush()
        old = Case(title="before the connection", tenant_id=t.id, severity=CaseSeverity.CRITICAL,
                   created_at=datetime.now(timezone.utc) - timedelta(days=2))
        manual = Case(title="Phishing at ACME", tenant_id=t.id, severity=CaseSeverity.LOW, description="bad mail")
        foreign = Case(title="other tenant", tenant_id=t2.id)
        db.add_all([old, manual, foreign])
        await db.commit()
        tid, t2id, uid, old_id, manual_id, foreign_id = t.id, t2.id, user.id, old.id, manual.id, foreign.id

    def deny():
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    app.dependency_overrides[deps.get_current_active_user] = lambda: user
    app.dependency_overrides[deps.get_effective_tenant_id] = lambda: tid
    app.dependency_overrides[deps.require_admin] = lambda: user if role["v"] == "admin" else deny()
    app.dependency_overrides[deps.require_analyst_or_above] = lambda: user if role["v"] != "viewer" else deny()
    case_ids = [old_id, manual_id, foreign_id]
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            assert (await c.get("/api/v1/ticketing/connection")).json() is None
            body = {"provider": "jira", "base_url": "https://acme.atlassian.net/",
                    "credentials": {"email": "soc@acme.example", "token": "s3cret-token"},
                    "settings": {"project_key": "SEC"}, "auto_create_min_severity": "high"}
            assert (await c.put("/api/v1/ticketing/connection", json={**body, "base_url": "http://x"})).status_code == 422
            assert (await c.put("/api/v1/ticketing/connection", json={**body, "credentials": {"token": "x"}})).status_code == 422
            r = await c.put("/api/v1/ticketing/connection", json=body)
            assert r.status_code == 200, r.text
            assert "s3cret" not in r.text and r.json()["has_credentials"] and r.json()["base_url"] == "https://acme.atlassian.net"
            # keep the stored credentials when omitted (same provider and host) …
            r = await c.put("/api/v1/ticketing/connection", json={**body, "credentials": None, "push_notes": False})
            assert r.status_code == 200, r.text
            # … but never carry them to another host
            r = await c.put("/api/v1/ticketing/connection", json={**body, "credentials": None,
                                                                   "base_url": "https://attacker.example"})
            assert r.status_code == 422 and "host" in r.text, r.text
            assert (await c.post("/api/v1/ticketing/connection/test")).json() == {"ok": True}

            # manual create for a low-severity case; a second create is refused
            r = await c.post(f"/api/v1/cases/{manual_id}/ticket")
            assert r.status_code == 201, r.text
            assert r.json()["external_id"] == "SEC-1" and r.json()["url"] == "https://acme.atlassian.net/browse/SEC-1"
            created = next(b for m, p, b in fake.calls if p.endswith("/issue"))
            assert created["fields"]["project"] == {"key": "SEC"} and "[SOC Hub #" in created["fields"]["summary"]
            assert (await c.post(f"/api/v1/cases/{manual_id}/ticket")).status_code == 502
            assert (await c.post(f"/api/v1/cases/{foreign_id}/ticket")).status_code == 404

            # an analyst note before push_notes is on is not pushed later
            async with AsyncSessionLocal() as db:
                db.add(TimelineEvent(case_id=manual_id, user_id=uid, event_type="comment", content="early @[Bo](user:1)"))
                await db.commit()
            fake.status = ("In Progress", False)
            fake.comments = [{"id": "11", "author": {"displayName": "Client Carl"},
                              "body": {"type": "doc", "content": [{"type": "paragraph", "content": [
                                  {"type": "text", "text": "Is the mailbox safe?"}]}]}}]
            r = await c.post(f"/api/v1/cases/{manual_id}/ticket/sync")
            assert r.status_code == 200 and r.json()["remote_status"] == "In Progress", r.text
            assert pushed(fake) == []

        # periodic job: auto-creates only new cases at/above the floor, then syncs
        async with AsyncSessionLocal() as db:
            hot = Case(title="Ransomware", tenant_id=tid, severity=CaseSeverity.CRITICAL)
            mild = Case(title="Spam", tenant_id=tid, severity=CaseSeverity.MEDIUM)
            db.add_all([hot, mild])
            await db.commit()
            case_ids += [hot.id, mild.id]
            hot_id = hot.id
            conn = (await db.execute(select(TicketConnection).where(TicketConnection.tenant_id == tid))).scalar_one()
            conn.push_notes = True
            await db.commit()
            db.add(TimelineEvent(case_id=manual_id, user_id=uid, event_type="comment", content="Reset done @[Bo](user:1)"))
            db.add(TimelineEvent(case_id=manual_id, event_type="ticket_comment", content="echo"))  # never pushed
            mc = await db.get(Case, manual_id)
            mc.status, mc.outcome = CaseStatus.RESOLVED, "true_positive"
            await db.commit()
            fake.status = ("Done", True)
            fake.comments.append({"id": "12", "author": {"displayName": "Jira"}, "body": "[SOC Hub] our own echo"})
            r = await ticket_sync.sync_tenant(db, conn)
            assert r["created"] == 1 and r["errors"] == [], r
        async with AsyncSessionLocal() as db:
            links = {lk.case_id: lk for lk in (await db.execute(select(CaseLink).where(CaseLink.tenant_id == tid))).scalars()}
            assert set(links) == {manual_id, hot_id}  # not the old critical case, not the medium one
            assert links[manual_id].remote_closed and links[manual_id].remote_cursor == "12"
            texts = [b["body"] for b in pushed(fake)]
            flat = [ticket_sync._adf_text(x) for x in texts]
            assert any("Reset done @Bo" in x and "Ana Analyst" in x for x in flat), flat
            assert any("Case resolved (outcome: true positive)" in x for x in flat), flat
            assert not any("early" in x or "echo" in x for x in flat), flat
            events = (await db.execute(select(TimelineEvent.event_type, TimelineEvent.content).where(
                TimelineEvent.case_id == manual_id))).all()
            imported = [ct for et, ct in events if et == "ticket_comment" and "Client Carl" in ct]
            assert len(imported) == 1 and "Is the mailbox safe?" in imported[0]
            assert not any("our own echo" in ct for _, ct in events)
            assert any(et == "ticket_status" and "Done" in ct for et, ct in events)
            # sync again: nothing new is pushed or imported
            n_calls = len(pushed(fake))
            conn = (await db.execute(select(TicketConnection).where(TicketConnection.tenant_id == tid))).scalar_one()
            await ticket_sync.sync_tenant(db, conn)
            assert len(pushed(fake)) == n_calls
            acts = set((await db.execute(select(AuditLog.action).where(AuditLog.tenant_id == tid))).scalars())
            assert {"create", "update", "ticket_created"} <= acts, acts

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            role["v"] = "analyst"
            assert (await c.get("/api/v1/ticketing/connection")).status_code == 403
            assert (await c.get(f"/api/v1/cases/{manual_id}/ticket")).json()["ticket"]["external_id"] == "SEC-1"
            role["v"] = "viewer"
            assert (await c.post(f"/api/v1/cases/{manual_id}/ticket/sync")).status_code == 403
            role["v"] = "admin"
            assert (await c.delete("/api/v1/ticketing/connection")).status_code == 204
            t = (await c.get(f"/api/v1/cases/{manual_id}/ticket")).json()
            assert t["configured"] is False and t["ticket"] is None  # link kept, no longer synced
    finally:
        app.dependency_overrides.clear()
        async with AsyncSessionLocal() as db:
            await db.execute(delete(AuditLog).where(AuditLog.tenant_id.in_([tid, t2id])))
            await db.execute(delete(TimelineEvent).where(TimelineEvent.case_id.in_(case_ids)))
            await db.execute(delete(CaseLink).where(CaseLink.case_id.in_(case_ids)))
            await db.execute(delete(TicketConnection).where(TicketConnection.tenant_id.in_([tid, t2id])))
            await db.execute(delete(Case).where(Case.id.in_(case_ids)))
            await db.execute(delete(User).where(User.id == uid))
            await db.execute(delete(Tenant).where(Tenant.id.in_([tid, t2id])))
            await db.commit()
        await engine.dispose()


def test_ticket_sync_flow(monkeypatch):
    asyncio.run(_scenario(monkeypatch))


def _conn(provider, settings):
    from types import SimpleNamespace
    return SimpleNamespace(id=1, tenant_id=1, provider=provider, base_url="https://psa.example", settings=settings,
                           push_notes=True, credentials_enc=ticket_sync.encrypt_credentials(
                               {"client_id": "cid", "client_secret": "cs", "company_id": "acme",
                                "public_key": "pub", "private_key": "priv"}))


def test_halopsa_and_connectwise_payloads(monkeypatch):
    calls = []

    async def fake(conn, allowlist, method, path, *, headers, json_body=None, form=None, url=None):
        calls.append((method, path, json_body, form, headers))
        if path == "auth/token":
            return {"access_token": "tok"}
        if path == "api/Tickets":
            return [{"id": 42}]
        if path == "api/Tickets/42":
            return {"status_id": 9, "status_name": "Closed"}
        if path.startswith("api/Actions?"):
            return {"actions": [{"id": 5, "who": "Client", "note": "hi"}, {"id": 3, "who": "x", "note": "old"}]}
        if path == "service/tickets":
            return {"id": 77}
        if path == "service/tickets/77":
            return {"status": {"name": "Closed (resolved)"}, "closedFlag": True}
        return {"id": 1}
    monkeypatch.setattr(ticket_sync, "_call", fake)

    async def run():
        halo = ticket_sync.client(_conn("halopsa", {"tickettype_id": "3", "client_id": "12"}), [])
        t = await halo.create("T", "D", "high")
        assert t["external_id"] == "42"
        assert calls[0][1] == "auth/token" and calls[0][3]["grant_type"] == "client_credentials"
        assert calls[1][2] == [{"summary": "T", "details": "D", "tickettype_id": 3, "client_id": 12}]
        assert calls[1][4]["Authorization"] == "Bearer tok"
        assert await halo.status("42") == {"status": "Closed", "closed": True}
        assert [c["id"] for c in await halo.comments("42", "4")] == ["5"]
        await halo.add_comment("42", "note")
        assert calls[-1][2][0]["hiddenfromuser"] is True  # internal by default

        cw = ticket_sync.client(_conn("connectwise", {"board_id": "7", "company_identifier": "ACME"}), [])
        t = await cw.create("T", "D", "high")
        body, hdr = calls[-1][2], calls[-1][4]
        assert t["external_id"] == "77" and body["board"] == {"id": 7} and body["company"] == {"identifier": "ACME"}
        assert hdr["clientId"] == "cid" and hdr["Authorization"].startswith("Basic ")
        assert await cw.status("77") == {"status": "Closed (resolved)", "closed": True}
        await cw.add_comment("77", "note")
        assert calls[-1][2]["internalAnalysisFlag"] is True
    asyncio.run(run())


def test_requests_go_through_the_ssrf_guard():
    from types import SimpleNamespace
    conn = SimpleNamespace(provider="jira", base_url="https://127.0.0.1")
    with pytest.raises(TicketError):
        asyncio.run(ticket_sync._call(conn, [], "GET", "rest/api/3/myself", headers={}))
    conn.base_url = "http://acme.atlassian.net"
    with pytest.raises(TicketError):
        asyncio.run(ticket_sync._call(conn, [], "GET", "x", headers={}))


def test_mcp_ticket_tools(monkeypatch):
    from app.mcp import tools
    from app.mcp.tools import ToolError
    from tests.guest_fixtures import make_case, make_tenant
    from tests.test_mcp_tools import _member, _p, _run
    fake = FakeVendor()
    monkeypatch.setattr(ticket_sync, "_call", fake)

    async def body(db, ids):
        t, t2 = await make_tenant(db, ids), await make_tenant(db, ids)
        user, other = await _member(db, ids, t), await _member(db, ids, t2)
        cid = (await make_case(db, ids, t)).id
        p = _p(user, t)
        with pytest.raises(ToolError):
            await tools.create_case_ticket(db, p, case_id=cid)  # not set up
        db.add(TicketConnection(tenant_id=t.id, provider="jira", base_url="https://acme.atlassian.net",
                                credentials_enc=ticket_sync.encrypt_credentials({"email": "e", "token": "t"}),
                                settings={"project_key": "SEC"}))
        await db.commit()
        r = await tools.create_case_ticket(db, p, case_id=cid)
        assert r["ticket"]["external_id"] == "SEC-1"
        assert (await tools.get_case_ticket(db, p, case_id=cid))["ticket"]["external_id"] == "SEC-1"
        with pytest.raises(ToolError):
            await tools.get_case_ticket(db, _p(other, t2), case_id=cid)
    _run(body)
