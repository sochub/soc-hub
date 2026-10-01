"""Tenant AI-provider override: admin config endpoints plus a read-only /info for every user.

Secrets are write-only: responses report only which secret fields are set."""
import asyncio
import json
import logging
import secrets as pysecrets
from typing import Any, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai import aws
from app.ai.config import decrypt_secrets, deployment_config, invalidate, row_to_config, tenant_allowlist
from app.ai.errors import AIUnavailable
from app.ai.llm import describe, test_connection
from app.ai.providers import ALL_SECRET_FIELDS, SECRET_FIELDS, SSRF_GUARDED, ConfigError, ProviderConfig, build_kwargs, missing_fields
from app.api import deps
from app.core.config import settings
from app.models.tenant_ai_config import TenantAIConfig
from app.models.user import User
from app.schemas.ai_config import AIConfigIn
from app.utils.audit import create_audit_log
from app.utils.crypto import encrypt
from app.workflows.ssrf import SSRFError, assert_url_allowed

logger = logging.getLogger(__name__)
router = APIRouter()
_PLAIN_FIELDS = ("provider", "model", "enabled", "api_base", "region", "project", "location", "auth_mode", "role_arn")


def _log(op: str, tenant_id: int, provider: Optional[str], outcome: str) -> None:
    # Tenant id, provider and outcome only — never secrets, api_base or request bodies.
    logger.info("ai_config %s tenant=%s provider=%s outcome=%s", op, tenant_id, provider, outcome)


async def _row(db, tenant_id) -> Optional[TenantAIConfig]:
    return (await db.execute(select(TenantAIConfig).where(TenantAIConfig.tenant_id == tenant_id))).scalars().first()


def _stored_secrets(row: Optional[TenantAIConfig]) -> dict:
    if not row:
        return {}
    try:
        return decrypt_secrets(row.credentials_enc, row.tenant_id)
    except AIUnavailable:
        return {}


def _out(row: Optional[TenantAIConfig], principal: Optional[str]) -> dict:
    dep = deployment_config()
    base = {"override_allowed": settings.AI_ALLOW_TENANT_OVERRIDE,
            "deployment": {"provider": dep.provider, "model": dep.model},
            "deployment_principal_arn": principal}
    if not row:
        return {**base, "source": "deployment", "provider": dep.provider, "model": dep.model, "enabled": False,
                "api_base": None, "region": None, "project": None, "location": None, "auth_mode": None,
                "role_arn": None, "external_id": None, "secrets_set": {f: False for f in sorted(ALL_SECRET_FIELDS)}}
    stored = _stored_secrets(row)
    return {**base, "source": "tenant" if (row.enabled and settings.AI_ALLOW_TENANT_OVERRIDE) else "deployment",
            **{f: getattr(row, f) for f in _PLAIN_FIELDS}, "external_id": row.external_id,
            "secrets_set": {f: bool(stored.get(f)) for f in sorted(ALL_SECRET_FIELDS)}}


def _merge_secrets(body: AIConfigIn, row: Optional[TenantAIConfig]) -> dict:
    # Stored secrets carry over only for the same provider — never reuse one vendor's key for another.
    stored = _stored_secrets(row) if row and row.provider == body.provider else {}
    allowed = SECRET_FIELDS[body.provider]
    merged = {k: v for k, v in stored.items() if k in allowed}
    for f in allowed:
        v = getattr(body, f)
        if v is not None and v.strip():
            merged[f] = v.strip()
    return merged


def _candidate(body: AIConfigIn, row, merged: dict, tenant_id: int) -> ProviderConfig:
    return ProviderConfig(provider=body.provider, model=body.model.strip(), source="tenant", tenant_id=tenant_id,
                          api_base=(body.api_base or "").strip() or None, region=body.region, project=body.project,
                          location=body.location, auth_mode=body.auth_mode if body.provider == "bedrock" else None,
                          role_arn=body.role_arn, external_id=(row.external_id if row else None), secrets=merged)


async def _validate(db, cfg: ProviderConfig, tenant_id: int, *, allow_incomplete_role: bool) -> None:
    if cfg.provider in SSRF_GUARDED and cfg.api_base:
        try:
            await asyncio.to_thread(assert_url_allowed, cfg.api_base, await tenant_allowlist(db, tenant_id))
        except SSRFError as e:
            raise HTTPException(status_code=400, detail=f"api_base rejected: {e}")
    missing = [m for m in missing_fields(cfg) if not (allow_incomplete_role and m == "role_arn")]
    if missing:
        raise HTTPException(status_code=422, detail="missing " + ", ".join(missing))
    if cfg.provider == "vertex" and cfg.secrets.get("service_account_json"):
        try:
            build_kwargs(cfg)
        except ConfigError as e:
            raise HTTPException(status_code=422, detail=str(e))


