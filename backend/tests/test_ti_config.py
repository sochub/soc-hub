import json
import logging
import secrets

import pytest
from sqlalchemy import delete, select

from app.db.session import AsyncSessionLocal, engine
from app.enrichment.config import DEFAULTS, load_settings, runnable_sources
from app.models.tenant import Tenant
from app.models.tenant_enrichment_config import TenantEnrichmentConfig
from app.utils.crypto import encrypt


@pytest.mark.asyncio
async def test_defaults_without_row_and_keyed_sources_inactive():
    async with AsyncSessionLocal() as db:
        t = Tenant(name="wf-test", slug=f"wf-test-{secrets.token_hex(3)}-ti")
        db.add(t)
        await db.commit()
        try:
            s = await load_settings(db, t.id)
            assert s.auto_max_tlp == "green" and s.artifact_tlp == "amber" and s.cache_ttl_hours == 24
            assert s.vt_per_minute == 4 and s.vt_per_day == 500 and s.keys == {}
            assert runnable_sources(s) == ["rdap", "crtsh"]
        finally:
            await db.execute(delete(Tenant).where(Tenant.id == t.id))
            await db.commit()
    await engine.dispose()


@pytest.mark.asyncio
async def test_row_with_keys_and_toggles(caplog):
    async with AsyncSessionLocal() as db:
        t = Tenant(name="wf-test", slug=f"wf-test-{secrets.token_hex(3)}-ti")
        db.add(t)
        await db.flush()
        db.add(TenantEnrichmentConfig(
            tenant_id=t.id, auto_max_tlp="amber", sources={**DEFAULTS["sources"], "crtsh": False},
            internal_domains=["corp.local"],
            credentials_enc=encrypt(json.dumps({"virustotal_api_key": "vt-k", "abusech_auth_key": "ab-k"}))))
        await db.commit()
        try:
            s = await load_settings(db, t.id)
            assert s.auto_max_tlp == "amber" and s.internal_domains == ["corp.local"]
            assert s.keys == {"virustotal_api_key": "vt-k", "abusech_auth_key": "ab-k"}
            assert runnable_sources(s) == ["virustotal", "urlhaus", "threatfox", "rdap"]
            # undecryptable credentials -> no keys, key_error, ERROR log with tenant id only
            row = (await db.execute(select(TenantEnrichmentConfig).where(
                TenantEnrichmentConfig.tenant_id == t.id))).scalars().one()
            row.credentials_enc = "garbage"
            await db.commit()
            caplog.set_level(logging.ERROR, logger="app.enrichment.config")
            s = await load_settings(db, t.id)
            assert s.keys == {} and s.key_error is True
            msgs = [r.getMessage() for r in caplog.records if r.name == "app.enrichment.config"]
            assert msgs == [f"ti_config decrypt_failed tenant={t.id}"]
        finally:
            await db.execute(delete(Tenant).where(Tenant.id == t.id))
            await db.commit()
    await engine.dispose()
