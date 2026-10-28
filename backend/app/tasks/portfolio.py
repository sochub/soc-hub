import logging
from datetime import datetime, timedelta, timezone

from app.worker import celery_app

logger = logging.getLogger(__name__)
WEEK = timedelta(days=7)


@celery_app.task
def send_client_digests_task():
    """Hourly: email the 7-day summary to each active tenant with recipients, at most once a week
    (the first one goes out within the hour after recipients are set)."""
    from sqlalchemy import select

    from app.db.session import AsyncSessionLocal
    from app.models.tenant import Tenant
    from app.services import email_service, portfolio
    from app.tasks.runner import _run_async

    async def run():
        now = datetime.now(timezone.utc)
        async with AsyncSessionLocal() as db:
            tenants = (await db.execute(select(Tenant).where(Tenant.is_active.is_(True)))).scalars().all()
            for t in tenants:
                if not t.digest_recipients or (t.digest_last_sent_at and now - t.digest_last_sent_at < WEEK):
                    continue
                d = await portfolio.digest(db, t, 7)
                body = portfolio.digest_html(d)
                sent = [r for r in t.digest_recipients
                        if email_service._send_email(r, f"{t.name}: weekly security summary", body, log_recipient=False)]
                if sent or not email_service._smtp_configured():  # unconfigured SMTP: don't retry every hour
                    t.digest_last_sent_at = now
                    await db.commit()
                logger.info("client digest tenant=%s sent=%d/%d", t.id, len(sent), len(t.digest_recipients))

    _run_async(run())