@router.get("/config")
async def get_config(db: AsyncSession = Depends(deps.get_db), current_user: User = Depends(deps.require_admin),
                     tenant_id: int = Depends(deps.get_effective_tenant_id)) -> Any:
    return _out(await _row(db, tenant_id), await aws.deployment_principal_arn())


@router.put("/config")
async def put_config(body: AIConfigIn, db: AsyncSession = Depends(deps.get_db),
                     current_user: User = Depends(deps.require_admin),
                     tenant_id: int = Depends(deps.get_effective_tenant_id)) -> Any:
    if not settings.AI_ALLOW_TENANT_OVERRIDE:
        raise HTTPException(status_code=403, detail="AI provider is managed by the deployment")
    row = await _row(db, tenant_id)
    merged = _merge_secrets(body, row)
    cfg = _candidate(body, row, merged, tenant_id)
    try:
        await _validate(db, cfg, tenant_id, allow_incomplete_role=True)
    except HTTPException:
        _log("save", tenant_id, cfg.provider, "rejected")
        raise
    created = row is None
    if created:
        row = TenantAIConfig(tenant_id=tenant_id, provider=cfg.provider, model=cfg.model)
        db.add(row)
    changed = [f for f in _PLAIN_FIELDS if getattr(row, f, None) != getattr(cfg if f != "enabled" else body, f, None)]
    row.provider, row.model = cfg.provider, cfg.model
    row.api_base, row.region, row.project, row.location = cfg.api_base, cfg.region, cfg.project, cfg.location
    row.auth_mode, row.role_arn = cfg.auth_mode, cfg.role_arn
    if cfg.provider == "bedrock" and cfg.auth_mode == "role" and not row.external_id:
        row.external_id = pysecrets.token_urlsafe(24)
    row.enabled = body.enabled and not (cfg.provider == "bedrock" and cfg.auth_mode == "role" and not cfg.role_arn)
    row.credentials_enc = encrypt(json.dumps(merged)) if merged else None
    secrets_updated = sorted(f for f in SECRET_FIELDS[cfg.provider] if (getattr(body, f) or "").strip())
    await db.flush()
    await create_audit_log(db=db, entity_type="ai_config", entity_id=row.id, action="create" if created else "update",
                           tenant_id=tenant_id, user_id=current_user.id,
                           changes={"fields": changed, "secrets_updated": secrets_updated})
    await db.commit()
    await db.refresh(row)
    # ponytail: per-process cache — other workers/processes pick the change up within the 60s TTL.
    invalidate(tenant_id)
    aws.clear_cache(tenant_id)
    _log("save", tenant_id, cfg.provider, "created" if created else "updated")
    return _out(row, await aws.deployment_principal_arn())


@router.post("/config/test")
async def test_config(body: Optional[AIConfigIn] = Body(None), db: AsyncSession = Depends(deps.get_db),
                      current_user: User = Depends(deps.require_admin),
                      tenant_id: int = Depends(deps.get_effective_tenant_id)) -> Any:
    row = await _row(db, tenant_id)
    if body is not None:
        cfg = _candidate(body, row, _merge_secrets(body, row), tenant_id)
        try:
            await _validate(db, cfg, tenant_id, allow_incomplete_role=False)
        except HTTPException:
            _log("test", tenant_id, cfg.provider, "rejected")
            raise
    elif row and row.enabled and settings.AI_ALLOW_TENANT_OVERRIDE:
        try:
            cfg = row_to_config(row)
        except AIUnavailable as e:
            _log("test", tenant_id, row.provider, "unavailable")
            return {"ok": False, "message": str(e)}
    else:
        cfg = deployment_config()
    allow = await tenant_allowlist(db, tenant_id) if cfg.source == "tenant" else []
    ok, message = await test_connection(cfg, allowlist=allow)
    _log("test", tenant_id, cfg.provider, "ok" if ok else "failed")
    return {"ok": ok, "message": message}


@router.delete("/config", status_code=204)
async def delete_config(db: AsyncSession = Depends(deps.get_db), current_user: User = Depends(deps.require_admin),
                        tenant_id: int = Depends(deps.get_effective_tenant_id)) -> Response:
    row = await _row(db, tenant_id)
    provider = row.provider if row else None
    if row:
        await create_audit_log(db=db, entity_type="ai_config", entity_id=row.id, action="delete",
                               tenant_id=tenant_id, user_id=current_user.id)
        await db.delete(row)
        await db.commit()
    # ponytail: per-process cache — other workers/processes pick the change up within the 60s TTL.
    invalidate(tenant_id)
    aws.clear_cache(tenant_id)
    _log("delete", tenant_id, provider, "deleted" if row else "absent")
    return Response(status_code=204)


@router.get("/info")
async def info(db: AsyncSession = Depends(deps.get_db), current_user: User = Depends(deps.get_current_active_user),
               tenant_id: int = Depends(deps.get_effective_tenant_id)) -> Any:
    return await describe(db, tenant_id)
