"""The two promises of the audit log: nothing confidential is stored; no write route is silently unaudited."""
import json
import re

from fastapi.routing import APIRoute

from app.audit.middleware import AUDIT_EXCLUDED_ROUTES, WRITE_METHODS
from app.main import app
from app.models.audit_log import AuditLog
from app.utils.audit import create_audit_log
from app.db.session import AsyncSessionLocal
from tests.audit_fixtures import PW, hdr, rows, run

CANARIES = ["CANARY-PW-7f3a", "CANARY-SECRET-91bd", "CANARY-COMMENT-c0de", "CANARY-QS-5e5e",
            "CANARY-HDR-a11a", "CANARY-NESTED-beef", "CANARY-422-dead", "CANARY-GPW-3b3b", "CANARY-GLINK-8d8d"]


def _dump(rs):
    return json.dumps([{c.name: getattr(r, c.name) for c in AuditLog.__table__.columns} for r in rs], default=str)


def test_no_confidential_value_reaches_any_audit_row():
    async def body(c, w):
        assert PW not in CANARIES
        w.secret_names.append("CANARYNAME")
        await c.post("/api/v1/auth/login/access-token", data={"username": w.emails["admin"], "password": CANARIES[0]})
        r = await c.post("/api/v1/secrets/", headers=hdr(w, "admin"),
                         json={"name": "CANARYNAME", "value": CANARIES[1], "allowed_hosts": ["api.example.com"]})
        assert r.status_code == 201, r.text
        r = await c.post(f"/api/v1/cases/{w.case}/timeline", headers=hdr(w, "admin"),
                         json={"event_type": "comment", "content": CANARIES[2]})
        assert r.status_code == 200, r.text
        r = await c.put(f"/api/v1/cases/{w.case}?token={CANARIES[3]}", headers=hdr(w, "admin"), json={"title": "x"})
        assert r.status_code == 200, r.text
        r = await c.put(f"/api/v1/cases/{w.case}", headers={**hdr(w, "admin"), "X-Api-Key": CANARIES[4]},
                        json={"title": "y"})
        assert r.status_code == 200, r.text
        r = await c.post("/api/v1/secrets/", headers=hdr(w, "admin"), json={"value": CANARIES[6]})  # 422: no name
        assert r.status_code == 422, r.text
        # guest sign-in: a password and a link token in the body (the /guest/link/<tok> path is an SPA route and
        # never reaches the backend, so it has no backend row to leak into)
        r = await c.post("/api/v1/guest/auth/login", json={"email": f"g@{w.h}.invalid", "password": CANARIES[7]})
        assert r.status_code == 401, r.text
        r = await c.post("/api/v1/guest/auth/link", json={"token": CANARIES[8]})
        assert r.status_code == 401, r.text
        async with AsyncSessionLocal() as db:
            await create_audit_log(db, "case", w.case, "update", w.ta,
                                   changes={"config": {"api_key": CANARIES[5]}, "title": "ok"})
            await db.commit()
        blob = _dump(await rows(w))
        for canary in CANARIES:
            assert canary not in blob, canary
        assert "ok" in blob  # sanity: scrubbed payloads still keep harmless fields
    run(body)


def _walk(routes, prefix=""):
    # FastAPI 0.142 keeps included routers as lazy `_IncludedRouter` branches, so `app.routes` holds no
    # APIRoute for /api/v1/*. Walk the branches and join the include prefixes: the same template the
    # middleware rebuilds per request in `_route_template`.
    for r in routes:
        if isinstance(r, APIRoute):
            for m in r.methods & WRITE_METHODS:
                yield f"{m} {prefix}{r.path}"
        elif hasattr(r, "original_router") and hasattr(r, "include_context"):
            yield from _walk(r.original_router.routes, prefix + r.include_context.prefix)


def _write_routes():
    routes = set(_walk(app.routes))
    assert len(routes) > 50, len(routes)  # a broken walk must not make the coverage test vacuous
    return routes


def test_route_enumeration_matches_openapi():
    # Guards the private-API walk above: every write operation in the schema must be found by it.
    schema = {f"{m.upper()} {p}" for p, ops in app.openapi()["paths"].items() for m in ops
              if m.upper() in WRITE_METHODS}
    assert schema and schema <= _write_routes(), schema - _write_routes()


