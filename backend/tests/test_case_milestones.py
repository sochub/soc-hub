"""Case-level incident milestones: declared overrides vs computed defaults, shared by the case API and the report."""
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete

from app.db.session import AsyncSessionLocal
from app.models.ioc import IOC
from app.models.membership import TenantMembership
from app.services.milestones import case_milestones
from tests.guest_fixtures import client, make_case, make_tenant, make_user, run
from tests.session_fixtures import make_web_token

UTC = timezone.utc


async def _member(db, ids, tenant, role="analyst"):
    u = await make_user(db, ids)
    db.add(TenantMembership(user_id=u.id, tenant_id=tenant.id, role=role))
    await db.commit()
    return u, {"Authorization": f"Bearer {await make_web_token(db, u, tenant.id)}"}


def test_declared_overrides_computed():
    async def body(ids):
        async with AsyncSessionLocal() as db:
            t = await make_tenant(db, ids)
            detected = datetime(2026, 10, 22, 14, 3, tzinfo=UTC)
            resolved = datetime(2026, 10, 23, 9, 0, tzinfo=UTC)
            c = await make_case(db, ids, t, detected_at=detected, resolved_at=resolved)
            ms = await case_milestones(db, c)
        assert ms["detected"] == {"at": detected, "source": "declared"}
        assert ms["recovered"] == {"at": resolved, "source": "computed"}
    run(body)


def test_unknown_when_nothing():
    async def body(ids):
        async with AsyncSessionLocal() as db:
            t = await make_tenant(db, ids)
            c = await make_case(db, ids, t)
            ms = await case_milestones(db, c)
        assert ms["first_seen"]["source"] == "unknown"
        assert ms["detected"]["source"] == "computed"
    run(body)


def test_computed_first_seen_from_ioc():
    async def body(ids):
        ioc_ids = []
        try:
            async with AsyncSessionLocal() as db:
                t = await make_tenant(db, ids)
                c = await make_case(db, ids, t)
                seen = datetime(2026, 10, 1, tzinfo=UTC)
                i = IOC(tenant_id=t.id, case_id=c.id, ioc_type="ip", value="203.0.113.9", first_seen=seen)
                db.add(i)
                await db.commit()
                ioc_ids.append(i.id)
                ms = await case_milestones(db, c)
            assert ms["first_seen"] == {"at": seen, "source": "computed"}
        finally:
            async with AsyncSessionLocal() as db:
                await db.execute(delete(IOC).where(IOC.id.in_(ioc_ids or [-1])))
                await db.commit()
    run(body)


def test_get_case_includes_milestones():
    async def body(ids):
        async with AsyncSessionLocal() as db:
            t = await make_tenant(db, ids)
            c = await make_case(db, ids, t)
            _, h = await _member(db, ids, t)
        async with client() as cl:
            r = await cl.get(f"/api/v1/cases/{c.id}", headers=h)
        assert r.status_code == 200, r.text
        ms = r.json()["milestones"]
        assert set(ms) >= {"first_seen", "detected", "contained", "recovered", "ttd_seconds", "ttc_seconds",
                           "ttr_seconds"}
        assert ms["detected"]["source"] == "computed"
    run(body)


def test_list_cases_has_no_milestones():
    async def body(ids):
        async with AsyncSessionLocal() as db:
            t = await make_tenant(db, ids)
            await make_case(db, ids, t)
            _, h = await _member(db, ids, t)
        async with client() as cl:
            r = await cl.get("/api/v1/cases/", headers=h)
        assert r.status_code == 200, r.text
        assert r.json() and all(c.get("milestones") is None for c in r.json())
    run(body)


def test_guest_case_has_no_milestones():
    async def body(ids):
        from tests.guest_fixtures import make_grant, session_headers
        async with AsyncSessionLocal() as db:
            t = await make_tenant(db, ids)
            c = await make_case(db, ids, t, contained_at=datetime(2026, 10, 22, tzinfo=UTC))
            e = ids.email("g")
            await make_grant(db, c, e)
        async with client() as cl:
            r = await cl.get(f"/api/v1/guest/cases/{c.id}", headers=session_headers(e, link_tenant_id=t.id))
        assert r.status_code == 200, r.text
        assert "milestones" not in r.json() and "contained_at" not in r.text
    run(body)


def _future(**kw):
    return (datetime.now(UTC) + timedelta(**kw)).isoformat()
