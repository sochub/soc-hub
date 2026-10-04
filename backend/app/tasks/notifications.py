import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, or_

from app.db.session import AsyncSessionLocal, engine
from app.models.notification import Notification
from app.notifications.delivery import TransientDeliveryError, deliver_one
from app.worker import celery_app

logger = logging.getLogger(__name__)


async def _deliver(ids):
    import redis.asyncio as aioredis
    from app.core.config import settings
    r = aioredis.from_url(settings.REDIS_URL)
    retry = []
    try:
        async with AsyncSessionLocal() as db:
            for nid in ids:
                try:
                    await deliver_one(db, nid, r)
                except TransientDeliveryError as e:
                    retry.append((nid, str(e)))
                except Exception as e:  # never let one bad row stop the batch
                    logger.warning("notification delivery failed id=%s reason=%s", nid, type(e).__name__)
    finally:
        await r.aclose()
        await engine.dispose()
    return retry


@celery_app.task(bind=True, acks_late=True, max_retries=3)
def deliver_notifications_task(self, ids):
    retry = asyncio.run(_deliver(ids))
    if retry:
        if self.request.retries >= self.max_retries:
            for nid, reason in retry:
                logger.warning("notification delivery failed id=%s reason=%s", nid, reason)
            return
        raise self.retry(args=[[nid for nid, _ in retry]], countdown=30 * 2 ** self.request.retries)


async def _prune():
    now = datetime.now(timezone.utc)
    try:
        async with AsyncSessionLocal() as db:
            await db.execute(delete(Notification).where(or_(
                Notification.created_at < now - timedelta(days=180),
                (Notification.read_at.isnot(None)) & (Notification.created_at < now - timedelta(days=90)))))
            await db.commit()
    finally:
        await engine.dispose()


@celery_app.task(acks_late=True)
def prune_notifications_task():
    asyncio.run(_prune())
