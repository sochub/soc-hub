"""Per-tenant enrichment settings with defaults; keys decrypted only when loaded."""
import json
import logging
from dataclasses import dataclass, field
from typing import Dict, List

from sqlalchemy import select

from app.models.tenant_enrichment_config import TenantEnrichmentConfig
from app.utils.crypto import decrypt

logger = logging.getLogger(__name__)

SOURCES = ("virustotal", "urlhaus", "threatfox", "rdap", "crtsh")
SOURCE_KEY = {"virustotal": "virustotal_api_key", "urlhaus": "abusech_auth_key", "threatfox": "abusech_auth_key"}
KEY_FIELDS = ("virustotal_api_key", "abusech_auth_key")
DEFAULTS = {
    "auto_max_tlp": "green", "artifact_tlp": "amber", "cache_ttl_hours": 24,
    "sources": {s: True for s in SOURCES}, "vt_per_minute": 4, "vt_per_day": 500, "internal_domains": [],
}


@dataclass
class EnrichmentSettings:
    auto_max_tlp: str = DEFAULTS["auto_max_tlp"]
    artifact_tlp: str = DEFAULTS["artifact_tlp"]
    cache_ttl_hours: int = DEFAULTS["cache_ttl_hours"]
    sources: Dict[str, bool] = field(default_factory=lambda: dict(DEFAULTS["sources"]))
    vt_per_minute: int = DEFAULTS["vt_per_minute"]
    vt_per_day: int = DEFAULTS["vt_per_day"]
    internal_domains: List[str] = field(default_factory=list)
    keys: Dict[str, str] = field(default_factory=dict)
    key_error: bool = False


def decrypt_keys(enc, tenant_id) -> tuple:
    if not enc:
        return {}, False
    try:
        data = json.loads(decrypt(enc))
        if not isinstance(data, dict):
            raise ValueError("not an object")
        return {k: v for k, v in data.items() if k in KEY_FIELDS and v}, False
    except Exception:
        logger.error("ti_config decrypt_failed tenant=%s", tenant_id)
        return {}, True


async def load_settings(db, tenant_id: int) -> EnrichmentSettings:
    row = (await db.execute(select(TenantEnrichmentConfig).where(
        TenantEnrichmentConfig.tenant_id == tenant_id))).scalars().first()
    if row is None:
        return EnrichmentSettings()
    keys, key_error = decrypt_keys(row.credentials_enc, tenant_id)
    return EnrichmentSettings(
        auto_max_tlp=row.auto_max_tlp, artifact_tlp=row.artifact_tlp, cache_ttl_hours=row.cache_ttl_hours,
        sources={**DEFAULTS["sources"], **(row.sources or {})}, vt_per_minute=row.vt_per_minute,
        vt_per_day=row.vt_per_day, internal_domains=list(row.internal_domains or []), keys=keys, key_error=key_error)


def runnable_sources(s: EnrichmentSettings) -> List[str]:
    out = []
    for src in SOURCES:
        if not s.sources.get(src, True):
            continue
        key = SOURCE_KEY.get(src)
        if key and not s.keys.get(key):
            continue
        out.append(src)
    return out
