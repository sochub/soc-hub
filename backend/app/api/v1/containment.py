"""Containment guardrails (admin policy) and the containment ledger (cases show it; analysts mark releases)."""
import ipaddress
from datetime import datetime, timezone
from typing import Any, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from app.models.case import Case, TimelineEvent
from app.models.containment import ContainmentAction, ContainmentPolicy, TARGET_KINDS
from app.models.user import User
from app.services import containment
from app.utils.audit import create_audit_log

router = APIRouter()


class ProtectedEntry(BaseModel):
    kind: str
    pattern: str = Field(min_length=1, max_length=255)
    note: Optional[str] = Field(default=None, max_length=200)

    @field_validator("kind")
    @classmethod
    def _kind(cls, v):
        if v not in TARGET_KINDS + ("any",):
            raise ValueError("kind must be host, user, ip or any")
        return v

    @model_validator(mode="after")
    def _ip(self):
        if self.kind == "ip":
            try:
                ipaddress.ip_network(self.pattern.strip(), strict=False)
            except ValueError:
                raise ValueError(f"'{self.pattern}' is not an IP address or CIDR range") from None
        return self


class PolicyIn(BaseModel):
    enabled: bool = True
    daily_cap: int = Field(ge=0, le=10_000)
    max_targets_per_case: int = Field(ge=0, le=1000)
    protected: List[ProtectedEntry] = Field(default=[], max_length=500)


def _action(a: ContainmentAction) -> dict:
    return {"id": a.id, "case_id": a.case_id, "run_id": a.run_id, "workflow_id": a.workflow_id, "action": a.action,
            "target_kind": a.target_kind, "target": a.target, "status": a.status, "reason": a.reason,
            "release_hint": a.release_hint, "created_at": a.created_at, "released_at": a.released_at,
            "released_by": a.released_by}


@router.get("/policy")
async def get_policy(
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
    tenant_id: int = Depends(deps.get_effective_tenant_id),
) -> Any:
    return await containment.policy(db, tenant_id)


@router.put("/policy")
async def put_policy(
    body: PolicyIn,
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.require_admin),
    tenant_id: int = Depends(deps.get_effective_tenant_id),
) -> Any:
    before = await containment.policy(db, tenant_id)
    row = (await db.execute(select(ContainmentPolicy).where(ContainmentPolicy.tenant_id == tenant_id))).scalars().first()
    if row is None:
        row = ContainmentPolicy(tenant_id=tenant_id)
        db.add(row)
    data = body.model_dump()
    for k, v in data.items():
        setattr(row, k, v)
    await db.flush()
    changes = {k: {"from": before[k], "to": data[k]} for k in data if before[k] != data[k]}
    if changes:
        await create_audit_log(db=db, entity_type="containment_policy", entity_id=row.id, action="update",
                               tenant_id=tenant_id, user_id=current_user.id, changes=changes)
    await db.commit()
    return await containment.policy(db, tenant_id)


@router.get("/actions")
async def list_actions(
    case_id: Optional[int] = None,
    status: Optional[str] = None,
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
    tenant_id: int = Depends(deps.get_effective_tenant_id),
) -> Any:
    q = select(ContainmentAction).where(ContainmentAction.tenant_id == tenant_id)
    if case_id is not None:
        q = q.where(ContainmentAction.case_id == case_id)
    if status:
        q = q.where(ContainmentAction.status == status)
    rows = (await db.execute(q.order_by(ContainmentAction.id.desc()).limit(500))).scalars().all()
    return [_action(a) for a in rows]


@router.post("/actions/{action_id}/release")
async def release_action(
    action_id: int,
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.require_analyst_or_above),
    tenant_id: int = Depends(deps.get_effective_tenant_id),
) -> Any:
    """Record that a containment was lifted (at the vendor, e.g. per release_hint). It frees the case's slot."""
    a = (await db.execute(select(ContainmentAction).where(
        ContainmentAction.id == action_id, ContainmentAction.tenant_id == tenant_id))).scalars().first()
    if a is None:
        raise HTTPException(status_code=404, detail="Containment action not found")
    if a.status != "active":
        raise HTTPException(status_code=409, detail=f"This action is {a.status}, not active")
    a.status, a.released_at, a.released_by = "released", datetime.now(timezone.utc), current_user.id
    if a.case_id and (await db.execute(select(Case.id).where(Case.id == a.case_id, Case.tenant_id == tenant_id))).first():
        db.add(TimelineEvent(case_id=a.case_id, user_id=current_user.id, event_type="containment_released",
                             content=f"Containment released: {a.action} on {a.target_kind} {a.target}."))
    await create_audit_log(db=db, entity_type="containment_action", entity_id=a.id, action="released",
                           tenant_id=tenant_id, user_id=current_user.id, changes={"target": a.target})
    await db.commit()
    await db.refresh(a)
    return _action(a)
