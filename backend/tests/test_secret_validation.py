import asyncio
import secrets as pysecrets

import httpx
import pytest
from sqlalchemy import delete, select

from app.api import deps
from app.core.security import get_password_hash
from app.db.session import AsyncSessionLocal, engine
from app.main import app
from app.models.audit_log import AuditLog
from app.models.tenant import Tenant
from app.models.tenant_secret import TenantSecret
from app.models.user import User
from app.models.workflow import Workflow
from app.workflows.validation import validate_graph, workflow_errors

ONLY = "Secrets are only allowed in HTTP request URL, headers and body"
XFORM = "secrets can only be inserted, not transformed"


def n(id, type, **kw):
    return {"id": id, "type": type, "position": {"x": 0, "y": 0}, "config": kw.pop("config", {}), **kw}


def graph(node_type, config):
    return {"nodes": [n("start", "trigger"), n("x", node_type, config=config)],
            "edges": [{"id": "e", "source": "start", "target": "x", "source_handle": None}]}


def http(**cfg):
    return graph("http_request", {"method": "GET", "url": "https://a.example/x", **cfg})


def msgs(g, names=frozenset({"TOK"}), trigger="manual"):
    return [x["message"] for x in validate_graph(g, trigger, names)]


def test_valid_uses():
    assert msgs(http(headers={"Authorization": "Bearer {{ secrets.TOK }}"})) == []
    assert msgs(http(url="https://a.example/?k={{ secrets.TOK }}")) == []
    assert msgs(http(body={"a": {"b": ["{{ secrets.TOK }}"]}})) == []
    assert msgs(http(headers={"A": "{{ 'x' ~ secrets.TOK }}"})) == []  # concatenation allowed


def test_not_in_other_http_keys_or_nodes():
    assert ONLY in msgs(http(method="{{ secrets.TOK }}"))
    assert ONLY in msgs(graph("slack_ask_user", {"email": "a@b.c", "message": "{{ secrets.TOK }}"}))
    assert ONLY in msgs(graph("case_add_note", {"content": "{{ secrets.TOK }}"}))
    assert ONLY in msgs(graph("condition", {"expression": "secrets.TOK == 'x'"}))


def test_keys_rejected():
    assert ONLY in msgs(http(headers={"{{ secrets.TOK }}": "v"}))
    assert ONLY in msgs(http(body={"{{ secrets.TOK }}": "v"}))
    assert ONLY in msgs(http(body={"a": {"{{ secrets.TOK }}": "v"}}))


def test_names():
    assert "Unknown secret NOPE" in msgs(http(headers={"A": "{{ secrets.NOPE }}"}))
    assert "Invalid secret name lower" in msgs(http(headers={"A": "{{ secrets.lower }}"}))
    assert "Invalid secret name X" in msgs(http(headers={"A": "{{ secrets.X }}"}))
    # None skips the unknown check, not the invalid one
    assert msgs(http(headers={"A": "{{ secrets.NOPE }}"}), names=None) == []
    assert "Invalid secret name lower" in msgs(http(headers={"A": "{{ secrets.lower }}"}), names=None)


@pytest.mark.parametrize("expr", [
    "{{ secrets.TOK | upper }}", "{{ secrets.TOK[0] }}", "{{ secrets.TOK == 'a' }}", "{{ secrets.TOK != 'a' }}",
    "{{ secrets.TOK + 'a' }}", "{{ 'a' in secrets.TOK }}", "{{ secrets.TOK.upper() }}",
    "{{ secrets['TOK'] }}", "{{ secrets | list }}", "{{ secrets|attr('TOK') }}", "{{ secrets }}",
    "{% set x = secrets.TOK %}{{ x }}", "{% if secrets.TOK %}y{% endif %}",
])
def test_transforms_and_dynamic_rejected(expr):
    assert XFORM in msgs(http(headers={"A": expr}))


def test_dynamic_rejected_in_any_node():
    assert XFORM in msgs(graph("case_add_note", {"content": "{{ secrets['TOK'] }}"}))
    assert XFORM in msgs(graph("condition", {"expression": "secrets['TOK']"}))


def test_unrelated_text_and_fields_ok():
    assert msgs(graph("case_add_note", {"content": "rotate the secrets today; {{ case.secrets }}"})) == []


