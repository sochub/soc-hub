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


async def _setup(ids, role="analyst", **case_kw):
    async with AsyncSessionLocal() as db:
        t = await make_tenant(db, ids)
        c = await make_case(db, ids, t, **case_kw)
        _, h = await _member(db, ids, t, role=role)
    return t, c, h


async def _case_row(cid):
    from app.models.case import Case
    async with AsyncSessionLocal() as db:
        return await db.get(Case, cid)


def test_put_declares_and_returns():
    async def body(ids):
        _, c, h = await _setup(ids)
        async with client() as cl:
            r = await cl.put(f"/api/v1/cases/{c.id}/milestones", headers=h,
                             json={"contained_at": "2099-10-22T18:40:00Z"})
        assert r.status_code == 200, r.text
        assert r.json()["contained"]["source"] == "declared"
        assert (await _case_row(c.id)).contained_at == datetime(2099, 10, 22, 18, 40, tzinfo=UTC)
    run(body)


def test_put_timezone_offset_stored_utc():
    async def body(ids):
        _, c, h = await _setup(ids)
        async with client() as cl:
            r = await cl.put(f"/api/v1/cases/{c.id}/milestones", headers=h,
                             json={"first_seen_at": "2020-10-20T09:00:00-05:00"})
        assert r.status_code == 200, r.text
        assert (await _case_row(c.id)).first_seen_at == datetime(2020, 10, 20, 14, 0, tzinfo=UTC)
    run(body)


def test_put_null_clears_to_computed():
    async def body(ids):
        _, c, h = await _setup(ids, detected_at=datetime(2020, 1, 1, tzinfo=UTC))
        async with client() as cl:
            r = await cl.put(f"/api/v1/cases/{c.id}/milestones", headers=h, json={"detected_at": None})
        assert r.status_code == 200, r.text
        assert r.json()["detected"]["source"] == "computed"
    run(body)


def test_put_partial_keeps_others():
    async def body(ids):
        first = datetime(2020, 1, 1, tzinfo=UTC)
        _, c, h = await _setup(ids, first_seen_at=first)
        async with client() as cl:
            r = await cl.put(f"/api/v1/cases/{c.id}/milestones", headers=h,
                             json={"contained_at": "2099-01-01T00:00:00Z"})
        assert r.status_code == 200, r.text
        assert r.json()["first_seen"] == {"at": "2020-01-01T00:00:00Z", "source": "declared"}
    run(body)


def test_put_inversion_introduced_422():
    async def body(ids):
        _, c, h = await _setup(ids)
        async with client() as cl:
            r = await cl.put(f"/api/v1/cases/{c.id}/milestones", headers=h,
                             json={"contained_at": "2000-01-01T00:00:00Z"})
        assert r.status_code == 422, r.text
        assert "Contained" in r.text and "Detected" in r.text
        assert (await _case_row(c.id)).contained_at is None
    run(body)


def test_preexisting_inversion_does_not_block():
    async def body(ids):
        ioc_ids = []
        try:
            t, c, h = await _setup(ids)
            async with AsyncSessionLocal() as db:  # computed first_seen later than case creation (detected)
                i = IOC(tenant_id=t.id, case_id=c.id, ioc_type="ip", value="203.0.113.7",
                        first_seen=datetime(2099, 1, 1, tzinfo=UTC))
                db.add(i)
                await db.commit()
                ioc_ids.append(i.id)
            async with client() as cl:
                r = await cl.put(f"/api/v1/cases/{c.id}/milestones", headers=h,
                                 json={"recovered_at": "2099-06-01T00:00:00Z"})
            assert r.status_code == 200, r.text
        finally:
            async with AsyncSessionLocal() as db:
                await db.execute(delete(IOC).where(IOC.id.in_(ioc_ids or [-1])))
                await db.commit()
    run(body)


def test_put_viewer_403():
    async def body(ids):
        _, c, h = await _setup(ids, role="viewer")
        async with client() as cl:
            r = await cl.put(f"/api/v1/cases/{c.id}/milestones", headers=h, json={"detected_at": None})
        assert r.status_code == 403
    run(body)


def test_put_cross_tenant_404():
    async def body(ids):
        _, _, h = await _setup(ids)
        async with AsyncSessionLocal() as db:
            other = await make_case(db, ids, await make_tenant(db, ids))
        async with client() as cl:
            r = await cl.put(f"/api/v1/cases/{other.id}/milestones", headers=h, json={"detected_at": None})
        assert r.status_code == 404
    run(body)


def test_put_audited():
    async def body(ids):
        from sqlalchemy import select
        from app.models.audit_log import AuditLog
        _, c, h = await _setup(ids)
        async with client() as cl:
            await cl.put(f"/api/v1/cases/{c.id}/milestones", headers=h, json={"recovered_at": "2099-01-01T00:00:00Z"})
        async with AsyncSessionLocal() as db:
            row = (await db.execute(select(AuditLog).where(AuditLog.entity_type == "case", AuditLog.entity_id == c.id,
                                                           AuditLog.action == "milestones_changed"))).scalars().first()
        assert row is not None and row.changes["new"]["recovered_at"].startswith("2099-01-01")
    run(body)


def test_create_case_with_first_seen(monkeypatch):
    import app.api.v1.cases as cases_mod
    monkeypatch.setattr(cases_mod.run_case_triage_task, "delay", lambda *a, **k: None)
    monkeypatch.setattr(cases_mod, "emit_event", lambda *a, **k: None)

    async def body(ids):
        async with AsyncSessionLocal() as db:
            t = await make_tenant(db, ids)
            _, h = await _member(db, ids, t)
        two_days_ago = (datetime.now(UTC) - timedelta(days=2)).isoformat()
        async with client() as cl:
            r = await cl.post("/api/v1/cases/", headers=h, json={"title": "ms-create", "first_seen_at": two_days_ago})
            assert r.status_code == 200, r.text
            ids.cases.append(r.json()["id"])
            g = await cl.get(f"/api/v1/cases/{r.json()['id']}", headers=h)
            assert g.json()["milestones"]["first_seen"]["source"] == "declared"
            bad = await cl.post("/api/v1/cases/", headers=h, json={"title": "ms-future", "first_seen_at": _future(hours=1)})
            assert bad.status_code == 422
    run(body)
