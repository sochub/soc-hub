import logging
from datetime import datetime, timedelta, timezone

from app.worker import celery_app

logger = logging.getLogger(__name__)
WEEK = timedelta(days=7)


@celery_app.task
def send_client_digests_task():
    """Hourly: email the 7-day summary to each active tenant with recipients, at most once a week
    (the first one goes out within the hour after recipients are set)."""
    from sqlalchemy import select, update

    from app.db.session import AsyncSessionLocal
    from app.models.tenant import Tenant
    from app.services import email_service, portfolio
    from app.tasks.runner import _run_async

    async def run():
        now = datetime.now(timezone.utc)
        async with AsyncSessionLocal() as db:
            tenant_ids = (await db.execute(select(Tenant.id).where(Tenant.is_active.is_(True)))).scalars().all()
            for tid in tenant_ids:
                # Claim before sending: lock the row (SKIP LOCKED: another run has it), stamp, COMMIT, then email.
                # A crash mid-send never re-sends the week's digest; a run that overlaps this one skips the tenant.
                t = (await db.execute(select(Tenant).where(Tenant.id == tid, Tenant.is_active.is_(True))
                                      .with_for_update(skip_locked=True)
                                      .execution_options(populate_existing=True))).scalars().first()
                if t is None or not t.digest_recipients or (
                        t.digest_last_sent_at and now - t.digest_last_sent_at < WEEK):
                    await db.rollback()
                    continue
                previous, t.digest_last_sent_at = t.digest_last_sent_at, now
                d = await portfolio.digest(db, t, 7)
                body = portfolio.digest_html(d)
                name, recipients = t.name, list(t.digest_recipients)
                await db.commit()
                sent = [r for r in recipients
                        if email_service._send_email(r, f"{name}: weekly security summary", body, log_recipient=False)]
                if not sent and email_service._smtp_configured():
                    # Nothing went out (SMTP down): release the claim so next hour retries. Unconfigured SMTP
                    # keeps the stamp, so it doesn't retry every hour.
                    await db.execute(update(Tenant).where(Tenant.id == tid, Tenant.digest_last_sent_at == now)
                                     .values(digest_last_sent_at=previous))
                    await db.commit()
                logger.info("client digest tenant=%s sent=%d/%d", tid, len(sent), len(recipients))

    _run_async(run())