@pytest.mark.parametrize("prose", [
    "| secrets | value |", "rotate secrets [urgent]", "audit secrets.json", "see secrets.md", "secrets | list",
])
def test_prose_outside_spans_accepted(prose):
    assert msgs(graph("case_add_note", {"content": prose})) == []
    assert msgs(http(body={"text": prose})) == []


def test_dedupe_per_node():
    g = http(headers={"A": "{{ secrets.NOPE }}", "B": "{{ secrets.NOPE }}"}, body="{{ secrets.NOPE }}")
    assert msgs(g).count("Unknown secret NOPE") == 1


def test_trigger_filter():
    g = http()
    assert {"node_id": None, "message": ONLY} in [
        {"node_id": x["node_id"], "message": x["message"]} for x in workflow_errors(g, "manual", "secrets.TOK == 'a'", {"TOK"})]
    assert ONLY in [x["message"] for x in workflow_errors(g, "manual", "secrets['TOK']", {"TOK"})]
    assert workflow_errors(g, "manual", "case.id == 1", {"TOK"}) == []


async def _api_scenario():
    h = pysecrets.token_hex(3)
    ids = {"tenants": [], "users": [], "workflows": []}
    await engine.dispose()
    async with AsyncSessionLocal() as db:
        try:
            t = Tenant(name="wf-test", slug=f"wf-test-{h}-sv")
            t2 = Tenant(name="wf-test", slug=f"wf-test-{h}-sw")
            user = User(email=f"wf-sv-{h}@example.test", hashed_password=get_password_hash(pysecrets.token_hex(8)), is_active=True)
            db.add_all([t, t2, user])
            await db.flush()
            ids["tenants"] += [t2.id]
            ids["tenants"].append(t.id)
            ids["users"].append(user.id)
            db.add(TenantSecret(tenant_id=t.id, name="KNOWN", value_enc="x", allowed_hosts=[]))
            db.add(TenantSecret(tenant_id=t2.id, name="OTHER", value_enc="x", allowed_hosts=[]))
            await db.commit()
            app.dependency_overrides[deps.get_current_active_user] = lambda: user
            app.dependency_overrides[deps.require_admin] = lambda: user
            app.dependency_overrides[deps.get_effective_tenant_id] = lambda: t.id

            def body(name):
                g = http(headers={"A": "{{ secrets.%s }}" % name})
                return {"name": "t", "trigger_type": "manual", "trigger_filter": None, "graph": g}

            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
                r = await c.post("/api/v1/workflows/", json=body("KNOWN"))
                assert r.status_code == 201 and r.json()["validation_errors"] == [], r.text
                wid = r.json()["id"]
                ids["workflows"].append(wid)
                r = await c.post(f"/api/v1/workflows/{wid}/enable")
                assert r.status_code == 200, r.text
                r = await c.put(f"/api/v1/workflows/{wid}", json=body("NOPE"))
                assert r.status_code == 422, r.text
                assert "Unknown secret NOPE" in str(r.json()), r.text
                r = await c.post(f"/api/v1/workflows/{wid}/validate")
                assert r.json()["errors"] == []
                # tenant B's secret name is unknown to tenant A
                r = await c.post("/api/v1/workflows/", json=body("OTHER"))
                assert r.status_code == 201, r.text
                other = r.json()
                ids["workflows"].append(other["id"])
                assert [e["message"] for e in other["validation_errors"]] == ["Unknown secret OTHER"], other
                r = await c.post(f"/api/v1/workflows/{other['id']}/enable")
                assert r.status_code == 422 and "Unknown secret OTHER" in str(r.json()), r.text
        finally:
            app.dependency_overrides.clear()
            await db.rollback()
            if ids["workflows"]:
                await db.execute(delete(AuditLog).where(AuditLog.entity_type == "workflow", AuditLog.entity_id.in_(ids["workflows"])))
                await db.execute(delete(Workflow).where(Workflow.id.in_(ids["workflows"])))
            await db.execute(delete(TenantSecret).where(TenantSecret.tenant_id.in_(ids["tenants"])))
            await db.execute(delete(User).where(User.id.in_(ids["users"])))
            await db.execute(delete(Tenant).where(Tenant.id.in_(ids["tenants"])))
            await db.commit()
    await engine.dispose()


def test_api_unknown_secret_rejected():
    asyncio.run(_api_scenario())
