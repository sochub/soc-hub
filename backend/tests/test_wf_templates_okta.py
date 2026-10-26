"""Okta templates: API Services app (private-key JWT + DPoP), no SSWS tokens."""
import asyncio
import json
import secrets

import httpx
from sqlalchemy import delete

from app.api import deps
from app.core.security import get_password_hash
from app.db.session import AsyncSessionLocal, engine
from app.main import app
from app.models.artifact import Artifact, ArtifactType
from app.models.audit_log import AuditLog
from app.models.case import Case
from app.models.case_artifact import CaseArtifact
from app.models.tenant import Tenant
from app.models.tenant_secret import TenantSecret
from app.models.user import User
from app.models.workflow import Workflow
from app.utils.crypto import encrypt
from app.workflows import templates as tpl
from tests.test_wf_templates_identity import _drain, _steps

OKTA = "acme.okta.com"
SCOPES = {"okta-system-log-pull": "okta.logs.read", "okta-contain-user": "okta.users.manage",
          "okta-suspend-user": "okta.users.manage", "okta-reset-authenticators": "okta.users.manage"}
SECRET = {"okta-system-log-pull": "OKTA_READ_PRIVATE_KEY"}


def test_okta_templates_use_api_services():
    for key, scope in SCOPES.items():
        t = tpl.get_template(key)
        assert [p["key"] for p in t["params"]] == ["OKTA_DOMAIN", "OKTA_CLIENT_ID"], key
        secret = SECRET.get(key, "OKTA_RESPONSE_PRIVATE_KEY")
        assert [s["name"] for s in t["secrets"]] == [secret] and t["secrets"][0]["hosts"] == ["%%OKTA_DOMAIN%%"]
        https = [n for n in t["graph"]["nodes"] if n["type"] == "http_request"]
        assert https, key
        for n in https:
            a = n["config"]["auth"]
            assert a == {"type": "oauth2_private_key_jwt", "token_url": "https://%%OKTA_DOMAIN%%/oauth2/v1/token",
                         "client_id": "%%OKTA_CLIENT_ID%%", "private_key": "{{ secrets.%s }}" % secret,
                         "scope": scope, "dpop": True}, (key, n["id"])
            assert "Authorization" not in n["config"].get("headers", {})
        assert "Okta side" in t["setup"] and "API Services" in t["setup"] and "DPoP" in t["setup"]


def test_no_ssws_left():
    for t in tpl.load_templates():
        text = json.dumps(t)
        for bad in ("OKTA_API_TOKEN", "OKTA_RESPONSE_API_TOKEN", "SSWS"):
            assert bad not in text, (t["key"], bad)


async def _scenario(queue, key, urls_expected):
    h = secrets.token_hex(3)
    ids = {"wf": [], "case": None, "art": None, "sec": []}
    async with AsyncSessionLocal() as db:
        t = Tenant(name="wf-okta", slug=f"wf-okta-{h}")
        u = User(email=f"wf-okta-{h}@example.test", hashed_password=get_password_hash(secrets.token_hex(8)), is_active=True)
        db.add_all([t, u])
        await db.flush()
        tid, uid = t.id, u.id
        try:
            s = TenantSecret(tenant_id=tid, name="OKTA_RESPONSE_PRIVATE_KEY", value_enc=encrypt("x-" + h), allowed_hosts=[OKTA])
            db.add(s)
            case = Case(title="Compromised account", tenant_id=tid)
            db.add(case)
            art = Artifact(tenant_id=tid, artifact_type=ArtifactType.EMAIL, value="olack@adatum.com")
            db.add(art)
            await db.flush()
            ids["sec"].append(s.id)
            ids["case"], ids["art"] = case.id, art.id
            db.add(CaseArtifact(case_id=case.id, artifact_id=art.id))
            await db.commit()
            app.dependency_overrides[deps.get_current_active_user] = lambda: u
            app.dependency_overrides[deps.get_effective_tenant_id] = lambda: tid
            app.dependency_overrides[deps.require_admin] = lambda: u
            tpl.reset_cache()
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
                r = await c.post(f"/api/v1/workflows/templates/{key}/install",
                                 json={"params": {"OKTA_DOMAIN": OKTA, "OKTA_CLIENT_ID": "0oaTEST"}})
                assert r.status_code == 201, r.text
                d = r.json()
                ids["wf"].append(d["workflow"]["id"])
                assert d["workflow"]["validation_errors"] in (None, []), d["workflow"]
                assert d["missing_secrets"] == []
                lookup = {"status": 200, "headers": {}, "body": {"id": "00u1abcd", "status": "ACTIVE"}}
                r = await c.post(f"/api/v1/workflows/{d['workflow']['id']}/dry-run",
                                 json={"case_id": ids["case"], "mocks": {"lookup": lookup}})
                assert r.status_code == 200, r.text
                await _drain(queue)
                steps = await _steps(r.json()["run_id"])
                for node, (method, url) in urls_expected.items():
                    wd = steps[node][0][1]["would_do"]
                    assert (wd["method"], wd["url"]) == (method, f"https://{OKTA}{url}"), wd
                    assert wd["auth"]["type"] == "oauth2_private_key_jwt" and wd["auth"]["dpop"] is True
                    assert "⟦secret:OKTA_RESPONSE_PRIVATE_KEY#" in wd["auth"]["private_key"]
                    assert "Authorization" not in wd.get("headers", {})
                return steps
        finally:
            app.dependency_overrides.clear()
            tpl.reset_cache()
            await db.rollback()
            await db.execute(delete(Workflow).where(Workflow.id.in_(ids["wf"])))
            await db.execute(delete(AuditLog).where(AuditLog.tenant_id == tid))
            if ids["case"]:
                await db.execute(delete(CaseArtifact).where(CaseArtifact.case_id == ids["case"]))
                await db.execute(delete(Case).where(Case.id == ids["case"]))
            if ids["art"]:
                await db.execute(delete(Artifact).where(Artifact.id == ids["art"]))
            await db.execute(delete(TenantSecret).where(TenantSecret.id.in_(ids["sec"])))
            await db.execute(delete(User).where(User.id == uid))
            await db.execute(delete(Tenant).where(Tenant.id == tid))
            await db.commit()


def _run(monkeypatch, key, urls, check):
    import app.tasks.workflows as tw
    queue = []
    monkeypatch.setattr(tw.advance_run_task, "delay", lambda rid: queue.append(("advance", rid)))
    monkeypatch.setattr(tw.execute_step_task, "delay", lambda sid: queue.append(("step", sid)))
    monkeypatch.setattr(tw.loop_tick_task, "delay", lambda sid: queue.append(("tick", sid)))
    try:
        steps = asyncio.run(_scenario(queue, key, urls))
    finally:
        asyncio.run(engine.dispose())
    check(steps)


def test_okta_reset_dry_run(monkeypatch):
    def check(steps):
        note = steps["result"][0][1]["would_do"]["content"]
        assert "reset_factors HTTP 200" in note and "re-enroll" in note and "sessions are not revoked" in note
    _run(monkeypatch, "okta-reset-authenticators",
         {"lookup": ("GET", "/api/v1/users/olack%40adatum.com"),
          "reset": ("POST", "/api/v1/users/00u1abcd/lifecycle/reset_factors")}, check)


def test_okta_suspend_dry_run(monkeypatch):
    def check(steps):
        note = steps["result"][0][1]["would_do"]["content"]
        assert "suspend HTTP 200" in note and "unsuspend" in note
    _run(monkeypatch, "okta-suspend-user",
         {"suspend": ("POST", "/api/v1/users/00u1abcd/lifecycle/suspend")}, check)
