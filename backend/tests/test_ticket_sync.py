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
from app.models.case import Case, CaseLink, CaseSeverity, CaseStatus, TimelineEvent  # noqa: F401
from app.models.case_task import CaseTask
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
        case = Case(title="Phishing at ACME", tenant_id=t.id, severity=CaseSeverity.HIGH, description="bad mail")
        foreign = Case(title="other tenant", tenant_id=t2.id)
        db.add_all([case, foreign])
        await db.flush()
        isolate = CaseTask(case_id=case.id, tenant_id=t.id, phase="containment", title="Isolate the laptop",
                           description="Network-contain WS-01")
        reset = CaseTask(case_id=case.id, tenant_id=t.id, phase="eradication", title="Reset the password")
        ftask = CaseTask(case_id=foreign.id, tenant_id=t2.id, title="theirs")
        db.add_all([isolate, reset, ftask])
        await db.commit()
        tid, t2id, uid, cid, fid = t.id, t2.id, user.id, case.id, foreign.id
        isolate_id, reset_id, ftask_id = isolate.id, reset.id, ftask.id

    def deny():
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    app.dependency_overrides[deps.get_current_active_user] = lambda: user
    app.dependency_overrides[deps.get_effective_tenant_id] = lambda: tid
    app.dependency_overrides[deps.require_admin] = lambda: user if role["v"] == "admin" else deny()
    app.dependency_overrides[deps.require_analyst_or_above] = lambda: user if role["v"] != "viewer" else deny()
    case_ids = [cid, fid]
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
            r = await c.put("/api/v1/ticketing/connection", json={**body, "credentials": None})
            assert r.status_code == 200, r.text
            # … but never carry them to another host
            r = await c.put("/api/v1/ticketing/connection", json={**body, "credentials": None,
                                                                   "base_url": "https://attacker.example"})
            assert r.status_code == 422 and "host" in r.text, r.text
            assert (await c.post("/api/v1/ticketing/connection/test")).json() == {"ok": True}

            # a ticket per task: the task's title and the case go in the ticket
            r = await c.post(f"/api/v1/cases/{cid}/tasks/{isolate_id}/ticket")
            assert r.status_code == 201, r.text
            assert r.json()["external_id"] == "SEC-1" and r.json()["url"] == "https://acme.atlassian.net/browse/SEC-1"
            created = next(b for m, p, b in fake.calls if p.endswith("/issue"))["fields"]
            assert created["summary"] == f"[SOC Hub #{cid}] Isolate the laptop" and created["project"] == {"key": "SEC"}
            assert "Network-contain WS-01" in ticket_sync._adf_text(created["description"])
            assert (await c.post(f"/api/v1/cases/{cid}/tasks/{isolate_id}/ticket")).status_code == 502  # once
            assert (await c.post(f"/api/v1/cases/{fid}/tasks/{ftask_id}/ticket")).status_code == 404
            assert (await c.post(f"/api/v1/cases/{cid}/tasks/{ftask_id}/ticket")).status_code == 404
            tasks = {x["id"]: x for x in (await c.get(f"/api/v1/cases/{cid}/tasks")).json()}
            assert tasks[isolate_id]["ticket"]["external_id"] == "SEC-1" and tasks[reset_id]["ticket"] is None

            # pull: status and comments
            fake.status = ("In Progress", False)
            fake.comments = [{"id": "11", "author": {"displayName": "IT Ivan"}, "body": {"type": "doc", "content": [
                {"type": "paragraph", "content": [{"type": "text", "text": "Laptop is off the network"}]}]}}]
            r = await c.post(f"/api/v1/cases/{cid}/tasks/{isolate_id}/ticket/sync")
            assert r.status_code == 200 and r.json()["remote_status"] == "In Progress", r.text
            assert pushed(fake) == []
            # closing the ticket ticks the task, with the ticket as its result
            fake.status = ("Done", True)
            r = await c.post(f"/api/v1/cases/{cid}/tasks/{isolate_id}/ticket/sync")
            assert r.json()["remote_closed"] is True
            done = {x["id"]: x for x in (await c.get(f"/api/v1/cases/{cid}/tasks")).json()}[isolate_id]
            assert done["status"] == "done" and done["result"] == "Closed in Jira SEC-1 (Done)" and done["result_status"] == "ok"
            assert pushed(fake) == []  # closed there: nothing to tell the ticket

            # ticking a task in SOC Hub posts its result on the ticket
            r = await c.post(f"/api/v1/cases/{cid}/tasks/{reset_id}/ticket")
            assert r.json()["external_id"] == "SEC-2"
            async with AsyncSessionLocal() as db:
                rt = await db.get(CaseTask, reset_id)
                rt.result, rt.result_status = "Password reset by @[Bo](user:1)", "ok"
                await db.commit()
            assert (await c.put(f"/api/v1/cases/{cid}/tasks/{reset_id}", json={"status": "done"})).status_code == 200
            fake.status, fake.comments = ("To Do", False), []
            await c.post(f"/api/v1/cases/{cid}/tasks/{reset_id}/ticket/sync")
            flat = [ticket_sync._adf_text(b["body"]) for b in pushed(fake)]
            assert flat == ["[SOC Hub] Task completed: Password reset by @Bo"], flat
            await c.post(f"/api/v1/cases/{cid}/tasks/{reset_id}/ticket/sync")
            assert len(pushed(fake)) == 1  # posted once

            role["v"] = "viewer"
            assert (await c.post(f"/api/v1/cases/{cid}/tasks/{reset_id}/ticket/sync")).status_code == 403
            role["v"] = "admin"

        # periodic job: auto-creates tickets for tasks added after the setting, on open cases at/above the floor
        async with AsyncSessionLocal() as db:
            db.add(CaseTask(case_id=cid, tenant_id=tid, title="Hunt for the same sender"))
            low = Case(title="low", tenant_id=tid, severity=CaseSeverity.LOW)
            db.add(low)
            await db.flush()
            case_ids.append(low.id)
            db.add(CaseTask(case_id=low.id, tenant_id=tid, title="Ignored: low case"))
            await db.commit()
            conn = (await db.execute(select(TicketConnection).where(TicketConnection.tenant_id == tid))).scalar_one()
            r = await ticket_sync.sync_tenant(db, conn)
            assert r["created"] == 1 and r["errors"] == [], r
        async with AsyncSessionLocal() as db:
            links = (await db.execute(select(CaseLink).where(CaseLink.tenant_id == tid))).scalars().all()
            titles = {(await db.get(CaseTask, lk.task_id)).title for lk in links}
            assert titles == {"Isolate the laptop", "Reset the password", "Hunt for the same sender"}, titles
            events = (await db.execute(select(TimelineEvent.event_type, TimelineEvent.content).where(
                TimelineEvent.case_id == cid))).all()
            assert any(et == "ticket_comment" and "IT Ivan" in ct and "Isolate the laptop" in ct for et, ct in events)
            assert any(et == "ticket_status" and "Done" in ct for et, ct in events)
            acts = set((await db.execute(select(AuditLog.action).where(AuditLog.tenant_id == tid))).scalars())
            assert {"create", "update", "ticket_created"} <= acts, acts
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            role["v"] = "analyst"
            assert (await c.get("/api/v1/ticketing/connection")).status_code == 403
            role["v"] = "admin"
            assert (await c.delete("/api/v1/ticketing/connection")).status_code == 204
            tasks = (await c.get(f"/api/v1/cases/{cid}/tasks")).json()
            assert all(x["ticket"] is None for x in tasks)  # links kept, no longer synced
    finally:
        app.dependency_overrides.clear()
        async with AsyncSessionLocal() as db:
            await db.execute(delete(AuditLog).where(AuditLog.tenant_id.in_([tid, t2id])))
            await db.execute(delete(TimelineEvent).where(TimelineEvent.case_id.in_(case_ids)))
            await db.execute(delete(CaseLink).where(CaseLink.case_id.in_(case_ids)))
            await db.execute(delete(CaseTask).where(CaseTask.case_id.in_(case_ids)))
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
        task = CaseTask(case_id=cid, tenant_id=t.id, title="Isolate")
        db.add(task)
        await db.commit()
        task_id = task.id
        p = _p(user, t)
        with pytest.raises(ToolError):
            await tools.create_task_ticket(db, p, case_id=cid, task_id=task_id)  # not set up
        db.add(TicketConnection(tenant_id=t.id, provider="jira", base_url="https://acme.atlassian.net",
                                credentials_enc=ticket_sync.encrypt_credentials({"email": "e", "token": "t"}),
                                settings={"project_key": "SEC"}))
        await db.commit()
        r = await tools.create_task_ticket(db, p, case_id=cid, task_id=task_id)
        assert r["ticket"]["external_id"] == "SEC-1"
        listed = (await tools.list_case_tasks(db, p, case_id=cid))["tasks"][0]
        assert listed["ticket"]["external_id"] == "SEC-1"
        fake.status = ("Done", True)
        await tools.sync_task_ticket(db, p, case_id=cid, task_id=task_id)
        assert (await tools.list_case_tasks(db, p, case_id=cid))["tasks"][0]["status"] == "done"
        with pytest.raises(ToolError):
            await tools.sync_task_ticket(db, _p(other, t2), case_id=cid, task_id=task_id)
        await db.refresh(user)
        await db.execute(delete(CaseLink).where(CaseLink.case_id == cid))
        await db.execute(delete(CaseTask).where(CaseTask.case_id == cid))
        await db.commit()
    _run(body)


