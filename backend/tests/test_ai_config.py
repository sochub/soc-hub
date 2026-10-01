import asyncio
import json
import logging
import uuid

import pytest
from sqlalchemy import delete

from app.ai import config as aicfg, llm
from app.ai.errors import AIUnavailable
from app.core.config import settings
from app.db.session import AsyncSessionLocal, engine
from app.models.tenant import Tenant
from app.models.tenant_ai_config import TenantAIConfig
from app.utils.crypto import encrypt


def test_deployment_default_is_legacy_ollama(monkeypatch):
    for k in ("AI_PROVIDER", "AI_MODEL", "AI_API_BASE", "AI_API_KEY"):
        monkeypatch.setattr(settings, k, None)
    c = aicfg.deployment_config()
    assert (c.provider, c.model, c.api_base, c.source) == ("ollama", settings.OLLAMA_MODEL, settings.OLLAMA_BASE_URL, "deployment")


def test_deployment_explicit(monkeypatch):
    monkeypatch.setattr(settings, "AI_PROVIDER", "anthropic")
    monkeypatch.setattr(settings, "AI_MODEL", "claude-x")
    monkeypatch.setattr(settings, "AI_API_KEY", "k")
    c = aicfg.deployment_config()
    assert (c.provider, c.model, c.secrets) == ("anthropic", "claude-x", {"api_key": "k"})


def test_deployment_bedrock_region_and_chain(monkeypatch):
    monkeypatch.setattr(settings, "AI_PROVIDER", "bedrock")
    monkeypatch.setattr(settings, "AI_MODEL", "m")
    monkeypatch.setattr(settings, "AI_AWS_REGION", "eu-west-1")
    c = aicfg.deployment_config()
    assert c.region == "eu-west-1" and c.auth_mode == "chain"


def test_complete_unconfigured_deployment_is_unavailable(monkeypatch):
    monkeypatch.setattr(settings, "AI_PROVIDER", "openai")
    monkeypatch.setattr(settings, "AI_MODEL", "gpt-4o-mini")
    monkeypatch.setattr(settings, "AI_API_KEY", None)
    aicfg.invalidate_all()
    try:
        with pytest.raises(AIUnavailable, match="misconfigured"):
            asyncio.run(llm.complete([{"role": "user", "content": "x"}], tenant_id=None))
    finally:
        aicfg.invalidate_all()


def test_row_to_config_decrypts_and_bad_token_is_unavailable():
    row = TenantAIConfig(tenant_id=1, provider="openai", model="m", enabled=True,
                         credentials_enc=encrypt(json.dumps({"api_key": "k1"})))
    assert aicfg.row_to_config(row).secrets == {"api_key": "k1"}
    row.credentials_enc = "garbage"
    with pytest.raises(AIUnavailable, match="re-enter"):
        aicfg.row_to_config(row)


def test_decrypt_failure_logs_error_with_ids_only(caplog):
    ciphertext = "gAAAAAbogus-ciphertext-value"
    row = TenantAIConfig(tenant_id=4242, provider="openai", model="m", enabled=True, credentials_enc=ciphertext)
    with caplog.at_level(logging.ERROR, logger="app.ai.config"):
        with pytest.raises(AIUnavailable):
            aicfg.row_to_config(row)
    recs = [r for r in caplog.records if r.name == "app.ai.config"]
    assert len(recs) == 1 and recs[0].levelno == logging.ERROR
    msg = recs[0].getMessage()
    assert "4242" in msg and "InvalidToken" in msg
    assert ciphertext not in msg and "bogus" not in msg
    assert recs[0].exc_info is None


def test_resolution_override_cache_and_no_fallback(monkeypatch):
    async def scenario():
        slug = f"wf-test-{uuid.uuid4().hex[:8]}"
        async with AsyncSessionLocal() as db:
            t = Tenant(name=slug, slug=slug)
            db.add(t)
            await db.commit()
            tid = t.id
        try:
            aicfg.invalidate_all()
            async with AsyncSessionLocal() as db:
                assert (await aicfg.resolve_config(db, tid)).source == "deployment"
                row = TenantAIConfig(tenant_id=tid, provider="anthropic", model="c", enabled=True,
                                     credentials_enc=encrypt(json.dumps({"api_key": "tk"})))
                db.add(row)
                await db.commit()
                # cached for 60 s -> still deployment until invalidated
                assert (await aicfg.resolve_config(db, tid)).source == "deployment"
                aicfg.invalidate(tid)
                cfg = await aicfg.resolve_config(db, tid)
                assert (cfg.source, cfg.provider, cfg.secrets) == ("tenant", "anthropic", {"api_key": "tk"})
                # override disabled by deployment -> deployment config
                monkeypatch.setattr(settings, "AI_ALLOW_TENANT_OVERRIDE", False)
                aicfg.invalidate(tid)
                assert (await aicfg.resolve_config(db, tid)).source == "deployment"
                monkeypatch.setattr(settings, "AI_ALLOW_TENANT_OVERRIDE", True)
                aicfg.invalidate(tid)
            # no fallback: tenant provider failure must not call the deployment provider
            seen = []

            async def fail(**kw):
                seen.append(kw["model"])
                raise __import__("litellm").AuthenticationError(message="bad", llm_provider="anthropic", model="c")
            monkeypatch.setattr(llm.litellm, "acompletion", fail)
            with pytest.raises(AIUnavailable):
                await llm.complete([{"role": "user", "content": "x"}], tenant_id=tid)
            assert seen == ["anthropic/c"]
            async with AsyncSessionLocal() as db:
                assert await llm.describe(db, tid) == {"provider": "anthropic", "model": "c", "source": "tenant"}
            # undecryptable tenant credentials -> AIUnavailable, never the deployment provider
            async with AsyncSessionLocal() as db:
                r = (await db.execute(TenantAIConfig.__table__.select().where(
                    TenantAIConfig.tenant_id == tid))).first()
                await db.execute(TenantAIConfig.__table__.update().where(
                    TenantAIConfig.id == r.id).values(credentials_enc="garbage"))
                await db.commit()
            aicfg.invalidate(tid)
            seen.clear()
            with pytest.raises(AIUnavailable, match="re-enter"):
                await llm.complete([{"role": "user", "content": "x"}], tenant_id=tid)
            assert seen == []
            async with AsyncSessionLocal() as db:
                assert await llm.describe(db, tid) == {"provider": None, "model": None, "source": "tenant"}
        finally:
            async with AsyncSessionLocal() as db:
                await db.execute(delete(TenantAIConfig).where(TenantAIConfig.tenant_id == tid))
                await db.execute(delete(Tenant).where(Tenant.id == tid))
                await db.commit()
            aicfg.invalidate_all()
            await engine.dispose()

    asyncio.run(scenario())


def test_describe_reports_resolved_config(monkeypatch):
    for k in ("AI_PROVIDER", "AI_MODEL", "AI_API_BASE", "AI_API_KEY"):
        monkeypatch.setattr(settings, k, None)
    aicfg.invalidate_all()
    try:
        out = asyncio.run(llm.describe(None, None))
    finally:
        aicfg.invalidate_all()
    assert out == {"provider": "ollama", "model": settings.OLLAMA_MODEL, "source": "deployment"}
