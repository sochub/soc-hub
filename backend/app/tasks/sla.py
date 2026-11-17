import asyncio
import logging
import random
from datetime import datetime, timezone
from types import SimpleNamespace

from celery.exceptions import SoftTimeLimitExceeded
from sqlalchemy import or_, select

from app.worker import celery_app
from app.db.session import AsyncSessionLocal, engine
from app.models.case import Case, CaseStatus
from app.models.tenant import Tenant
from app.models.membership import TenantMembership
from app.models.user import User
from app.notifications import service as notify
from app.services.email_service import send_sla_breach_email
from app.utils.sla import compute_sla_state, load_policy_overrides

logger = logging.getLogger(__name__)

_OPEN_STATUSES = [s for s in CaseStatus if s not in (CaseStatus.RESOLVED, CaseStatus.CLOSED)]
_BREACHES = (("response", "sla_response_breach_notified_at", "Response SLA"),
             ("resolution", "sla_resolution_breach_notified_at", "Resolution SLA"))


def _due(case: Case, state: dict) -> list:
    """The breach kinds that are breached and not yet notified."""
    return [(kind, col, label) for kind, col, label in _BREACHES
            if state[f"{kind}_status"] == "breached" and getattr(case, col) is None]


async def _admin_emails(db, tenant_id: int, exclude_user_ids: set) -> list:
    """Active tenant admins, minus the users the in-app notification (and its delivery) already reaches."""
    rows = (await db.execute(
        select(User.id, User.email)
        .join(TenantMembership, TenantMembership.user_id == User.id)
        .where(TenantMembership.tenant_id == tenant_id, TenantMembership.role == "admin", User.is_active == True)  # noqa: E712
    )).all()
    return sorted({email for uid, email in rows if uid not in exclude_user_ids})


async def _claim(db, case_id: int, policy_overrides) -> list:
    """Lock one case (SKIP LOCKED: another sweep has it), stamp its due breaches, write the owner/follower
    notifications, and COMMIT before anything is sent: a crash after this never re-notifies, a crash before it
    never notified. Returns [(kind, label, admin_emails)] to send after the commit."""
    case = (await db.execute(
        select(Case).where(Case.id == case_id, Case.status.in_(_OPEN_STATUSES))
        .with_for_update(skip_locked=True).execution_options(populate_existing=True))).scalars().first()
    if case is None:
        await db.rollback()
        return []
    due = _due(case, compute_sla_state(case, policy_overrides))
    now = datetime.now(timezone.utc)
    out = []
    for kind, col, label in due:
        setattr(case, col, now)
        # Owner + followers: in-app notification, delivered (Slack DM / email) after commit by its own task.
        reached = {n.user_id for n in await notify.on_sla_breach(db, case, kind)}
        out.append((kind, label, await _admin_emails(db, case.tenant_id, reached)))
    snapshot = SimpleNamespace(id=case.id, title=case.title, severity=case.severity)
    await db.commit()
    return [(snapshot, kind, label, emails) for kind, label, emails in out]


async def _check_tenant(db, tenant_id: int) -> None:
    policy_overrides = await load_policy_overrides(db, tenant_id)
    candidates = (await db.execute(
        select(Case).where(Case.tenant_id == tenant_id, Case.status.in_(_OPEN_STATUSES),
                           or_(Case.sla_response_breach_notified_at.is_(None),
                               Case.sla_resolution_breach_notified_at.is_(None)))
    )).scalars().all()
    due_ids = [c.id for c in candidates if _due(c, compute_sla_state(c, policy_overrides))]
    await db.rollback()  # end the read transaction; each claim runs in its own

    from app.tasks.slack import slack_case_event_task  # Slack responder alerts (after commit)
    for case_id in due_ids:
        for case, kind, label, emails in await _claim(db, case_id, policy_overrides):
            if emails:  # SMTP runs in its own task, never inside the sweep
                send_sla_breach_emails_task.delay(case.id, case.title, getattr(case.severity, "value", case.severity),
                                                  kind, emails)
            slack_case_event_task.delay(tenant_id, "sla_breach", case.id, {"label": label})


async def _run() -> None:
    try:
        async with AsyncSessionLocal() as db:
            tenant_ids = list((await db.execute(select(Tenant.id).where(Tenant.is_active == True))).scalars().all())  # noqa: E712
            random.shuffle(tenant_ids)  # if a run is cut short, the next one doesn't start with the same tenant
            for tenant_id in tenant_ids:
                try:
                    await _check_tenant(db, tenant_id)
                except SoftTimeLimitExceeded:
                    raise  # the task is out of time: stop, don't carry on to the next tenant
                except Exception:
                    logger.exception("SLA breach check failed for tenant %d", tenant_id)
                    await db.rollback()
    finally:
        # See app/tasks/triage.py for why this is required — the engine's
        # pooled connections are bound to this call's event loop, and Celery
        # beat reuses the process for the next scheduled run.
        await engine.dispose()


# Sweeps every tenant's open cases: scales with the whole deployment. Emails go out from their own task.
@celery_app.task(acks_late=True, max_retries=1, soft_time_limit=270, time_limit=290)
def check_sla_breaches_task() -> None:
    asyncio.run(_run())


@celery_app.task(acks_late=True, soft_time_limit=120, time_limit=150)
def send_sla_breach_emails_task(case_id: int, title: str, severity: str, breach_type: str, emails: list) -> None:
    """Email tenant admins about a breach the sweep already stamped (at most once per breach: no retry)."""
    case = SimpleNamespace(id=case_id, title=title, severity=severity)

    async def send():
        for email in emails:
            await send_sla_breach_email(email, case, breach_type)
    asyncio.run(send())
