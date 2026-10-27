import logging

from app.worker import celery_app

logger = logging.getLogger(__name__)


@celery_app.task
def sync_tickets_task():
    """Every 5 minutes: for each enabled ticketing connection of an active tenant, auto-create tickets and
    sync linked cases both ways (app/services/ticket_sync.py)."""
    from sqlalchemy import select

    from app.db.session import AsyncSessionLocal
    from app.models.tenant import Tenant
    from app.models.ticket_connection import TicketConnection
    from app.services.ticket_sync import sync_tenant
    from app.tasks.runner import _run_async

    async def run():
        async with AsyncSessionLocal() as db:
            ids = (await db.execute(select(TicketConnection.id).join(Tenant, Tenant.id == TicketConnection.tenant_id)
                                    .where(TicketConnection.enabled.is_(True), Tenant.is_active.is_(True)))).scalars().all()
        for cid in ids:
            async with AsyncSessionLocal() as db:
                conn = await db.get(TicketConnection, cid)
                try:
                    r = await sync_tenant(db, conn)
                    if r["created"] or r["synced"] or r["errors"]:
                        logger.info("ticket sync tenant %s: %s", conn.tenant_id, r)
                except Exception:  # a broken connection (bad credentials blob, …) must not stop the others
                    logger.exception("ticket sync failed for connection %s", cid)

    _run_async(run())
