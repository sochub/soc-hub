import pytest

from app.audit import middleware as mw
from app.audit.middleware import derive_entity
from tests.audit_fixtures import hdr, rows, run


def test_derive_entity():
    assert derive_entity("/api/v1/cases/{case_id}", {"case_id": "42"}) == ("case", 42)
    assert derive_entity("/api/v1/cases/{case_id}/attachments/{att_id}/download",
                         {"case_id": "1", "att_id": "7"}) == ("attachment", 7)
    assert derive_entity("/api/v1/users/{user_id}/mfa/reset", {"user_id": "3"}) == ("user", 3)
    assert derive_entity("/api/v1/cases/", {}) == (None, None)
    assert derive_entity("/api/v1/x/{slug}", {"slug": "abc"}) == (None, None)


def test_write_row_with_context():
    async def body(c, w):
        r = await c.put(f"/api/v1/cases/{w.case}", json={"title": "renamed"}, headers=hdr(w, "admin"))
        assert r.status_code == 200
        got = await rows(w, category="write")
        assert len(got) == 1
        row = got[0]
        assert row.action == "PUT /api/v1/cases/{case_id}" and row.route == "/api/v1/cases/{case_id}"
        assert (row.entity_type, row.entity_id) == ("case", w.case)
        assert row.tenant_id == w.ta and row.user_id == w.users["admin"] and row.user_email == w.emails["admin"]
        assert row.outcome == "success" and row.status_code == 200 and row.method == "PUT"
        assert row.request_id and len(row.request_id) == 36 and row.duration_ms is not None
        # the detailed change row from the same request shares the request_id
        change = await rows(w, category="change", request_id=row.request_id)
        assert change, "case update should also write a linked change row"
    run(body)


def test_denied_is_one_security_row():
    async def body(c, w):
        r = await c.put(f"/api/v1/cases/{w.case}", json={"title": "x"})  # no token
        assert r.status_code == 401
        got = await rows(w, route="/api/v1/cases/{case_id}")
        assert len(got) == 1 and got[0].category == "security" and got[0].action == "http.denied"
        assert got[0].outcome == "denied" and got[0].tenant_id is None
        await c.get("/api/v1/users/", headers=hdr(w, "analyst"))  # 403 on a GET is recorded too
        denied = await rows(w, action="http.denied", user_id=w.users["analyst"])
        assert len(denied) == 1 and denied[0].tenant_id == w.ta
    run(body)


def test_plain_get_and_excluded_paths_record_nothing():
    async def body(c, w):
        await c.get("/api/v1/cases/", headers=hdr(w, "admin"))
        await c.get("/health")
        await c.options("/api/v1/cases/")
        assert await rows(w) == []
    run(body)


def test_unmatched_routes():
    async def body(c, w):
        assert (await c.get("/api/v1/nope")).status_code == 404
        assert await rows(w) == []
        assert (await c.post("/api/v1/nope", headers=hdr(w, "admin"))).status_code in (401, 404, 405)
        got = await rows(w, method="POST")
        assert len(got) == 1 and got[0].route is None
    run(body)


def test_query_string_never_stored():
    async def body(c, w):
        await c.put(f"/api/v1/cases/{w.case}?token=CANARYQS", json={"title": "y"}, headers=hdr(w, "admin"))
        for r in await rows(w):
            assert "CANARYQS" not in repr(vars(r))
    run(body)


def test_super_admin_override_attributes_target_tenant():
    async def body(c, w):
        r = await c.put(f"/api/v1/cases/{w.case}?tenant_id={w.ta}", json={"title": "z"}, headers=hdr(w, "root"))
        assert r.status_code == 200
        got = await rows(w, category="write", user_id=w.users["root"])
        assert len(got) == 1 and got[0].tenant_id == w.ta
    run(body)


def test_insert_failure_is_swallowed(monkeypatch):
    async def boom(**kw):
        raise RuntimeError("db down")

    monkeypatch.setattr(mw, "_insert", boom)

    async def body(c, w):
        r = await c.put(f"/api/v1/cases/{w.case}", json={"title": "still ok"}, headers=hdr(w, "admin"))
        assert r.status_code == 200 and r.json()["title"] == "still ok"
    run(body)


def test_server_error_still_recorded(monkeypatch):
    from app.api.v1 import cases as cases_mod

    async def explode(*a, **k):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(cases_mod, "apply_case_update", explode)

    async def body(c, w):
        try:
            await c.put(f"/api/v1/cases/{w.case}", json={"title": "q"}, headers=hdr(w, "admin"))
        except RuntimeError:
            pass  # ASGITransport re-raises app exceptions
        got = await rows(w, category="write")
        assert len(got) == 1 and got[0].outcome == "error" and got[0].status_code == 500
    run(body)


def test_platform_route_is_tenantless():
    async def body(c, w):
        r = await c.put(f"/api/v1/tenants/{w.tb}", json={"name": "wf-test"}, headers=hdr(w, "root"))
        assert r.status_code == 200
        got = await rows(w, category="write", user_id=w.users["root"])
        assert len(got) == 1 and got[0].tenant_id is None
    run(body)


