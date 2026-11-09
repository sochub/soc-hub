"""Optimistic version checks on report, activity-note and investigation-event saves.

Matrix (base_updated_at vs the row's stored updated_at, compared to the second):
  omitted                      -> no check (200)
  sent == row (or both NULL)   -> 200, updated_at advances
  sent != row, null vs value, value vs null -> 409 {"detail": "version_conflict", "updated_at": <current>}
"""
import asyncio
import secrets

import httpx
from sqlalchemy import delete
from sqlalchemy.orm.attributes import set_committed_value

from app.api import deps
from app.core.security import get_password_hash
from app.db.session import AsyncSessionLocal, engine
from app.main import app
from app.models.audit_log import AuditLog
from app.models.case import Case, TimelineEvent
from app.models.case_event import CaseEvent
from app.models.case_report import CaseReport
from app.models.tenant import Tenant
from app.models.user import User


async def _run(monkeypatch, body):
    await engine.dispose()
    h = secrets.token_hex(3)
    ids = {"tenants": [], "users": [], "cases": []}
    async with AsyncSessionLocal() as db:
        try:
            t, t2 = Tenant(name="ver-test", slug=f"ver-{h}-a"), Tenant(name="ver-test", slug=f"ver-{h}-b")
            user = User(email=f"ver-{h}@example.test", hashed_password=get_password_hash(secrets.token_hex(8)),
                        is_active=True, is_super_admin=True)
            db.add_all([t, t2, user])
            await db.flush()
            ids["tenants"] += [int(t.id), int(t2.id)]
            ids["users"].append(int(user.id))
            c = Case(title="ver-a", tenant_id=t.id)
            db.add(c)
            await db.flush()
            ids["cases"].append(int(c.id))
            await db.commit()
            state = {"tenant": ids["tenants"][0], "user": user}
            app.dependency_overrides[deps.get_current_active_user] = lambda: state["user"]
            app.dependency_overrides[deps.get_effective_tenant_id] = lambda: state["tenant"]
            app.dependency_overrides[deps.require_analyst_or_above] = lambda: state["user"]
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as client:
                await body(client, ids, state, user)
        finally:
            app.dependency_overrides.clear()
            async with AsyncSessionLocal() as db3:
                cs = ids["cases"]
                await db3.execute(delete(TimelineEvent).where(TimelineEvent.case_id.in_(cs)))
                await db3.execute(delete(CaseEvent).where(CaseEvent.case_id.in_(cs)))
                await db3.execute(delete(CaseReport).where(CaseReport.case_id.in_(cs)))
                await db3.execute(delete(AuditLog).where(AuditLog.tenant_id.in_(ids["tenants"])))
                await db3.execute(delete(Case).where(Case.id.in_(cs)))
                await db3.execute(delete(User).where(User.id.in_(ids["users"])))
                await db3.execute(delete(Tenant).where(Tenant.id.in_(ids["tenants"])))
                await db3.commit()


def _conflict(r):
    assert r.status_code == 409, r.text
    j = r.json()
    assert j["detail"] == "version_conflict" and "updated_at" in j, j
    return j


def test_report_version_checks(monkeypatch):
    async def body(c, ids, state, user):
        url = f"/api/v1/cases/{ids['cases'][0]}/report"
        # never-saved row: base null passes (both NULL), a stale non-null base conflicts
        r = await c.put(url, json={"executive_summary": "v1", "base_updated_at": "2020-01-01T00:00:00Z"})
        _conflict(r)
        r = await c.put(url, json={"executive_summary": "v1", "base_updated_at": None})
        assert r.status_code == 200, r.text
        v1 = r.json()["report"]["updated_at"]
        assert v1
        await asyncio.sleep(1.1)
        # current base -> 200 and advances
        r = await c.put(url, json={"executive_summary": "v2", "base_updated_at": v1})
        assert r.status_code == 200, r.text
        v2 = r.json()["report"]["updated_at"]
        assert v2 > v1
        # stale base -> 409 carrying the current version; null base against a saved row also conflicts
        j = _conflict(await c.put(url, json={"executive_summary": "v3", "base_updated_at": v1}))
        assert j["updated_at"][:19] == v2[:19]
        _conflict(await c.put(url, json={"executive_summary": "v3", "base_updated_at": None}))
        assert (await c.get(url)).json()["report"]["executive_summary"] == "v2"
        # omitted -> no check
        r = await c.put(url, json={"executive_summary": "v4"})
        assert r.status_code == 200, r.text
    asyncio.run(_run(monkeypatch, body))


def test_timeline_note_version_checks(monkeypatch):
    async def body(c, ids, state, user):
        set_committed_value(user, "memberships", [])  # super admin: no lazy load needed
        cid = ids["cases"][0]
        async with AsyncSessionLocal() as db:
            ev = TimelineEvent(case_id=cid, user_id=user.id, event_type="comment", content="n0")
            db.add(ev)
            await db.commit()
            eid = int(ev.id)
        url = f"/api/v1/cases/{cid}/timeline/{eid}"
        # never-edited note: updated_at is NULL; null base passes, non-null base conflicts
        _conflict(await c.put(url, json={"content": "n1", "base_updated_at": "2020-01-01T00:00:00Z"}))
        r = await c.put(url, json={"content": "n1", "base_updated_at": None})
        assert r.status_code == 200, r.text
        v1 = r.json()["updated_at"]
        assert v1
        await asyncio.sleep(1.1)
        r = await c.put(url, json={"content": "n2", "base_updated_at": v1})
        assert r.status_code == 200, r.text
        v2 = r.json()["updated_at"]
        assert v2 > v1 and r.json()["content"] == "n2"
        j = _conflict(await c.put(url, json={"content": "n3", "base_updated_at": v1}))
        assert j["updated_at"][:19] == v2[:19]
        _conflict(await c.put(url, json={"content": "n3", "base_updated_at": None}))
        r = await c.put(url, json={"content": "n4"})
        assert r.status_code == 200 and r.json()["content"] == "n4", r.text
    asyncio.run(_run(monkeypatch, body))


def test_case_event_version_checks(monkeypatch):
    async def body(c, ids, state, user):
        cid = ids["cases"][0]
        base = f"/api/v1/cases/{cid}/events"
        r = await c.post(base, json={"event_date": "2026-10-01T09:00:00Z", "title": "e0"})
        assert r.status_code == 201, r.text
        eid = r.json()["id"]
        url = f"{base}/{eid}"
        # freshly created: updated_at NULL
        _conflict(await c.put(url, json={"title": "e1", "base_updated_at": "2020-01-01T00:00:00Z"}))
        r = await c.put(url, json={"title": "e1", "base_updated_at": None})
        assert r.status_code == 200, r.text
        v1 = r.json()["updated_at"]
        assert v1
        await asyncio.sleep(1.1)
        r = await c.put(url, json={"title": "e2", "base_updated_at": v1})
        assert r.status_code == 200, r.text
        v2 = r.json()["updated_at"]
        assert v2 > v1
        j = _conflict(await c.put(url, json={"title": "e3", "base_updated_at": v1}))
        assert j["updated_at"][:19] == v2[:19]
        _conflict(await c.put(url, json={"title": "e3", "base_updated_at": None}))
        r = await c.put(url, json={"title": "e4"})
        assert r.status_code == 200 and r.json()["title"] == "e4", r.text
    asyncio.run(_run(monkeypatch, body))
