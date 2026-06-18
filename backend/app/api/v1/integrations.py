from typing import Any, List
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from pydantic import BaseModel

from app.api import deps
from app.models.case import Case
from app.models.user import User
from app.models.webhook import Webhook
from app.tasks.jira import create_jira_ticket_task
from app.api.v1.tenants import generate_webhook_key

router = APIRouter()

class JiraSyncRequest(BaseModel):
    case_id: int

@router.post("/jira/sync", status_code=202)
async def sync_jira_ticket(
    *,
    db: AsyncSession = Depends(deps.get_db),
    sync_in: JiraSyncRequest,
    current_user: User = Depends(deps.require_analyst_or_above),
    tenant_id: int = Depends(deps.get_effective_tenant_id),
) -> Any:
    """Trigger a background task to create/sync a Jira ticket for a case.

    The case is loaded and verified to belong to the caller's tenant; the ticket
    title/description are sourced from the case (not from caller input) to avoid
    cross-tenant references and content injection.
    """
    case = (await db.execute(
        select(Case).where(Case.id == sync_in.case_id, Case.tenant_id == tenant_id)
    )).scalars().first()
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")

    create_jira_ticket_task.delay(case.id, case.title, case.description or "", tenant_id)
    return {"message": "Sync task started"}


class WebhookCreate(BaseModel):
    name: str


class WebhookOut(BaseModel):
    id: int
    name: str
    api_key: str
    created_at: Any
    class Config:
        from_attributes = True


@router.get("/webhooks", response_model=List[WebhookOut])
async def list_webhooks(
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.require_admin),
    tenant_id: int = Depends(deps.get_effective_tenant_id),
) -> Any:
    """List the active tenant's ingestion webhooks. Admin only."""
    result = await db.execute(
        select(Webhook).where(Webhook.tenant_id == tenant_id).order_by(Webhook.id)
    )
    return result.scalars().all()


@router.post("/webhooks", response_model=WebhookOut, status_code=201)
async def create_webhook(
    *,
    db: AsyncSession = Depends(deps.get_db),
    webhook_in: WebhookCreate,
    current_user: User = Depends(deps.require_admin),
    tenant_id: int = Depends(deps.get_effective_tenant_id),
) -> Any:
    """Create a named webhook for the active tenant. Admin only."""
    webhook = Webhook(tenant_id=tenant_id, name=webhook_in.name, api_key=generate_webhook_key())
    db.add(webhook)
    await db.commit()
    await db.refresh(webhook)
    return webhook


@router.delete("/webhooks/{webhook_id}", status_code=204)
async def revoke_webhook(
    *,
    db: AsyncSession = Depends(deps.get_db),
    webhook_id: int,
    current_user: User = Depends(deps.require_admin),
    tenant_id: int = Depends(deps.get_effective_tenant_id),
) -> None:
    """Revoke (hard-delete) a webhook. Its key stops working immediately."""
    result = await db.execute(
        select(Webhook).where(Webhook.id == webhook_id, Webhook.tenant_id == tenant_id)
    )
    webhook = result.scalars().first()
    if not webhook:
        raise HTTPException(status_code=404, detail="Webhook not found")
    await db.delete(webhook)
    await db.commit()
