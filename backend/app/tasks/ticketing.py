import logging

from celery.exceptions import SoftTimeLimitExceeded

from app.worker import celery_app

logger = logging.getLogger(__name__)

SYNC_TIMED_OUT = "Sync ran out of time; the remaining tickets are synced on the next run."


async def _active_connection_ids():
    from sqlalchemy import select

    from app.db.session import AsyncSessionLocal
    from app.models.tenant import Tenant
    from app.models.ticket_connection import TicketConnection

    async with AsyncSessionLocal() as db:
        return (await db.execute(select(TicketConnection.id).join(Tenant, Tenant.id == TicketConnection.tenant_id)
                                 .where(TicketConnection.enabled.is_(True), Tenant.is_active.is_(True))
                                 .order_by(TicketConnection.last_synced_at.asc().nullsfirst(), TicketConnection.id)
                                 )).scalars().all()


@celery_app.task
def sync_tickets_task():
    """Every 5 minutes: fan out one sync task per enabled ticketing connection of an active tenant, so one slow or
    large tenant can't use up the time limit of everyone after it (app/services/ticket_sync.py)."""
    from app.tasks.runner import _run_async

    for cid in _run_async(_active_connection_ids()):
        sync_ticket_connection_task.delay(cid)


async def _sync_connection(cid: int) -> None:
    from app.db.session import AsyncSessionLocal
    from app.models.tenant import Tenant
    from app.models.ticket_connection import TicketConnection
    from app.services import ticket_sync

    async with AsyncSessionLocal() as db:
        conn = await db.get(TicketConnection, cid)
        if conn is None or not conn.enabled:
            return
        tenant = await db.get(Tenant, conn.tenant_id)
        if tenant is None or tenant.is_active is not True:  # deactivated after the fan-out
            return
        try:
            r = await ticket_sync.sync_tenant(db, conn)
            if r["created"] or r["synced"] or r["errors"]:
                logger.info("ticket sync tenant %s: %s", conn.tenant_id, r)
        except SoftTimeLimitExceeded:
            raise
        except Exception:  # a broken connection (bad credentials blob, …): logged, the next run retries
            logger.exception("ticket sync failed for connection %s", cid)


async def _record_timeout(cid: int) -> None:
    from datetime import datetime, timezone

    from app.db.session import AsyncSessionLocal
    from app.models.ticket_connection import TicketConnection

    async with AsyncSessionLocal() as db:
        row = await db.get(TicketConnection, cid)
        if row is not None:
            row.last_synced_at = datetime.now(timezone.utc)
            row.last_error = SYNC_TIMED_OUT
            await db.commit()


# Each HTTP call has its own deadline (ticket_sync.HTTP_DEADLINE_SECONDS), and links are synced least-recently
# synced first, so a connection with more tickets than fit in one run still makes progress across runs.
@celery_app.task(soft_time_limit=240, time_limit=270)
def sync_ticket_connection_task(cid: int):
    from app.tasks.runner import _run_async

    try:
        _run_async(_sync_connection(cid))
    except SoftTimeLimitExceeded:
        logger.warning("ticket sync for connection %s hit its time limit", cid)
        _run_async(_record_timeout(cid))
        raise
