"""Case time tracking: heartbeat capture, manual entries, edit permissions, audit, admin rollup."""
import asyncio
import secrets
from datetime import datetime, timedelta, timezone

import httpx
from fastapi import HTTPException
from sqlalchemy import delete, select, update

from app.api import deps
from app.core.security import get_password_hash
from app.db.session import AsyncSessionLocal, engine
from app.main import app
from app.models.audit_log import AuditLog
from app.models.case import Case
from app.models.case_time_entry import CaseTimeEntry
from app.models.tenant import Tenant
from app.models.user import User


def _deny():
    raise HTTPException(status_code=403, detail="Insufficient permissions")


async def _scenario(monkeypatch, body):
    await engine.dispose()
    h = secrets.token_hex(3)
    ids = {"tenants": [], "users": [], "cases": []}
    state = {"role": "analyst", "user": None, "tenant": None}
    monkeypatch.setattr(deps, "_active_role", lambda u: state["role"])
    async with AsyncSessionLocal() as db:
        try:
            t = Tenant(name="ct-test", slug=f"ct-{h}")
            u1, u2 = (User(email=f"ct-{h}-{i}@example.test", hashed_password=get_password_hash(secrets.token_hex(8)),
                           is_active=True) for i in (1, 2))
            db.add_all([t, u1, u2])
            await db.flush()
            c = Case(title="ct-case", tenant_id=t.id)
            db.add(c)
            await db.flush()
            ids["tenants"].append(int(t.id))
            ids["users"] += [int(u1.id), int(u2.id)]
            ids["cases"].append(int(c.id))
            await db.commit()
            state["user"], state["tenant"] = u1, ids["tenants"][0]
            app.dependency_overrides[deps.get_current_active_user] = lambda: state["user"]
            app.dependency_overrides[deps.get_effective_tenant_id] = lambda: state["tenant"]
            app.dependency_overrides[deps.require_analyst_or_above] = (
                lambda: state["user"] if state["role"] in ("admin", "analyst") else _deny())
            app.dependency_overrides[deps.require_admin] = (
                lambda: state["user"] if state["role"] == "admin" else _deny())
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as client:
                await body(client, state, ids["cases"][0], ids["tenants"][0], [u1, u2])
        finally:
            app.dependency_overrides.clear()
            async with AsyncSessionLocal() as d:
                await d.execute(delete(AuditLog).where(AuditLog.tenant_id.in_(ids["tenants"])))
                await d.execute(delete(CaseTimeEntry).where(CaseTimeEntry.case_id.in_(ids["cases"])))
                await d.execute(delete(Case).where(Case.id.in_(ids["cases"])))
                await d.execute(delete(User).where(User.id.in_(ids["users"])))
                await d.execute(delete(Tenant).where(Tenant.id.in_(ids["tenants"])))
                await d.commit()


async def _entries(cid):
    async with AsyncSessionLocal() as d:
        return list((await d.execute(select(CaseTimeEntry).where(CaseTimeEntry.case_id == cid)
                                     .order_by(CaseTimeEntry.id))).scalars())


async def _shift(cid, seconds):
    async with AsyncSessionLocal() as d:
        await d.execute(update(CaseTimeEntry).where(CaseTimeEntry.case_id == cid).values(
            started_at=CaseTimeEntry.started_at - timedelta(seconds=seconds),
            ended_at=CaseTimeEntry.ended_at - timedelta(seconds=seconds)))
        await d.commit()


async def _audits(tid, etype="time_entry"):
    async with AsyncSessionLocal() as d:
        return list((await d.execute(select(AuditLog).where(
            AuditLog.tenant_id == tid, AuditLog.entity_type == etype))).scalars())


def test_heartbeat_extend_gap_and_no_audit(monkeypatch):
    async def body(c, st, cid, tid, users):
        url = f"/api/v1/cases/{cid}/time/heartbeat"
        assert (await c.post(url)).status_code == 204
        rows = await _entries(cid)
        assert len(rows) == 1 and rows[0].source == "auto" and rows[0].user_id == users[0].id
        await _shift(cid, 60)  # last beat was 60s ago -> same row extended
        assert (await c.post(url)).status_code == 204
        rows = await _entries(cid)
        assert len(rows) == 1 and 55 <= (rows[0].ended_at - rows[0].started_at).total_seconds() <= 70
        await _shift(cid, 600)  # 10 min gap -> new row
        assert (await c.post(url)).status_code == 204
        assert len(await _entries(cid)) == 2
        assert await _audits(tid) == []  # heartbeats are not audited
        assert (await c.post(f"/api/v1/cases/{cid + 10**6}/time/heartbeat")).status_code == 404
    asyncio.run(_scenario(monkeypatch, body))


