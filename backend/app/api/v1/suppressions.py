"""Alert suppression rules: matched at ingest; a used rule can only be disabled, not deleted or re-scoped."""
from typing import Any, List

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from app.models.suppression_rule import SuppressionRule
from app.models.user import User
from app.schemas.suppression import SuppressionCreate, SuppressionOut, SuppressionPreview, SuppressionUpdate
from app.services import suppression_service

router = APIRouter()


@router.get("/", response_model=List[SuppressionOut])
async def list_rules(
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
    tenant_id: int = Depends(deps.get_effective_tenant_id),
) -> Any:
    return (await db.execute(select(SuppressionRule).where(SuppressionRule.tenant_id == tenant_id)
                             .order_by(SuppressionRule.id.desc()))).scalars().all()


@router.post("/preview")
async def preview_rule(
    body: SuppressionPreview,
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
    tenant_id: int = Depends(deps.get_effective_tenant_id),
) -> Any:
    """How many of the last 500 alerts these conditions would have suppressed."""
    return await suppression_service.preview(db, tenant_id, [c.model_dump() for c in body.conditions])


@router.post("/", response_model=SuppressionOut, status_code=201)
async def create_rule(
    body: SuppressionCreate,
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.require_analyst_or_above),
    tenant_id: int = Depends(deps.get_effective_tenant_id),
) -> Any:
    rule = await suppression_service.create_rule(db, tenant_id=tenant_id, user_id=current_user.id,
                                                 data=body.model_dump())
    await db.commit()
    await db.refresh(rule)
    return rule


@router.put("/{rule_id}", response_model=SuppressionOut)
async def update_rule(
    rule_id: int,
    body: SuppressionUpdate,
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.require_analyst_or_above),
    tenant_id: int = Depends(deps.get_effective_tenant_id),
) -> Any:
    rule = await suppression_service.get_rule(db, tenant_id, rule_id)
    await suppression_service.update_rule(db, rule=rule, user_id=current_user.id,
                                          data=body.model_dump(exclude_unset=True))
    await db.commit()
    await db.refresh(rule)
    return rule


@router.delete("/{rule_id}", status_code=204)
async def delete_rule(
    rule_id: int,
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.require_analyst_or_above),
    tenant_id: int = Depends(deps.get_effective_tenant_id),
) -> None:
    rule = await suppression_service.get_rule(db, tenant_id, rule_id)
    await suppression_service.delete_rule(db, rule=rule, user_id=current_user.id)
    await db.commit()
