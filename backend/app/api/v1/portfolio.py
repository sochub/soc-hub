"""Organizations view: one view across the organizations (tenants) a user works in (see app/services/portfolio.py)."""
from typing import Any, List

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from app.models.tenant import Tenant
from app.models.user import User
from app.services import portfolio
from app.utils.audit import create_audit_log
from app.utils.emails import normalize_email

router = APIRouter()


class DigestRecipients(BaseModel):
    recipients: List[EmailStr] = Field(default=[], max_length=10)


async def _enterable(db: AsyncSession, user: User, tenant_id: int) -> tuple:
    for t, role in (await portfolio.enterable_tenants(db, user))[0]:
        if t.id == tenant_id:
            return t, role
    raise HTTPException(status_code=404, detail="Tenant not found")


@router.get("/")
async def get_portfolio(
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
) -> Any:
    """Per-tenant metrics and one queue of open cases (SLA breached first, then severity, then age)."""
    return await portfolio.overview(db, current_user)


@router.get("/{tenant_id}/digest")
async def get_digest(
    tenant_id: int,
    days: int = Query(7, ge=1, le=90),
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
) -> Any:
    t, role = await _enterable(db, current_user, tenant_id)
    d = await portfolio.digest(db, t, days)
    return {**d, "text": portfolio.digest_text(d), "recipients": t.digest_recipients or [],
            "last_sent_at": t.digest_last_sent_at, "can_edit_recipients": role in ("admin", "super_admin")}


@router.put("/{tenant_id}/digest-recipients")
async def put_digest_recipients(
    tenant_id: int,
    body: DigestRecipients,
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
) -> Any:
    """Tenant admins choose who gets the weekly summary email ([] turns it off)."""
    t, role = await _enterable(db, current_user, tenant_id)
    if role not in ("admin", "super_admin"):
        raise HTTPException(status_code=403, detail="Only this tenant's admins can change who gets the summary")
    new = sorted({normalize_email(str(e)) for e in body.recipients})
    row = await db.get(Tenant, t.id)
    if new != (row.digest_recipients or []):
        await create_audit_log(db=db, entity_type="tenant", entity_id=t.id, action="digest_recipients_changed",
                               tenant_id=t.id, user_id=current_user.id,
                               changes={"from": row.digest_recipients or [], "to": new})
        row.digest_recipients = new
        await db.commit()
    return {"recipients": new}