def test_jira_service_account_oauth(monkeypatch):
    """Service account: token from auth.atlassian.com (client credentials, once per client), Jira calls through
    api.atlassian.com/ex/jira/<cloud id>, browse links on the site."""
    calls = []

    async def fake(conn, allowlist, method, path, *, headers, json_body=None, form=None, url=None):
        calls.append((method, url or path, headers, form, json_body))
        if url == ticket_sync.ATLASSIAN_TOKEN_URL:
            return {"access_token": "sa-token", "expires_in": 3600}
        if method == "POST" and (url or "").endswith("/issue"):
            return {"id": "1", "key": "SEC-9"}
        if "?fields=status" in (url or ""):
            return {"fields": {"status": {"name": "Done", "statusCategory": {"key": "done"}}}}
        return {"id": "5"}
    monkeypatch.setattr(ticket_sync, "_call", fake)

    async def run():
        from types import SimpleNamespace
        conn = SimpleNamespace(id=1, tenant_id=1, provider="jira", base_url="https://acme.atlassian.net", push_notes=False,
                               settings={"deployment": "service_account", "cloud_id": "11223344-aaaa-bbbb-cccc-556677889900",
                                         "project_key": "SEC"},
                               credentials_enc=ticket_sync.encrypt_credentials({"client_id": "cid", "client_secret": "cs"}))
        cl = ticket_sync.client(conn, [])
        t = await cl.create("T", "D", "high")
        assert t == {"external_id": "SEC-9", "key": "SEC-9", "url": "https://acme.atlassian.net/browse/SEC-9"}
        assert await cl.status("SEC-9") == {"status": "Done", "closed": True}
        token_calls = [c for c in calls if c[1] == ticket_sync.ATLASSIAN_TOKEN_URL]
        assert len(token_calls) == 1 and token_calls[0][3] == {"grant_type": "client_credentials", "client_id": "cid",
                                                              "client_secret": "cs"}
        api = [c for c in calls if c[1] != ticket_sync.ATLASSIAN_TOKEN_URL]
        assert api[0][1] == "https://api.atlassian.com/ex/jira/11223344-aaaa-bbbb-cccc-556677889900/rest/api/3/issue"
        assert all(c[2]["Authorization"] == "Bearer sa-token" for c in api)
    asyncio.run(run())


