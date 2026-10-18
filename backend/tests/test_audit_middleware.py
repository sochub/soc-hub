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
