import json
import logging
import re
from typing import Any, List

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse, Response
from sqlalchemy import Text, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from app.models.tenant import Tenant
from app.models.tenant_secret import TenantSecret
from app.models.user import User
from app.models.workflow import Workflow
from app.schemas.secret import SecretCreate, SecretNameOut, SecretOut, SecretUpdate
from app.secrets.hosts import valid_host_pattern
from app.utils.audit import create_audit_log
from app.utils.crypto import encrypt

router = APIRouter()
logger = logging.getLogger(__name__)


async def _in_use_by(db: AsyncSession, tenant_id: int, name: str) -> List[dict]:
    wfs = (await db.execute(
        select(Workflow).where(Workflow.tenant_id == tenant_id,
                               Workflow.graph.cast(Text).ilike(f"%secrets.{name}%"))
        .order_by(Workflow.name))).scalars().all()
    pat = re.compile(rf"\bsecrets\.{re.escape(name)}\b")
    return [{"id": w.id, "name": w.name} for w in wfs if pat.search(json.dumps(w.graph))]


async def _emails(db: AsyncSession, rows) -> dict:
    ids = {i for r in rows for i in (r.created_by, r.updated_by) if i}
    if not ids:
        return {}
    return dict((await db.execute(select(User.id, User.email).where(User.id.in_(ids)))).all())


def _out(row: TenantSecret, emails: dict, in_use: List[dict]) -> dict:
    return {"name": row.name, "description": row.description, "allowed_hosts": row.allowed_hosts or [],
            "last_used_at": row.last_used_at, "created_at": row.created_at, "updated_at": row.updated_at,
            "created_by_email": emails.get(row.created_by), "updated_by_email": emails.get(row.updated_by),
            "in_use_by": in_use}


async def _get(db: AsyncSession, tenant_id: int, name: str) -> TenantSecret:
    row = (await db.execute(select(TenantSecret).where(
        TenantSecret.tenant_id == tenant_id, TenantSecret.name == name))).scalars().first()
    if not row:
        raise HTTPException(status_code=404, detail="Secret not found")
    return row


async def _check_hosts(db: AsyncSession, tenant_id: int, hosts: List[str]) -> None:
    tenant = (await db.execute(select(Tenant).where(Tenant.id == tenant_id))).scalars().first()
    allow = (tenant.workflow_http_allowlist if tenant else None) or []
    bad = [h for h in hosts if not valid_host_pattern(h, allow)]
    if bad:
        raise HTTPException(status_code=422, detail=f"Invalid allowed host(s): {', '.join(repr(h) for h in bad)}")


@router.get("/", response_model=List[SecretOut])
async def list_secrets(db: AsyncSession = Depends(deps.get_db), current_user: User = Depends(deps.require_admin),
                       tenant_id: int = Depends(deps.get_effective_tenant_id)) -> Any:
    rows = (await db.execute(select(TenantSecret).where(TenantSecret.tenant_id == tenant_id)
                             .order_by(TenantSecret.name))).scalars().all()
    emails = await _emails(db, rows)
    return [_out(r, emails, await _in_use_by(db, tenant_id, r.name)) for r in rows]


@router.get("/names", response_model=List[SecretNameOut])
async def secret_names(db: AsyncSession = Depends(deps.get_db),
                       current_user: User = Depends(deps.require_analyst_or_above),
                       tenant_id: int = Depends(deps.get_effective_tenant_id)) -> Any:
    rows = (await db.execute(select(TenantSecret).where(TenantSecret.tenant_id == tenant_id)
                             .order_by(TenantSecret.name))).scalars().all()
    return [{"name": r.name, "description": r.description, "allowed_hosts": r.allowed_hosts or []} for r in rows]


@router.post("/", response_model=SecretOut, status_code=201)
async def create_secret(body: SecretCreate, db: AsyncSession = Depends(deps.get_db),
                        current_user: User = Depends(deps.require_admin),
                        tenant_id: int = Depends(deps.get_effective_tenant_id)) -> Any:
    await _check_hosts(db, tenant_id, body.allowed_hosts)
    exists = (await db.execute(select(TenantSecret.id).where(
        TenantSecret.tenant_id == tenant_id, TenantSecret.name == body.name))).first()
    if exists:
        logger.info("secret create tenant=%s name=%s outcome=duplicate", tenant_id, body.name)
        raise HTTPException(status_code=409, detail="A secret with this name already exists")
    row = TenantSecret(tenant_id=tenant_id, name=body.name, value_enc=encrypt(body.value),
                       allowed_hosts=body.allowed_hosts, description=body.description,
                       created_by=current_user.id, updated_by=current_user.id)
    db.add(row)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        logger.info("secret create tenant=%s name=%s outcome=duplicate", tenant_id, body.name)
        raise HTTPException(status_code=409, detail="A secret with this name already exists")
    await create_audit_log(db=db, entity_type="secret", entity_id=row.id, action="create", tenant_id=tenant_id,
                           user_id=current_user.id,
                           changes={"name": row.name, "fields": ["value", "allowed_hosts", "description"]})
    await db.commit()
    await db.refresh(row)
    logger.info("secret create tenant=%s name=%s outcome=ok", tenant_id, row.name)
    return _out(row, {current_user.id: current_user.email}, [])


@router.put("/{name}", response_model=SecretOut)
async def update_secret(name: str, body: SecretUpdate, db: AsyncSession = Depends(deps.get_db),
                        current_user: User = Depends(deps.require_admin),
                        tenant_id: int = Depends(deps.get_effective_tenant_id)) -> Any:
    row = await _get(db, tenant_id, name)
    fields = []
    if body.allowed_hosts is not None:
        await _check_hosts(db, tenant_id, body.allowed_hosts)
        row.allowed_hosts = body.allowed_hosts
        fields.append("allowed_hosts")
    if body.description is not None:
        row.description = body.description
        fields.append("description")
    if body.value is not None and body.value.strip():
        row.value_enc = encrypt(body.value)
        fields.append("value")
    row.updated_by = current_user.id
    await create_audit_log(db=db, entity_type="secret", entity_id=row.id, action="update", tenant_id=tenant_id,
                           user_id=current_user.id, changes={"name": row.name, "fields": fields})
    await db.commit()
    await db.refresh(row)
    logger.info("secret update tenant=%s name=%s outcome=ok", tenant_id, name)
    return _out(row, await _emails(db, [row]), await _in_use_by(db, tenant_id, name))


@router.delete("/{name}", status_code=204, response_class=Response)
async def delete_secret(name: str, force: bool = False, db: AsyncSession = Depends(deps.get_db),
                        current_user: User = Depends(deps.require_admin),
                        tenant_id: int = Depends(deps.get_effective_tenant_id)):
    row = await _get(db, tenant_id, name)
    in_use = await _in_use_by(db, tenant_id, name)
    if in_use and not force:
        logger.info("secret delete tenant=%s name=%s outcome=in_use", tenant_id, name)
        return JSONResponse(status_code=409, content={
            "detail": f"Secret {name} is used by {len(in_use)} workflow(s)", "workflows": in_use})
    await create_audit_log(db=db, entity_type="secret", entity_id=row.id, action="delete", tenant_id=tenant_id,
                           user_id=current_user.id, changes={"name": name, "fields": []})
    await db.delete(row)
    await db.commit()
    logger.info("secret delete tenant=%s name=%s outcome=ok", tenant_id, name)
    return Response(status_code=204)