def test_service_account_settings_validation():
    async def go():
        await engine.dispose()
        h = secrets.token_hex(3)
        async with AsyncSessionLocal() as db:
            t = Tenant(name="sa", slug=f"sa-{h}")
            u = User(email=f"sa-{h}@example.test", hashed_password=get_password_hash(secrets.token_hex(8)), is_active=True)
            db.add_all([t, u])
            await db.commit()
            tid, uid = t.id, u.id
        app.dependency_overrides[deps.require_admin] = lambda: u
        app.dependency_overrides[deps.get_effective_tenant_id] = lambda: tid
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
                body = {"provider": "jira", "base_url": "https://acme.atlassian.net",
                        "settings": {"deployment": "service_account", "project_key": "SEC"},
                        "credentials": {"client_id": "cid", "client_secret": "cs"}}
                r = await c.put("/api/v1/ticketing/connection", json=body)
                assert r.status_code == 422 and "cloud_id" in r.text, r.text
                r = await c.put("/api/v1/ticketing/connection", json={**body, "settings": {**body["settings"], "cloud_id": "../../admin"}})
                assert r.status_code == 422, r.text
                r = await c.put("/api/v1/ticketing/connection", json={**body, "settings": {**body["settings"], "cloud_id": "11223344-aaaa"}})
                assert r.status_code == 200 and r.json()["settings"]["deployment"] == "service_account", r.text
        finally:
            app.dependency_overrides.clear()
            async with AsyncSessionLocal() as db:
                await db.execute(delete(AuditLog).where(AuditLog.tenant_id == tid))
                await db.execute(delete(TicketConnection).where(TicketConnection.tenant_id == tid))
                await db.execute(delete(User).where(User.id == uid))
                await db.execute(delete(Tenant).where(Tenant.id == tid))
                await db.commit()
            await engine.dispose()
    asyncio.run(go())
