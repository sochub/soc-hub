from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from app.models.case import Case
from app.models.notification import Notification
from app.models.user import User
from app.schemas.notification import NotificationOut, ReadAllOut, UnreadCount

router = APIRouter()


def _actor(full_name: Optional[str], email: Optional[str]) -> Optional[str]:
    """full_name, else email local part; never a full email."""
    if full_name:
        return full_name
    if email:
        return email.split("@")[0] or None
    return None


def _mine(user: User, tenant_id: int):
    return (Notification.user_id == user.id, Notification.tenant_id == tenant_id)


@router.get("/", response_model=List[NotificationOut])
async def list_notifications(
    unread_only: bool = False,
    limit: int = Query(50, ge=1, le=100),
    before_id: Optional[int] = None,
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
    tenant_id: int = Depends(deps.get_effective_tenant_id),
):
    q = (select(Notification, Case.title, User.full_name, User.email)
         .join(Case, Case.id == Notification.case_id)
         .outerjoin(User, User.id == Notification.actor_id)
         .where(*_mine(current_user, tenant_id)))
    if unread_only:
        q = q.where(Notification.read_at.is_(None))
    if before_id is not None:
        q = q.where(Notification.id < before_id)
    rows = (await db.execute(q.order_by(Notification.id.desc()).limit(limit))).all()
    return [NotificationOut(
        id=n.id, type=n.type, summary=n.summary, case_id=n.case_id, case_title=title,
        actor_name=_actor(fn, em) if n.actor_id else None,
        timeline_event_id=n.timeline_event_id, read_at=n.read_at, created_at=n.created_at,
    ) for n, title, fn, em in rows]


@router.get("/unread-count", response_model=UnreadCount)
async def unread_count(
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
    tenant_id: int = Depends(deps.get_effective_tenant_id),
):
    n = (await db.execute(select(func.count()).select_from(Notification).where(
        *_mine(current_user, tenant_id), Notification.read_at.is_(None)))).scalar() or 0
    return UnreadCount(count=n)


@router.post("/read-all", response_model=ReadAllOut)
async def read_all(
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
    tenant_id: int = Depends(deps.get_effective_tenant_id),
):
    res = await db.execute(update(Notification).where(
        *_mine(current_user, tenant_id), Notification.read_at.is_(None)
    ).values(read_at=datetime.now(timezone.utc)))
    await db.commit()
    return ReadAllOut(updated=res.rowcount or 0)


@router.post("/{notification_id}/read", status_code=204)
async def mark_read(
    notification_id: int,
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
    tenant_id: int = Depends(deps.get_effective_tenant_id),
):
    n = (await db.execute(select(Notification).where(
        Notification.id == notification_id, *_mine(current_user, tenant_id)))).scalars().first()
    if not n:
        raise HTTPException(status_code=404, detail="Notification not found")
    if n.read_at is None:
        n.read_at = datetime.now(timezone.utc)
        await db.commit()
    return Response(status_code=204)
