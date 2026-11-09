"""Soft edit lock on the incident report: claim, conflict, takeover, expiry, release."""
import asyncio
import secrets
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import delete, select, update
from sqlalchemy.orm.attributes import set_committed_value

from app.api import deps
from app.core.security import get_password_hash
from app.db.session import AsyncSessionLocal, engine
from app.main import app
from app.models.audit_log import AuditLog
from app.models.case import Case
from app.models.case_report import CaseReport
from app.models.tenant import Tenant
from app.models.user import User


async def _run(body):
    await engine.dispose()
    h = secrets.token_hex(3)
    ids = {"tenants": [], "users": [], "cases": []}
    async with AsyncSessionLocal() as db:
        try:
            t = Tenant(name="lock-test", slug=f"lock-{h}")
            users = [User(email=f"lock-{h}-{i}@example.test", hashed_password=get_password_hash(secrets.token_hex(8)),
                          is_active=True, is_super_admin=True) for i in (1, 2)]
            db.add_all([t, *users])
            await db.flush()
            ids["tenants"].append(int(t.id))
            ids["users"] += [int(u.id) for u in users]
            c = Case(title="lock-a", tenant_id=t.id)
            db.add(c)
            await db.flush()
            ids["cases"].append(int(c.id))
            await db.commit()
            for u in users:
                set_committed_value(u, "memberships", [])
            state = {"user": users[0]}
            app.dependency_overrides[deps.get_current_active_user] = lambda: state["user"]
            app.dependency_overrides[deps.get_effective_tenant_id] = lambda: ids["tenants"][0]
            app.dependency_overrides[deps.require_analyst_or_above] = lambda: state["user"]
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as client:
                await body(client, ids, state, users)
        finally:
            app.dependency_overrides.clear()
            async with AsyncSessionLocal() as db3:
                await db3.execute(delete(CaseReport).where(CaseReport.case_id.in_(ids["cases"])))
                await db3.execute(delete(AuditLog).where(AuditLog.tenant_id.in_(ids["tenants"])))
                await db3.execute(delete(Case).where(Case.id.in_(ids["cases"])))
                await db3.execute(delete(User).where(User.id.in_(ids["users"])))
                await db3.execute(delete(Tenant).where(Tenant.id.in_(ids["tenants"])))
                await db3.commit()


def test_claim_conflict_takeover_release():
    async def body(c, ids, state, users):
        a, b = users
        url = f"/api/v1/cases/{ids['cases'][0]}/report"
        r = await c.post(f"{url}/lock", json={})
        assert r.status_code == 200, r.text
        j = r.json()
        assert j["self"] is True and j["locked_by"] == a.id and j["locked_by_email"] == a.email and j["locked_at"]
        # re-claim by the holder is fine; GET shows the lock
        assert (await c.post(f"{url}/lock", json={})).status_code == 200
        assert (await c.get(url)).json()["report"]["locked_by_email"] == a.email

        state["user"] = b
        r = await c.post(f"{url}/lock", json={"takeover": False})
        assert r.status_code == 409, r.text
        j = r.json()
        assert j["self"] is False and j["locked_by"] == a.id and j["locked_by_email"] == a.email
        # a non-holder delete never releases the holder's lock
        assert (await c.delete(f"{url}/lock")).status_code == 204
        assert (await c.get(url)).json()["report"]["locked_by"] == a.id

        r = await c.post(f"{url}/lock", json={"takeover": True})
        assert r.status_code == 200 and r.json()["self"] is True and r.json()["locked_by"] == b.id
        async with AsyncSessionLocal() as db:
            rows = (await db.execute(select(AuditLog).where(
                AuditLog.tenant_id == ids["tenants"][0], AuditLog.action == "report.lock_takeover"))).scalars().all()
        assert len(rows) == 1 and rows[0].changes == {"from": a.id} and rows[0].user_id == b.id

        # release, then the other user can claim without takeover
        assert (await c.delete(f"{url}/lock")).status_code == 204
        assert (await c.get(url)).json()["report"]["locked_by"] is None
        state["user"] = a
        r = await c.post(f"{url}/lock", json={})
        assert r.status_code == 200 and r.json()["locked_by"] == a.id
    asyncio.run(_run(body))


def test_expired_lock_is_claimable_and_hidden():
    async def body(c, ids, state, users):
        a, b = users
        url = f"/api/v1/cases/{ids['cases'][0]}/report"
        assert (await c.post(f"{url}/lock", json={})).status_code == 200
        async with AsyncSessionLocal() as db:
            await db.execute(update(CaseReport).where(CaseReport.case_id == ids["cases"][0])
                             .values(locked_at=datetime.now(timezone.utc) - timedelta(minutes=3)))
            await db.commit()
        state["user"] = b
        rep = (await c.get(url)).json()["report"]
        assert rep["locked_by"] is None and rep["locked_at"] is None
        r = await c.post(f"{url}/lock", json={})
        assert r.status_code == 200 and r.json()["locked_by"] == b.id
        async with AsyncSessionLocal() as db:
            n = (await db.execute(select(AuditLog).where(
                AuditLog.tenant_id == ids["tenants"][0], AuditLog.action == "report.lock_takeover"))).scalars().all()
        assert n == []
    asyncio.run(_run(body))


def test_lock_cross_tenant_404():
    async def body(c, ids, state, users):
        ids_tenant = ids["tenants"][0]
        app.dependency_overrides[deps.get_effective_tenant_id] = lambda: ids_tenant + 10**6
        r = await c.post(f"/api/v1/cases/{ids['cases'][0]}/report/lock", json={})
        assert r.status_code == 404
        assert (await c.delete(f"/api/v1/cases/{ids['cases'][0]}/report/lock")).status_code == 404
    asyncio.run(_run(body))
