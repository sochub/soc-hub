"""Analyst workload stats: snapshot, handled-in-period via ownership history, resolve/close credited to the actor."""
import asyncio
import secrets
from datetime import datetime, timedelta, timezone

import httpx
from fastapi import HTTPException
from sqlalchemy import delete, select

import app.db.base  # noqa: F401  (register all models)
from app.api import deps
from app.core.security import get_password_hash
from app.db.session import AsyncSessionLocal, engine
from app.main import app
from app.models.audit_log import AuditLog
from app.models.case import Case, CaseStatus, TimelineEvent
from app.models.case_triage import CaseTriageResult
from app.models.membership import TenantMembership
from app.models.notification import CaseFollower, Notification
from app.models.tenant import Tenant
from app.models.user import User
from app.services.case_service import apply_case_update


def _deny():
    raise HTTPException(status_code=403, detail="Insufficient permissions")


async def _update(case_id, data, user_id, backdate=None):
    async with AsyncSessionLocal() as db:
        case = await db.get(Case, case_id)
        await apply_case_update(db, case=case, update_data=data, user_id=user_id)
        await db.commit()
        if backdate:  # move this update's audit rows out of the default period
            await db.execute(AuditLog.__table__.update().where(
                AuditLog.entity_id == case_id, AuditLog.entity_type == "case").values(created_at=backdate))
            await db.commit()


async def _scenario(monkeypatch, body):
    await engine.dispose()
    h = secrets.token_hex(3)
    ids = {"tenants": [], "users": []}
    state = {"role": "admin", "tenant": None}
    monkeypatch.setattr(deps, "_active_role", lambda u: state["role"])
    try:
        async with AsyncSessionLocal() as db:
            t, t2 = Tenant(name="aw", slug=f"aw-{h}"), Tenant(name="aw2", slug=f"aw2-{h}")
            users = [User(email=f"aw-{h}-{n}@example.test", hashed_password=get_password_hash(secrets.token_hex(8)),
                          is_active=True) for n in ("admin", "a", "b", "viewer")]
            db.add_all([t, t2, *users])
            await db.flush()
            ids["tenants"] += [t.id, t2.id]
            ids["users"] += [u.id for u in users]
            adm, a, b, v = users
            db.add_all([TenantMembership(user_id=adm.id, tenant_id=t.id, role="admin"),
                        TenantMembership(user_id=a.id, tenant_id=t.id, role="analyst"),
                        TenantMembership(user_id=b.id, tenant_id=t.id, role="analyst"),
                        TenantMembership(user_id=v.id, tenant_id=t.id, role="viewer"),
                        TenantMembership(user_id=a.id, tenant_id=t2.id, role="analyst")])
            old = datetime.now(timezone.utc) - timedelta(days=40)
            cases = {
                "handover": Case(title="handover", tenant_id=t.id, owner_id=a.id, status=CaseStatus.OPEN),
                "a_prog": Case(title="a-prog", tenant_id=t.id, owner_id=a.id, status=CaseStatus.IN_PROGRESS),
                "a_pend": Case(title="a-pend", tenant_id=t.id, owner_id=a.id, status=CaseStatus.PENDING),
                "b_new": Case(title="b-new", tenant_id=t.id, owner_id=b.id, status=CaseStatus.NEW),
                "a_old": Case(title="a-old", tenant_id=t.id, owner_id=a.id, status=CaseStatus.CLOSED,
                              created_at=old, resolved_at=old),
                "other": Case(title="other-tenant", tenant_id=t2.id, owner_id=a.id, status=CaseStatus.OPEN),
            }
            db.add_all(cases.values())
            await db.commit()
            ids["cases"] = {k: c.id for k, c in cases.items()}
            ids["case_ids"] = list(ids["cases"].values())
            ids["u"] = {"admin": adm.id, "a": a.id, "b": b.id, "viewer": v.id}
        state["tenant"] = ids["tenants"][0]
        app.dependency_overrides[deps.get_current_active_user] = lambda: None
        app.dependency_overrides[deps.get_effective_tenant_id] = lambda: state["tenant"]
        app.dependency_overrides[deps.require_admin] = lambda: None if state["role"] == "admin" else _deny()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as client:
            await body(client, state, ids)
    finally:
        app.dependency_overrides.clear()
        async with AsyncSessionLocal() as d:
            tids, cids = ids["tenants"], ids.get("case_ids", [])
            if tids:
                await d.execute(delete(Notification).where(Notification.tenant_id.in_(tids)))
                await d.execute(delete(CaseFollower).where(CaseFollower.tenant_id.in_(tids)))
                await d.execute(delete(AuditLog).where(AuditLog.tenant_id.in_(tids)))
                await d.execute(delete(CaseTriageResult).where(CaseTriageResult.tenant_id.in_(tids)))
            if cids:
                await d.execute(delete(TimelineEvent).where(TimelineEvent.case_id.in_(cids)))
                await d.execute(delete(Case).where(Case.id.in_(cids)))
            if tids:
                await d.execute(delete(TenantMembership).where(TenantMembership.tenant_id.in_(tids)))
            if ids["users"]:
                await d.execute(delete(User).where(User.id.in_(ids["users"])))
            if tids:
                await d.execute(delete(Tenant).where(Tenant.id.in_(tids)))
            await d.commit()
        await engine.dispose()


