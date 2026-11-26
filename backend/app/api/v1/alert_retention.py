"""Alert retention policies (admin only): scheduled deletion of old alerts that no case holds."""
from typing import Any, List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from app.models.alert_retention import AlertRetentionPolicy
from app.models.user import User
from app.schemas.alert_retention import RetentionCreate, RetentionOut, RetentionScope, RetentionUpdate
from app.services import alert_retention
from app.utils.audit import create_audit_log

router = APIRouter()


async def _get(db: AsyncSession, tenant_id: int, policy_id: int) -> AlertRetentionPolicy:
    p = (await db.execute(select(AlertRetentionPolicy).where(
        AlertRetentionPolicy.id == policy_id, AlertRetentionPolicy.tenant_id == tenant_id))).scalars().first()
    if p is None:
        raise HTTPException(status_code=404, detail="Retention policy not found")
    return p


def _scope(data: dict) -> dict:
    return {k: data[k] for k in ("name", "retain_days", "statuses", "conditions", "enabled") if k in data}


@router.get("/", response_model=List[RetentionOut])
async def list_policies(db: AsyncSession = Depends(deps.get_db), current_user: User = Depends(deps.require_admin),
                        tenant_id: int = Depends(deps.get_effective_tenant_id)) -> Any:
    return (await db.execute(select(AlertRetentionPolicy).where(AlertRetentionPolicy.tenant_id == tenant_id)
                             .order_by(AlertRetentionPolicy.id))).scalars().all()


@router.post("/preview")
async def preview_policy(body: RetentionScope, db: AsyncSession = Depends(deps.get_db),
                         current_user: User = Depends(deps.require_admin),
                         tenant_id: int = Depends(deps.get_effective_tenant_id)) -> Any:
    """What these settings would delete if the policy ran now."""
    return await alert_retention.preview(db, tenant_id, body.model_dump())


@router.post("/", response_model=RetentionOut, status_code=201)
async def create_policy(body: RetentionCreate, db: AsyncSession = Depends(deps.get_db),
                        current_user: User = Depends(deps.require_admin),
                        tenant_id: int = Depends(deps.get_effective_tenant_id)) -> Any:
    data = body.model_dump()
    p = AlertRetentionPolicy(tenant_id=tenant_id, created_by=current_user.id, **_scope(data))
    db.add(p)
    await db.flush()
    await create_audit_log(db, "alert_retention_policy", p.id, "created", tenant_id, current_user.id, _scope(data))
    await db.commit()
    await db.refresh(p)
    return p


@router.put("/{policy_id}", response_model=RetentionOut)
async def update_policy(policy_id: int, body: RetentionUpdate, db: AsyncSession = Depends(deps.get_db),
                        current_user: User = Depends(deps.require_admin),
                        tenant_id: int = Depends(deps.get_effective_tenant_id)) -> Any:
    p = await _get(db, tenant_id, policy_id)
    data = _scope(body.model_dump(exclude_unset=True))
    if any(v is None for v in data.values()):
        raise HTTPException(status_code=422, detail="Fields cannot be null")
    for k, v in data.items():
        setattr(p, k, v)
    await create_audit_log(db, "alert_retention_policy", p.id, "updated", tenant_id, current_user.id, data)
    await db.commit()
    await db.refresh(p)
    return p


@router.post("/{policy_id}/run", response_model=RetentionOut)
async def run_policy_now(policy_id: int, db: AsyncSession = Depends(deps.get_db),
                         current_user: User = Depends(deps.require_admin),
                         tenant_id: int = Depends(deps.get_effective_tenant_id)) -> Any:
    """Run the policy now, enabled or not. The result (count, or the failure) is on the returned policy."""
    p = await _get(db, tenant_id, policy_id)
    uid = current_user.id
    await alert_retention.run_safely(db, p, trigger="manual", user_id=uid)
    return await _get(db, tenant_id, policy_id)


@router.delete("/{policy_id}", status_code=204)
async def delete_policy(policy_id: int, db: AsyncSession = Depends(deps.get_db),
                        current_user: User = Depends(deps.require_admin),
                        tenant_id: int = Depends(deps.get_effective_tenant_id)) -> None:
    p = await _get(db, tenant_id, policy_id)
    await create_audit_log(db, "alert_retention_policy", p.id, "deleted", tenant_id, current_user.id,
                           {"name": p.name})
    await db.delete(p)
    await db.commit()
