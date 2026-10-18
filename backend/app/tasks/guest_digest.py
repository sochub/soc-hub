import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy import select, update

from app.db.session import AsyncSessionLocal, engine
from app.guests import notify as gnotify
from app.guests.access import domain_allowed
from app.models.case import Case
from app.models.case_guest import CaseGuest, GuestNotification
from app.models.tenant import Tenant
from app.services import email_service
from app.services.email_service import send_guest_digest_email
from app.worker import celery_app

logger = logging.getLogger(__name__)


async def send_digest(grant_id: int) -> bool:
    gnotify.clear_pending(grant_id)  # first: events from now on schedule the next digest
    async with AsyncSessionLocal() as db:
        row = (await db.execute(select(CaseGuest, Tenant, Case)
                                .join(Tenant, Tenant.id == CaseGuest.tenant_id)
                                .join(Case, Case.id == CaseGuest.case_id)
                                .where(CaseGuest.id == grant_id))).first()
        pending = (await db.execute(select(GuestNotification).where(
            GuestNotification.case_guest_id == grant_id, GuestNotification.sent_at.is_(None))
            .order_by(GuestNotification.created_at, GuestNotification.id))).scalars().all()
        if not pending:
            return False
        sent = False
        if row is not None:
            g, t, c = row
            now = datetime.now(timezone.utc)
            active = g.revoked_at is None and g.expires_at > now and t.is_active and domain_allowed(t, g.email)
            if active:
                from app.api.v1.case_guests import portal_link
                link = await portal_link(db, g, t)
                sent = send_guest_digest_email(g.email, tenant_name=t.name, case_id=c.id, case_title=c.title,
                                               summaries=[p.summary for p in pending], link=link)
                if not sent:
                    logger.warning("guest digest not sent grant=%s", grant_id)
                    if email_service._smtp_configured():  # transient failure: re-arm (unconfigured would loop)
                        gnotify._schedule(grant_id)
                    return False  # keep rows; the next event retries
        await db.execute(update(GuestNotification).where(GuestNotification.id.in_([p.id for p in pending]))
                         .values(sent_at=datetime.now(timezone.utc)))
        await db.commit()
        return sent


async def _run(grant_id: int) -> None:
    try:
        await send_digest(grant_id)
    finally:
        await engine.dispose()


@celery_app.task(acks_late=True)
def send_guest_digest_task(grant_id: int):
    asyncio.run(_run(grant_id))
