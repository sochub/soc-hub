"""Which AI provider config applies: tenant override or deployment default."""
import json
import logging
import os
import time
from typing import Dict, List, Optional, Tuple

from cryptography.fernet import InvalidToken
from sqlalchemy import select

from app.ai.errors import AIUnavailable
from app.ai.providers import ProviderConfig
from app.core.config import settings
from app.models.tenant import Tenant
from app.models.tenant_ai_config import TenantAIConfig
from app.utils.crypto import decrypt

logger = logging.getLogger(__name__)

_TTL_SECONDS = 60.0
_cache: Dict[Optional[int], Tuple[float, ProviderConfig]] = {}


def invalidate(tenant_id: int) -> None:
    _cache.pop(tenant_id, None)


def invalidate_all() -> None:
    _cache.clear()


def deployment_config() -> ProviderConfig:
    provider = (settings.AI_PROVIDER or "ollama").lower()
    is_ollama = provider == "ollama"
    model = settings.AI_MODEL or (settings.OLLAMA_MODEL if is_ollama else "")
    api_base = settings.AI_API_BASE or (settings.OLLAMA_BASE_URL if is_ollama else None)
    region = settings.AI_AWS_REGION or os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION")
    secrets = {"api_key": settings.AI_API_KEY} if settings.AI_API_KEY else {}
    return ProviderConfig(provider=provider, model=model, source="deployment", api_base=api_base,
                          region=region, project=settings.VERTEX_PROJECT, location=settings.VERTEX_LOCATION,
                          auth_mode="chain", secrets=secrets)


def decrypt_secrets(enc: Optional[str], tenant_id: Optional[int] = None) -> dict:
    if not enc:
        return {}
    try:
        return json.loads(decrypt(enc))
    except (InvalidToken, ValueError) as e:
        # Tenant id and exception type only — never the ciphertext or plaintext.
        logger.error("ai_config decrypt_failed tenant=%s type=%s", tenant_id, type(e).__name__)
        raise AIUnavailable(
            "AI credentials can't be decrypted (SECRET_KEY changed?) — re-enter them in Integrations"
        ) from None


def row_to_config(row: TenantAIConfig) -> ProviderConfig:
    return ProviderConfig(provider=row.provider, model=row.model, source="tenant", tenant_id=row.tenant_id,
                          api_base=row.api_base, region=row.region, project=row.project, location=row.location,
                          auth_mode=row.auth_mode, role_arn=row.role_arn, external_id=row.external_id,
                          secrets=decrypt_secrets(row.credentials_enc, row.tenant_id))


async def resolve_config(db, tenant_id: Optional[int]) -> ProviderConfig:
    """Tenant override if enabled, else the deployment default. A broken tenant override raises
    AIUnavailable — it never falls back to the deployment provider."""
    hit = _cache.get(tenant_id)
    if hit and time.monotonic() - hit[0] < _TTL_SECONDS:
        return hit[1]
    cfg = deployment_config()
    if tenant_id is not None and settings.AI_ALLOW_TENANT_OVERRIDE:
        row = (await db.execute(select(TenantAIConfig).where(TenantAIConfig.tenant_id == tenant_id))).scalars().first()
        if row and row.enabled:
            cfg = row_to_config(row)
    _cache[tenant_id] = (time.monotonic(), cfg)
    return cfg


async def tenant_allowlist(db, tenant_id: Optional[int]) -> List[str]:
    if tenant_id is None:
        return []
    t = (await db.execute(select(Tenant).where(Tenant.id == tenant_id))).scalars().first()
    return list((t.workflow_http_allowlist if t else None) or [])
