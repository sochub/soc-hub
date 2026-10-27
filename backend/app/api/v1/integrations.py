from datetime import datetime
from typing import Any, List, Optional
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from pydantic import BaseModel

from app.api import deps
from app.models.user import User
from app.models.webhook import Webhook, hash_webhook_key, webhook_key_prefix
from app.api.v1.tenants import generate_webhook_key

router = APIRouter()

class WebhookCreate(BaseModel):
    name: str


class WebhookOut(BaseModel):
    id: int
    name: str
    key_prefix: str
    api_key: Optional[str] = None  # only in the create/rotate response; never stored
    needs_rotation: bool = False  # key predates Argon2 hashing and no longer authenticates
    created_at: datetime
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
    key = generate_webhook_key()
    webhook = Webhook(tenant_id=tenant_id, name=webhook_in.name, key_hash=hash_webhook_key(key),
                      key_prefix=webhook_key_prefix(key))
    db.add(webhook)
    await db.commit()
    await db.refresh(webhook)
    out = WebhookOut.model_validate(webhook)
    out.api_key = key
    return out


@router.post("/webhooks/{webhook_id}/rotate", response_model=WebhookOut)
async def rotate_webhook(
    *,
    db: AsyncSession = Depends(deps.get_db),
    webhook_id: int,
    current_user: User = Depends(deps.require_admin),
    tenant_id: int = Depends(deps.get_effective_tenant_id),
) -> Any:
    """Issue a new key for a webhook (same id and name). The old key stops working immediately."""
    webhook = (await db.execute(
        select(Webhook).where(Webhook.id == webhook_id, Webhook.tenant_id == tenant_id)
    )).scalars().first()
    if not webhook:
        raise HTTPException(status_code=404, detail="Webhook not found")
    key = generate_webhook_key()
    webhook.key_hash, webhook.key_prefix = hash_webhook_key(key), webhook_key_prefix(key)
    await db.commit()
    await db.refresh(webhook)
    out = WebhookOut.model_validate(webhook)
    out.api_key = key
    return out


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