def test_workload_attribution_snapshot_and_isolation(monkeypatch):
    async def body(client, state, ids):
        c, u = ids["cases"], ids["u"]
        # A owned "handover", hands it to B; A resolves it, B closes it.
        await _update(c["handover"], {"owner_id": u["b"]}, u["admin"])
        await _update(c["handover"], {"status": CaseStatus.RESOLVED}, u["a"])
        await _update(c["handover"], {"status": CaseStatus.CLOSED, "outcome": "false_positive"}, u["b"])
        # The other tenant: A resolves a case there; must not leak into the active tenant.
        await _update(c["other"], {"status": CaseStatus.RESOLVED}, u["a"])
        # Old case: reassigned and closed 40 days ago -> outside this week.
        await _update(c["a_old"], {"owner_id": u["b"]}, u["a"], backdate=datetime.now(timezone.utc) - timedelta(days=40))

        r = await client.get("/api/v1/stats/analyst-workload")
        assert r.status_code == 200, r.text
        rows = {row["user_id"]: row for row in r.json()["analysts"]}
        assert u["viewer"] not in rows and u["admin"] in rows
        ra, rb = rows[u["a"]], rows[u["b"]]
        assert ra["current"] == {"new": 0, "open": 0, "in_progress": 1, "pending": 1, "total": 2}
        assert rb["current"] == {"new": 1, "open": 0, "in_progress": 0, "pending": 0, "total": 1}
        assert (ra["handled"], ra["resolved"], ra["closed"]) == (3, 1, 0)
        assert (rb["handled"], rb["resolved"], rb["closed"]) == (2, 0, 1)

        async def drill(user, metric):
            r = await client.get("/api/v1/stats/analyst-workload/cases",
                                 params={"user_id": u[user], "metric": metric})
            assert r.status_code == 200, r.text
            return {x["id"] for x in r.json()["cases"]}

        assert await drill("a", "handled") == {c["handover"], c["a_prog"], c["a_pend"]}
        assert await drill("b", "handled") == {c["handover"], c["b_new"]}
        assert await drill("a", "resolved") == {c["handover"]}
        assert await drill("b", "closed") == {c["handover"]}
        assert await drill("a", "current") == {c["a_prog"], c["a_pend"]}

        # Explicit range 40 days back covers the old reassignment: both owners handled the old case.
        day = (datetime.now(timezone.utc) - timedelta(days=40)).date().isoformat()
        r = await client.get("/api/v1/stats/analyst-workload/cases",
                             params={"user_id": u["a"], "metric": "handled", "start": day, "end": day})
        assert c["a_old"] in {x["id"] for x in r.json()["cases"]}

        # Other tenant view: only its own case is counted.
        state["tenant"] = ids["tenants"][1]
        r = await client.get("/api/v1/stats/analyst-workload")
        rows = {row["user_id"]: row for row in r.json()["analysts"]}
        assert set(rows) == {u["a"]}
        assert rows[u["a"]]["resolved"] == 1 and rows[u["a"]]["current"]["total"] == 0
        assert await drill("a", "resolved") == {c["other"]}

        assert (await client.get("/api/v1/stats/analyst-workload",
                                 params={"start": "2026-02-02", "end": "2026-02-01"})).status_code == 422

    asyncio.run(_scenario(monkeypatch, body))


def test_workload_requires_admin(monkeypatch):
    async def body(client, state, ids):
        for role in ("analyst", "viewer"):
            state["role"] = role
            assert (await client.get("/api/v1/stats/analyst-workload")).status_code == 403
            assert (await client.get("/api/v1/stats/analyst-workload/cases",
                                     params={"user_id": ids["u"]["a"], "metric": "handled"})).status_code == 403

    asyncio.run(_scenario(monkeypatch, body))