def test_exclusion_allowlist_only_names_real_routes():
    real = _write_routes()
    assert set(AUDIT_EXCLUDED_ROUTES) <= real, set(AUDIT_EXCLUDED_ROUTES) - real
    for key, reason in AUDIT_EXCLUDED_ROUTES.items():
        assert isinstance(reason, str) and len(reason) > 10, key


def test_every_write_route_is_audited_unless_excluded():
    """Hit every non-excluded write route unauthenticated with no body: each must yield exactly one audit row,
    for THAT route template (http.denied for auth-protected routes, write for public ones). A route that yields
    nothing means the middleware isn't seeing it."""
    async def body(c, w):
        missing, wrong = [], []
        for key in sorted(_write_routes() - set(AUDIT_EXCLUDED_ROUTES)):
            method, path = key.split(" ", 1)
            url = re.sub(r"\{[^}]+\}", "999999999", path)  # every placeholder -> an id that doesn't exist
            before = {r.id for r in await rows(w)}
            try:
                await c.request(method, url)
            except Exception:
                pass  # ASGITransport re-raises app errors; the row must still exist
            new = [r for r in await rows(w) if r.id not in before]
            if not new:
                missing.append(key)
            elif len(new) != 1 or new[0].route != path:
                wrong.append((key, [(r.category, r.action, r.route) for r in new]))
        assert not missing, missing
        assert not wrong, wrong
    run(body)


def test_authenticated_write_yields_one_write_row_plus_its_change_rows():
    async def body(c, w):
        r = await c.put(f"/api/v1/cases/{w.case}", headers=hdr(w, "admin"), json={"title": "spot-check"})
        assert r.status_code == 200, r.text
        new = await rows(w)
        writes = [x for x in new if x.category == "write"]
        assert len(writes) == 1 and writes[0].action == "PUT /api/v1/cases/{case_id}", [(x.category, x.action) for x in new]
        rid = writes[0].request_id
        changes = [x for x in new if x.category == "change"]
        assert changes and all(x.request_id == rid for x in changes)
        assert len(new) == 1 + len(changes), [(x.category, x.action) for x in new]  # no http.denied or anything else
    run(body)


def test_workflow_run_audit_row_has_no_trigger_payload(monkeypatch):
    from app.tasks.workflows import advance_run_task

    monkeypatch.setattr(advance_run_task, "delay", lambda rid: None)
    graph = {"nodes": [{"id": "start", "type": "trigger", "config": {}},
                       {"id": "note", "type": "case_add_note", "config": {"content": "hi"}}],
             "edges": [{"id": "e1", "source": "start", "target": "note", "source_handle": None}]}

    async def body(c, w):
        r = await c.post("/api/v1/workflows/", headers=hdr(w, "admin"),
                         json={"name": "wf-canary", "trigger_type": "case.created", "graph": graph})
        assert r.status_code == 201, r.text
        d = await c.post(f"/api/v1/workflows/{r.json()['id']}/dry-run", headers=hdr(w, "admin"),
                         json={"case_id": w.case, "payload": {"event": "case.created", "raw": "CANARY-WF-123"}})
        assert d.status_code == 200, d.text
        all_rows = await rows(w)
        assert all_rows and not any("CANARY-WF-123" in repr(vars(x)) for x in all_rows)
        run_rows = await rows(w, entity_type="workflow_run", action="dry_run")
        assert len(run_rows) == 1
        assert run_rows[0].changes == {"workflow": "wf-canary", "event": "case.created", "case_id": w.case,
                                       "trigger_keys": ["event", "raw"]}
    run(body)


def test_tenant_created_index_exists():
    import asyncio

    from sqlalchemy import text

    from app.db.session import engine
    from app.models.audit_log import AuditLog

    assert "ix_audit_logs_tenant_created" in {i.name for i in AuditLog.__table__.indexes}

    async def go():
        await engine.dispose()
        async with engine.connect() as conn:
            d = (await conn.execute(text(
                "SELECT indexdef FROM pg_indexes WHERE indexname = 'ix_audit_logs_tenant_created'"))).scalar()
        await engine.dispose()
        return d
    assert "(tenant_id, created_at)" in (asyncio.run(go()) or "")
