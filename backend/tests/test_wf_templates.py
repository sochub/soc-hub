import asyncio
import secrets

import httpx
import pytest
from fastapi import HTTPException
from sqlalchemy import delete

from app.api import deps
from app.core.security import get_password_hash
from app.db.session import AsyncSessionLocal, engine
from app.main import app
from app.models.audit_log import AuditLog
from app.models.tenant import Tenant
from app.models.user import User
from app.models.workflow import Workflow
from app.workflows import templates as tpl
from app.workflows.validation import secret_names_in_graph, workflow_errors

FIXTURE = {
    "key": "fx-pull", "name": "Fixture", "vendor": "Fx", "kind": "ingest", "description": "d",
    "trigger_type": "schedule", "trigger_filter": "*/5 * * * *",
    "params": [{"key": "FX_DOMAIN", "label": "Domain", "placeholder": "a.b", "help": "h"}],
    "secrets": [{"name": "FX_TOKEN", "description": "t", "hosts": ["%%FX_DOMAIN%%"]}],
    "setup": "# do this",
    "graph": {"nodes": [{"id": "start", "type": "trigger", "config": {}},
                        {"id": "note", "type": "case_add_note", "config": {"content": "%%FX_DOMAIN%%"}}],
              "edges": [{"id": "e1", "source": "start", "target": "note", "source_handle": None}]},
}


def test_apply_params():
    out = tpl.apply_params(FIXTURE, {"FX_DOMAIN": "acme.example.com", "EXTRA": "x y"})
    assert out["graph"]["nodes"][1]["config"]["content"] == "acme.example.com"
    assert out["secrets"][0]["hosts"] == ["acme.example.com"]
    assert FIXTURE["graph"]["nodes"][1]["config"]["content"] == "%%FX_DOMAIN%%"  # deep copy
    with pytest.raises(ValueError, match="FX_DOMAIN"):
        tpl.apply_params(FIXTURE, {})
    for bad in ("a{{b", "x%%y", "a b", "a'b", ""):
        with pytest.raises(ValueError):
            tpl.apply_params(FIXTURE, {"FX_DOMAIN": bad})


def test_email_params():
    fx = {**FIXTURE, "params": [{"key": "ADMIN_EMAIL", "label": "Admin"}]}
    tpl.apply_params(fx, {"ADMIN_EMAIL": "admin@corp.example"})
    for bad in ("a{{b}}@x.com", "a b@x.com", "x@evil.com/../", "admin@corp.example\n"):
        with pytest.raises(ValueError, match="ADMIN_EMAIL"):
            tpl.apply_params(fx, {"ADMIN_EMAIL": bad})
    with pytest.raises(ValueError):  # non-_EMAIL params still refuse "@"
        tpl.apply_params(FIXTURE, {"FX_DOMAIN": "admin@corp.example"})


def test_shipped_templates_valid():
    tpl.reset_cache()
    loaded = tpl.load_templates()
    assert len({t["key"] for t in loaded}) == len(loaded)
    for t in loaded:
        params = {p["key"]: "12345" if p["key"].endswith("_ID") else "us-east-1" if p["key"].endswith("REGION")
                  else "admin@example.com" if p["key"].endswith("_EMAIL") else "example.com" for p in t["params"]}
        out = tpl.apply_params(t, params)
        assert workflow_errors(out["graph"], t["trigger_type"], out["trigger_filter"], None) == [], t["key"]
        assert secret_names_in_graph(out["graph"]) <= {s["name"] for s in t["secrets"]}, t["key"]


def _deny():
    raise HTTPException(status_code=403, detail="Insufficient permissions")


async def _scenario(monkeypatch):
    h = secrets.token_hex(3)
    ids = {"wf": []}
    state = {"role": "admin"}
    async with AsyncSessionLocal() as db:
        t = Tenant(name="wf-tpl", slug=f"wf-tpl-{h}")
        u = User(email=f"wf-tpl-{h}@example.test", hashed_password=get_password_hash(secrets.token_hex(8)), is_active=True)
        db.add_all([t, u])
        await db.commit()
        tid, uid = t.id, u.id
        try:
            app.dependency_overrides[deps.get_current_active_user] = lambda: u
            app.dependency_overrides[deps.get_effective_tenant_id] = lambda: tid
            app.dependency_overrides[deps.require_admin] = lambda: u if state["role"] == "admin" else _deny()
            monkeypatch.setattr(tpl, "load_templates", lambda: [FIXTURE])
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
                r = await c.get("/api/v1/workflows/templates")
                assert r.status_code == 200 and r.json()[0]["key"] == "fx-pull" and "graph" not in r.json()[0]
                url = "/api/v1/workflows/templates/fx-pull/install"
                r = await c.post(url, json={"params": {"FX_DOMAIN": "acme.example.com"}})
                assert r.status_code == 201, r.text
                d = r.json()
                ids["wf"].append(d["workflow"]["id"])
                assert d["workflow"]["enabled"] is False and d["workflow"]["name"] == "Fixture"
                assert d["workflow"]["graph"]["nodes"][1]["config"]["content"] == "acme.example.com"
                assert d["missing_secrets"] == ["FX_TOKEN"] and d["secret_hosts"] == {"FX_TOKEN": ["acme.example.com"]}
                r = await c.post(url, json={"params": {"FX_DOMAIN": "a{{b"}})
                assert r.status_code == 422
                r = await c.post("/api/v1/workflows/templates/nope/install", json={"params": {}})
                assert r.status_code == 404
                state["role"] = "analyst"
                assert (await c.post(url, json={"params": {"FX_DOMAIN": "x.com"}})).status_code == 403
        finally:
            app.dependency_overrides.clear()
            await db.rollback()
            await db.execute(delete(AuditLog).where(AuditLog.tenant_id == tid))
            await db.execute(delete(Workflow).where(Workflow.id.in_(ids["wf"])))
            await db.execute(delete(User).where(User.id == uid))
            await db.execute(delete(Tenant).where(Tenant.id == tid))
            await db.commit()
    await engine.dispose()


def test_install_api(monkeypatch):
    asyncio.run(_scenario(monkeypatch))