def test_viewer_heartbeat_forbidden(monkeypatch):
    async def body(c, st, cid, tid, users):
        st["role"] = "viewer"
        assert (await c.post(f"/api/v1/cases/{cid}/time/heartbeat")).status_code == 403
        assert await _entries(cid) == []
    asyncio.run(_scenario(monkeypatch, body))


def test_manual_entry_validation_audit_and_summary(monkeypatch):
    async def body(c, st, cid, tid, users):
        base = f"/api/v1/cases/{cid}/time"
        t0 = datetime.now(timezone.utc) - timedelta(hours=2)
        bad = {"started_at": t0.isoformat(), "ended_at": (t0 - timedelta(minutes=5)).isoformat()}
        assert (await c.post(base, json=bad)).status_code == 422
        assert (await c.post(base, json={**bad, "ended_at": bad["started_at"]})).status_code == 422
        ok = {"started_at": t0.isoformat(), "ended_at": (t0 + timedelta(minutes=30)).isoformat(), "note": "triage"}
        r = await c.post(base, json=ok)
        assert r.status_code == 201 and r.json()["seconds"] == 1800 and r.json()["source"] == "manual", r.text
        a = await _audits(tid)
        assert [(x.action, x.entity_id) for x in a] == [("created", r.json()["id"])]
        s = (await c.get(base)).json()
        assert s["total_seconds"] == 1800 and s["by_user"][0]["user_id"] == users[0].id and len(s["entries"]) == 1
    asyncio.run(_scenario(monkeypatch, body))


def test_patch_delete_permissions(monkeypatch):
    async def body(c, st, cid, tid, users):
        base = f"/api/v1/cases/{cid}/time"
        t0 = datetime.now(timezone.utc) - timedelta(hours=3)
        r = await c.post(base, json={"started_at": t0.isoformat(), "ended_at": (t0 + timedelta(hours=1)).isoformat()})
        eid = r.json()["id"]
        st["user"] = users[1]  # another analyst
        assert (await c.patch(f"{base}/{eid}", json={"note": "x"})).status_code == 403
        assert (await c.delete(f"{base}/{eid}")).status_code == 403
        st["role"] = "admin"
        r = await c.patch(f"{base}/{eid}", json={"note": "fixed"})
        assert r.status_code == 200 and r.json()["note"] == "fixed"
        bad = await c.patch(f"{base}/{eid}", json={"ended_at": (t0 - timedelta(hours=1)).isoformat()})
        assert bad.status_code == 422
        assert (await c.delete(f"{base}/{eid}")).status_code == 204
        assert [x.action for x in await _audits(tid)] == ["created", "updated", "deleted"]
        assert (await c.delete(f"{base}/{eid}")).status_code == 404
    asyncio.run(_scenario(monkeypatch, body))


def test_stats_time_admin_only(monkeypatch):
    async def body(c, st, cid, tid, users):
        t0 = datetime.now(timezone.utc) - timedelta(hours=1)
        async with AsyncSessionLocal() as d:
            d.add(CaseTimeEntry(tenant_id=tid, case_id=cid, user_id=users[0].id, started_at=t0,
                                ended_at=t0 + timedelta(seconds=900), source="manual"))
            await d.commit()
        assert (await c.get("/api/v1/stats/time")).status_code == 403
        st["role"] = "admin"
        r = await c.get("/api/v1/stats/time")
        assert r.status_code == 200
        assert r.json()["rows"] == [{"user_email": users[0].email, "case_id": cid, "case_title": "ct-case",
                                     "severity": "medium", "seconds": 900}], r.text
        r = await c.get("/api/v1/stats/time?format=csv")
        assert r.headers["content-type"].startswith("text/csv") and "attachment" in r.headers["content-disposition"]
        lines = r.text.strip().splitlines()
        assert lines[0] == "user_email,case_id,case_title,severity,seconds" and lines[1].endswith(",900")
    asyncio.run(_scenario(monkeypatch, body))
