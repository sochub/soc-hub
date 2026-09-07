import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy import select

from app.worker import celery_app
from app.db.session import AsyncSessionLocal, engine
from app.models.case import Case, CaseStatus
from app.models.tenant import Tenant
from app.models.membership import TenantMembership
from app.models.user import User
from app.services.email_service import send_sla_breach_email
from app.utils.sla import compute_sla_state, load_policy_overrides

logger = logging.getLogger(__name__)

_OPEN_STATUSES = [s for s in CaseStatus if s not in (CaseStatus.RESOLVED, CaseStatus.CLOSED)]


async def _notify_recipients(db, case: Case, breach_type: str) -> None:
    emails = set()

    if case.owner_id:
        owner = (await db.execute(select(User).where(User.id == case.owner_id, User.is_active == True))).scalars().first()  # noqa: E712
        if owner:
            emails.add(owner.email)

    admin_rows = await db.execute(
        select(User.email)
        .join(TenantMembership, TenantMembership.user_id == User.id)
        .where(TenantMembership.tenant_id == case.tenant_id, TenantMembership.role == "admin", User.is_active == True)  # noqa: E712
    )
    emails.update(admin_rows.scalars().all())

    for email in emails:
        await send_sla_breach_email(email, case, breach_type)


async def _check_tenant(db, tenant_id: int) -> None:
    policy_overrides = await load_policy_overrides(db, tenant_id)
    cases = (
        await db.execute(
            select(Case).where(Case.tenant_id == tenant_id, Case.status.in_(_OPEN_STATUSES))
        )
    ).scalars().all()

    for case in cases:
        state = compute_sla_state(case, policy_overrides)
        now = datetime.now(timezone.utc)

        if state["response_status"] == "breached" and case.sla_response_breach_notified_at is None:
            await _notify_recipients(db, case, "response")
            case.sla_response_breach_notified_at = now

        if state["resolution_status"] == "breached" and case.sla_resolution_breach_notified_at is None:
            await _notify_recipients(db, case, "resolution")
            case.sla_resolution_breach_notified_at = now

    await db.commit()


async def _run() -> None:
    try:
        async with AsyncSessionLocal() as db:
            tenant_ids = (await db.execute(select(Tenant.id).where(Tenant.is_active == True))).scalars().all()  # noqa: E712
            for tenant_id in tenant_ids:
                try:
                    await _check_tenant(db, tenant_id)
                except Exception:
                    logger.exception("SLA breach check failed for tenant %d", tenant_id)
                    await db.rollback()
    finally:
        # See app/tasks/triage.py for why this is required — the engine's
        # pooled connections are bound to this call's event loop, and Celery
        # beat reuses the process for the next scheduled run.
        await engine.dispose()


@celery_app.task(acks_late=True, max_retries=1)
def check_sla_breaches_task() -> None:
    asyncio.run(_run())
