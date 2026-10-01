import asyncio
import json
import logging
import re
import secrets

import httpx
from fastapi import HTTPException
from sqlalchemy import delete, func, select

from app.ai import aws
from app.ai import config as aicfg
from app.api import deps
from app.core.config import settings
from app.core.security import get_password_hash
from app.db.session import AsyncSessionLocal, engine
from app.main import app
from app.models.audit_log import AuditLog
from app.models.tenant import Tenant
from app.models.tenant_ai_config import TenantAIConfig
from app.models.user import User

KEY = "sk-abc12345678"
URL = "/api/v1/ai/config"
LOG_RX = re.compile(r"^ai_config (save|test|delete) tenant=\d+ provider=\S+ outcome=[a-z_]+$")


def _deny():
    raise HTTPException(status_code=403, detail="Insufficient permissions")


async def _scenario(monkeypatch, caplog, tested):
    h = secrets.token_hex(3)
    ids = {"tenants": [], "users": []}
    state = {"tenant": None, "role": "admin", "user": None}
    async with AsyncSessionLocal() as db:
        try:
            t = Tenant(name="wf-test", slug=f"wf-test-{h}-ai")
            user = User(email=f"ai-api-{h}@example.test", hashed_password=get_password_hash(secrets.token_hex(8)),
                        is_active=True)
            db.add_all([t, user])
            await db.flush()
            ids["tenants"].append(t.id)
            ids["users"].append(user.id)
            await db.commit()
            tid = t.id
            state["tenant"], state["user"] = tid, user

            app.dependency_overrides[deps.get_current_active_user] = lambda: state["user"]
            app.dependency_overrides[deps.get_effective_tenant_id] = lambda: state["tenant"]
            app.dependency_overrides[deps.require_admin] = lambda: state["user"] if state["role"] == "admin" else _deny()

            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as client:
                def ai_lines():
                    return [rec.getMessage() for rec in caplog.records if rec.name == "app.api.v1.ai_config"]

                async def call(method, url, op=None, outcome=None, **kw):
                    # m) exactly one well-formed ai_config line per save/test/delete; none for reads/denials
                    n0 = len(ai_lines())
                    r = await client.request(method, url, **kw)
                    new = ai_lines()[n0:]
                    if op is None:
                        assert new == [], f"m unexpected log {new}"
                    else:
                        assert len(new) == 1 and LOG_RX.match(new[0]), f"m {op} {new}"
                        assert new[0].startswith(f"ai_config {op} tenant={tid} "), f"m {op} {new}"
                        if outcome:
                            assert new[0].endswith(f"outcome={outcome}"), f"m outcome {new}"
                    return r

                # a) no row -> deployment defaults, nothing set
                r = await call("GET", URL)
                assert r.status_code == 200, f"a {r.status_code}"
                d = r.json()
                assert d["source"] == "deployment" and d["override_allowed"] is True, "a source"
                assert d["secrets_set"] and not any(d["secrets_set"].values()), "a secrets_set"
                assert d["deployment_principal_arn"] == "arn:aws:iam::123456789012:role/test", "a principal"

                # a2) /test with no row and no body never tests the deployment provider
                r = await call("POST", URL + "/test", "test", "no_config")
                assert r.json() == {"ok": False, "message": "No tenant AI configuration to test"}, f"a2 {r.text}"
                assert tested == [], "a2 deployment tested"

                # b) PUT openai with a key -> tenant source; key never echoed; api_base ignored for openai
                r = await call("PUT", URL, "save", "created",
                               json={"provider": "openai", "model": "gpt-4o-mini", "api_key": KEY, "api_base": "http://x"})
                assert r.status_code == 200, f"b put {r.status_code} {r.text}"
                assert KEY not in r.text, "b put echoes secret"
                assert r.json()["api_base"] is None, "b api_base stored for openai"
                r = await call("GET", URL)
                d = r.json()
                assert d["source"] == "tenant" and d["provider"] == "openai", "b source"
                assert d["secrets_set"]["api_key"] is True, "b secrets_set"
                assert KEY not in r.text, "b get echoes secret"

                # c) omitted and blank api_key keep the stored key
                r = await call("PUT", URL, "save", "updated", json={"provider": "openai", "model": "gpt-4o"})
                assert r.status_code == 200 and r.json()["secrets_set"]["api_key"] is True, "c omitted"
                r = await call("PUT", URL, "save", "updated", json={"provider": "openai", "model": "gpt-4o", "api_key": "   "})
                assert r.status_code == 200 and r.json()["secrets_set"]["api_key"] is True, "c blank"
                async with AsyncSessionLocal() as db2:
                    row = (await db2.execute(select(TenantAIConfig).where(TenantAIConfig.tenant_id == tid))).scalars().one()
                    assert aicfg.decrypt_secrets(row.credentials_enc)["api_key"] == KEY, "c stored key"
                    assert KEY not in (row.credentials_enc or ""), "c stored encrypted"

                # d) provider change discards old secrets: anthropic without a key is incomplete -> 422,
                #    and the OpenAI key is never reused for Anthropic
                r = await call("PUT", URL, "save", "rejected", json={"provider": "anthropic", "model": "claude"})
                assert r.status_code == 422 and "api_key" in r.json()["detail"], f"d {r.status_code} {r.text}"
                r = await call("PUT", URL, "save", "updated",
                               json={"provider": "gemini", "model": "gemini-pro", "api_key": "AIza-other"})
                assert r.status_code == 200, "d gemini"
                async with AsyncSessionLocal() as db2:
                    row = (await db2.execute(select(TenantAIConfig).where(TenantAIConfig.tenant_id == tid))).scalars().one()
                    assert aicfg.decrypt_secrets(row.credentials_enc) == {"api_key": "AIza-other"}, "d discarded"

                # e) invalid vertex service-account JSON -> 422
                r = await call("PUT", URL, "save", "rejected", json={"provider": "vertex", "model": "gemini-1.5", "project": "p",
                                                                     "location": "us-central1", "service_account_json": "{bad"})
                assert r.status_code == 422, f"e {r.status_code}"

                # f) internal api_base needs the allowlist (400 per controller ruling) on PUT and /test
                body_f = {"provider": "ollama", "model": "llama3", "api_base": "http://127.0.0.1:11434"}
                r = await call("PUT", URL, "save", "rejected", json=body_f)
                assert r.status_code == 400 and "allowlist" in r.json()["detail"], f"f {r.status_code} {r.text}"
                r = await call("POST", URL + "/test", "test", "rejected", json=body_f)
                assert r.status_code == 400 and "allowlist" in r.json()["detail"], f"f test {r.status_code} {r.text}"
                assert tested == [], "f rejected body was tested"
                r = await client.put("/api/v1/workflows/settings/http-allowlist", json={"hosts": ["127.0.0.1"]})
                assert r.status_code == 200, "f allowlist"
                r = await call("PUT", URL, "save", "updated", json=body_f)
                assert r.status_code == 200 and r.json()["api_base"] == body_f["api_base"], f"f retry {r.text}"
                # switching to ollama (no secrets) leaves nothing set
                assert not any(r.json()["secrets_set"].values()), "f ollama secrets_set"

                # f2) /test with a submitted body tests that candidate (not the stored row)
                r = await call("POST", URL + "/test", "test", "ok", json={**body_f, "model": "llama3.1"})
                assert r.status_code == 200 and r.json()["ok"] is True, f"f2 {r.text}"
                assert (tested[-1].provider, tested[-1].model, tested[-1].source) == ("ollama", "llama3.1", "tenant"), "f2 cfg"

                # g0) bedrock keys -> role drops the stored access keys
                bed = {"provider": "bedrock", "model": "anthropic.claude-3", "region": "us-east-1"}
                r = await call("PUT", URL, "save", "updated",
                               json={**bed, "auth_mode": "keys", "access_key_id": "AKIAEXAMPLE", "secret_access_key": "s3cr3t"})
                assert r.status_code == 200 and r.json()["secrets_set"]["access_key_id"] is True, f"g0 keys {r.text}"

                # g) bedrock role without role_arn -> saved disabled with an external_id; adding role_arn enables
                r = await call("PUT", URL, "save", "updated", json={**bed, "auth_mode": "role"})
                assert r.status_code == 200, f"g {r.status_code} {r.text}"
                d = r.json()
                assert not any(d["secrets_set"].values()), "g0 access keys kept after switch to role"
                async with AsyncSessionLocal() as db2:
                    row = (await db2.execute(select(TenantAIConfig).where(TenantAIConfig.tenant_id == tid))).scalars().one()
                    assert row.credentials_enc is None, "g0 stored keys"
                assert d["enabled"] is False and d["external_id"], "g disabled"
                ext = d["external_id"]
                assert (await call("GET", URL)).json()["external_id"] == ext, "g stable external_id"

                # g2) /test with no body tests the disabled row, never the deployment provider
                r = await call("POST", URL + "/test", "test", "ok")
                assert r.status_code == 200 and r.json()["ok"] is True, f"g2 {r.text}"
                assert (tested[-1].source, tested[-1].provider) == ("tenant", "bedrock"), "g2 cfg"

                r = await call("PUT", URL, "save", "updated",
                               json={**bed, "auth_mode": "role", "role_arn": "arn:aws:iam::111122223333:role/soc"})
                assert r.status_code == 200 and r.json()["enabled"] is True and r.json()["external_id"] == ext, "g enable"

                # h) test endpoint (stubbed test_connection) on the enabled row
                r = await call("POST", URL + "/test", "test", "ok")
                assert (r.status_code, r.json()) == (200, {"ok": True, "message": "ok"}), f"h {r.text}"

                # i) DELETE -> back to deployment; /test now has nothing to test
                r = await call("DELETE", URL, "delete", "deleted")
                assert r.status_code == 204, "i delete"
                assert (await call("GET", URL)).json()["source"] == "deployment", "i source"
                n = len(tested)
                r = await call("POST", URL + "/test", "test", "no_config")
                assert r.json()["ok"] is False and len(tested) == n, "i test after delete"

                # j) override disabled by deployment: PUT and /test-with-body are forbidden (and logged)
                monkeypatch.setattr(settings, "AI_ALLOW_TENANT_OVERRIDE", False)
                r = await call("PUT", URL, "save", "forbidden", json={"provider": "openai", "model": "gpt-4o", "api_key": KEY})
                assert r.status_code == 403, "j put"
                r = await call("POST", URL + "/test", "test", "forbidden", json={"provider": "openai", "model": "gpt-4o", "api_key": KEY})
                assert r.status_code == 403, "j test"
                assert (await call("GET", URL)).json()["override_allowed"] is False, "j get"
                monkeypatch.setattr(settings, "AI_ALLOW_TENANT_OVERRIDE", True)

                # k) viewer: config forbidden, info allowed
                state["role"] = "viewer"
                r = await call("PUT", URL, json={"provider": "openai", "model": "x", "api_key": KEY})
                assert r.status_code == 403, "k put"
                assert (await call("GET", URL)).status_code == 403, "k get"
                r = await call("GET", "/api/v1/ai/info")
                assert r.status_code == 200 and set(r.json()) == {"provider", "model", "source"}, f"k info {r.text}"
                state["role"] = "admin"

            # l) audit rows exist, the create lists every field it set, and none hold secret values
            rows = (await db.execute(select(AuditLog).where(AuditLog.entity_type == "ai_config",
                                                            AuditLog.tenant_id == tid))).scalars().all()
            assert rows and {"create", "update", "delete"} <= {x.action for x in rows}, "l actions"
            create = next(x for x in rows if x.action == "create")
            assert set(create.changes["fields"]) == {"provider", "model", "enabled"}, f"l create {create.changes}"
            assert create.changes["secrets_updated"] == ["api_key"], "l create secrets_updated"
            for row in rows:
                dumped = json.dumps(row.changes)
                assert KEY not in dumped and "AIza-other" not in dumped and "s3cr3t" not in dumped, "l secret in audit"

            # m) no secrets in any ai_config log line
            assert all(KEY not in m and "AIza-other" not in m and "s3cr3t" not in m for m in ai_lines()), "m secret in log"
        finally:
            app.dependency_overrides.clear()
            aicfg.invalidate_all()
            await db.rollback()
            tids = ids["tenants"]
            await db.execute(delete(AuditLog).where(AuditLog.tenant_id.in_(tids)))
            await db.execute(delete(TenantAIConfig).where(TenantAIConfig.tenant_id.in_(tids)))
            await db.execute(delete(User).where(User.id.in_(ids["users"])))
            await db.execute(delete(Tenant).where(Tenant.id.in_(tids)))
            await db.commit()
            left = (await db.execute(select(func.count()).select_from(Tenant).where(Tenant.slug.like("wf-test-%")))).scalar()
            assert left == 0
    await engine.dispose()


def test_ai_config_api(monkeypatch, caplog):
    import app.api.v1.ai_config as api_mod

    tested = []

    async def fake_test_connection(cfg, *, allowlist=()):
        tested.append(cfg)
        return True, "ok"

    async def fake_principal(**_):
        return "arn:aws:iam::123456789012:role/test"

    monkeypatch.setattr(api_mod, "test_connection", fake_test_connection)
    monkeypatch.setattr(aws, "deployment_principal_arn", fake_principal)
    caplog.set_level(logging.INFO, logger="app.api.v1.ai_config")
    aicfg.invalidate_all()
    asyncio.run(_scenario(monkeypatch, caplog, tested))