def test_effective_tenant_rule_is_order_independent():
    from app.audit.context import AuditContext, effective_tenant
    assert effective_tenant(AuditContext(tenant_id=5)) == 5  # provisional, tenant route
    assert effective_tenant(AuditContext(tenant_id=5, platform=True)) is None  # platform route, provisional ignored
    # require_super_admin + get_effective_tenant_id on one route: the explicit tenant wins, whichever ran first
    assert effective_tenant(AuditContext(tenant_id=7, tenant_explicit=True, platform=True)) == 7
    assert effective_tenant(AuditContext(tenant_id=None, tenant_explicit=True)) is None  # platform-scope view
    assert effective_tenant(AuditContext()) is None


def test_super_admin_member_platform_action_is_tenantless_and_hidden_from_tenant():
    from sqlalchemy import delete, select

    from app.core import security
    from app.db.session import AsyncSessionLocal
    from app.models.membership import TenantMembership
    from app.models.user import User

    async def body(c, w):
        async with AsyncSessionLocal() as db:
            m = TenantMembership(user_id=w.users["root"], tenant_id=w.ta, role="admin")
            db.add(m)
            await db.commit()
            mid = int(m.id)
        try:
            async with AsyncSessionLocal() as db:
                root = (await db.execute(select(User).where(User.id == w.users["root"]))).scalars().one()
                tok = security.issue_access_token(root, w.ta)
            r = await c.put(f"/api/v1/tenants/{w.tb}", json={"name": "wf-test"},
                            headers={"Authorization": f"Bearer {tok}"})
            assert r.status_code == 200
            got = await rows(w, category="write", user_id=w.users["root"])
            assert len(got) == 1 and got[0].tenant_id is None and got[0].route == "/api/v1/tenants/{tenant_id}"
            items = (await c.get("/api/v1/audit-logs?limit=200", headers=hdr(w, "admin"))).json()["items"]
            assert got[0].id not in {i["id"] for i in items}
            assert got[0].request_id not in {i["request_id"] for i in items}
        finally:
            async with AsyncSessionLocal() as db:
                await db.execute(delete(TenantMembership).where(TenantMembership.id == mid))
                await db.commit()
    run(body)


def _scope(**over):
    s = {"type": "http", "method": "POST", "path": "/x", "headers": [], "client": ("10.1.2.3", 1), "query_string": b""}
    s.update(over)
    return s


def test_audit_ip_validates_and_falls_back():
    from starlette.requests import Request
    assert mw.audit_ip(Request(_scope(headers=[(b"x-real-ip", b"203.0.113.9")]))) == "203.0.113.9"
    assert mw.audit_ip(Request(_scope(headers=[(b"x-real-ip", b"evil<script>")]))) == "10.1.2.3"
    assert mw.audit_ip(Request(_scope(headers=[(b"x-real-ip", b"nope")], client=None))) is None
    assert mw.audit_ip(Request(_scope(client=("testclient", 1)))) is None


def test_garbage_real_ip_header_is_not_stored():
    async def body(c, w):
        h = {**hdr(w, "admin"), "X-Real-IP": "<script>x</script>"}
        assert (await c.put(f"/api/v1/cases/{w.case}", json={"title": "ip"}, headers=h)).status_code == 200
        got = await rows(w, category="write")
        assert len(got) == 1 and got[0].ip == "127.0.0.1"
    run(body)


def test_route_template_fallback_warns_once(monkeypatch, caplog):
    import logging

    class R:
        path = "/{case_id}"

    monkeypatch.setattr(mw, "_route_fallback_warned", False)
    scope = {"route": R(), "fastapi": {"included_router": object()}}  # no include_context: private API drift
    with caplog.at_level(logging.WARNING, logger=mw.logger.name):
        assert mw._route_template(scope) == "/{case_id}"
        assert mw._route_template(scope) == "/{case_id}"
    assert len([r for r in caplog.records if r.levelno == logging.WARNING]) == 1


def test_queued_events_resolve_tenant_and_5xx_overrides_outcome(monkeypatch):
    import asyncio

    from app.audit.context import AuditContext

    written = []

    async def capture(**fields):
        written.append(fields)

    monkeypatch.setattr(mw, "insert_row", capture)
    ctx = AuditContext(tenant_id=5, platform=True)
    ctx.events = [{"action": "a", "outcome": "success", "tenant_id": None, "_tenant_from_ctx": True},
                  {"action": "b", "outcome": "denied", "tenant_id": 9}]
    asyncio.run(mw.AuditMiddleware(None)._record({"method": "POST"}, ctx, "rid", 503, 4))
    assert [(e["action"], e["tenant_id"], e["outcome"], e["status_code"]) for e in written] == [
        ("a", None, "error", 503), ("b", 9, "error", 503)]
    assert all("_tenant_from_ctx" not in e for e in written)
    written.clear()
    ctx = AuditContext(tenant_id=5)
    ctx.events = [{"action": "a", "outcome": "denied", "tenant_id": None, "_tenant_from_ctx": True}]
    asyncio.run(mw.AuditMiddleware(None)._record({"method": "POST"}, ctx, "rid", 400, 4))
    assert (written[0]["tenant_id"], written[0]["outcome"]) == (5, "denied")
