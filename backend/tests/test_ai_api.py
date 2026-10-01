import asyncio
import json
import logging
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


def _deny():
    raise HTTPException(status_code=403, detail="Insufficient permissions")


async def _scenario(monkeypatch, caplog):
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

            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
                # a) no row -> deployment defaults, nothing set
                r = await c.get(URL)
                assert r.status_code == 200, f"a {r.status_code}"
                d = r.json()
                assert d["source"] == "deployment" and d["override_allowed"] is True, "a source"
                assert d["secrets_set"] and not any(d["secrets_set"].values()), "a secrets_set"
                assert d["deployment_principal_arn"] == "arn:aws:iam::123456789012:role/test", "a principal"

                # b) PUT openai with a key -> tenant source; key never echoed
                r = await c.put(URL, json={"provider": "openai", "model": "gpt-4o-mini", "api_key": KEY})
                assert r.status_code == 200, f"b put {r.status_code} {r.text}"
                assert KEY not in r.text, "b put echoes secret"
                r = await c.get(URL)
                d = r.json()
                assert d["source"] == "tenant" and d["provider"] == "openai", "b source"
                assert d["secrets_set"]["api_key"] is True, "b secrets_set"
                assert KEY not in r.text, "b get echoes secret"

                # c) omitted and blank api_key keep the stored key
                r = await c.put(URL, json={"provider": "openai", "model": "gpt-4o"})
                assert r.status_code == 200 and r.json()["secrets_set"]["api_key"] is True, "c omitted"
                r = await c.put(URL, json={"provider": "openai", "model": "gpt-4o", "api_key": "   "})
                assert r.status_code == 200 and r.json()["secrets_set"]["api_key"] is True, "c blank"
                async with AsyncSessionLocal() as db2:
                    row = (await db2.execute(select(TenantAIConfig).where(TenantAIConfig.tenant_id == tid))).scalars().one()
                    assert aicfg.decrypt_secrets(row.credentials_enc)["api_key"] == KEY, "c stored key"
                    assert KEY not in (row.credentials_enc or ""), "c stored encrypted"

                # d) provider change discards old secrets: anthropic without a key is incomplete -> 422,
                #    and the OpenAI key is never reused for Anthropic
                r = await c.put(URL, json={"provider": "anthropic", "model": "claude"})
                assert r.status_code == 422 and "api_key" in r.json()["detail"], f"d {r.status_code} {r.text}"
                r = await c.put(URL, json={"provider": "gemini", "model": "gemini-pro", "api_key": "AIza-other"})
                assert r.status_code == 200, "d gemini"
                async with AsyncSessionLocal() as db2:
                    row = (await db2.execute(select(TenantAIConfig).where(TenantAIConfig.tenant_id == tid))).scalars().one()
                    assert aicfg.decrypt_secrets(row.credentials_enc) == {"api_key": "AIza-other"}, "d discarded"

                # e) invalid vertex service-account JSON -> 422
                r = await c.put(URL, json={"provider": "vertex", "model": "gemini-1.5", "project": "p",
                                           "location": "us-central1", "service_account_json": "{bad"})
                assert r.status_code == 422, f"e {r.status_code}"

                # f) internal api_base needs the allowlist (400 per controller ruling), then passes
                body_f = {"provider": "ollama", "model": "llama3", "api_base": "http://127.0.0.1:11434"}
                r = await c.put(URL, json=body_f)
                assert r.status_code == 400 and "allowlist" in r.json()["detail"], f"f {r.status_code} {r.text}"
                r = await c.put("/api/v1/workflows/settings/http-allowlist", json={"hosts": ["127.0.0.1"]})
                assert r.status_code == 200, "f allowlist"
                r = await c.put(URL, json=body_f)
                assert r.status_code == 200 and r.json()["api_base"] == body_f["api_base"], f"f retry {r.text}"

                # g) bedrock role without role_arn -> saved disabled with an external_id; adding role_arn enables
                bed = {"provider": "bedrock", "model": "anthropic.claude-3", "region": "us-east-1", "auth_mode": "role"}
                r = await c.put(URL, json=bed)
                assert r.status_code == 200, f"g {r.status_code} {r.text}"
                d = r.json()
                assert d["enabled"] is False and d["external_id"], "g disabled"
                ext = d["external_id"]
                assert (await c.get(URL)).json()["external_id"] == ext, "g stable external_id"
                r = await c.put(URL, json={**bed, "role_arn": "arn:aws:iam::111122223333:role/soc"})
                assert r.status_code == 200 and r.json()["enabled"] is True and r.json()["external_id"] == ext, "g enable"

                # h) test endpoint (stubbed test_connection)
                r = await c.post(URL + "/test")
                assert (r.status_code, r.json()) == (200, {"ok": True, "message": "ok"}), f"h {r.text}"

                # i) DELETE -> back to deployment
                r = await c.delete(URL)
                assert r.status_code == 204, "i delete"
                assert (await c.get(URL)).json()["source"] == "deployment", "i source"

                # j) override disabled by deployment
                monkeypatch.setattr(settings, "AI_ALLOW_TENANT_OVERRIDE", False)
                r = await c.put(URL, json={"provider": "openai", "model": "gpt-4o", "api_key": KEY})
                assert r.status_code == 403, "j put"
                assert (await c.get(URL)).json()["override_allowed"] is False, "j get"
                monkeypatch.setattr(settings, "AI_ALLOW_TENANT_OVERRIDE", True)

                # k) viewer: config forbidden, info allowed
                state["role"] = "viewer"
                assert (await c.put(URL, json={"provider": "openai", "model": "x", "api_key": KEY})).status_code == 403, "k put"
                assert (await c.get(URL)).status_code == 403, "k get"
                r = await c.get("/api/v1/ai/info")
                assert r.status_code == 200 and set(r.json()) == {"provider", "model", "source"}, f"k info {r.text}"
                state["role"] = "admin"

            # l) audit rows exist and hold no secret values
            rows = (await db.execute(select(AuditLog).where(AuditLog.entity_type == "ai_config",
                                                            AuditLog.tenant_id == tid))).scalars().all()
            assert rows and {"create", "update", "delete"} <= {x.action for x in rows}, "l actions"
            for row in rows:
                dumped = json.dumps(row.changes)
                assert KEY not in dumped and "AIza-other" not in dumped, "l secret in audit"

            # m) one INFO line per save/test/delete, no secrets
            lines = [rec.getMessage() for rec in caplog.records if rec.name == "app.api.v1.ai_config"]
            assert any("save" in m for m in lines) and any("test" in m for m in lines) \
                and any("delete" in m for m in lines), f"m {lines}"
            assert all(KEY not in m and "AIza-other" not in m for m in lines), "m secret in log"
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

    async def fake_test_connection(cfg, *, allowlist=()):
        return True, "ok"

    async def fake_principal(**_):
        return "arn:aws:iam::123456789012:role/test"

    monkeypatch.setattr(api_mod, "test_connection", fake_test_connection)
    monkeypatch.setattr(aws, "deployment_principal_arn", fake_principal)
    caplog.set_level(logging.INFO, logger="app.api.v1.ai_config")
    aicfg.invalidate_all()
    asyncio.run(_scenario(monkeypatch, caplog))
