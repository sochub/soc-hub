"""Alert retention: delete old alerts that match a policy and that no case holds.

Every reference to an alert from another table is ON DELETE SET NULL (workflow runs, email artifacts, suppression
rules), so a purge never cascades. Alerts with a case_id are never selected: the case keeps its alert context."""
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.alert_retention import AlertRetentionPolicy
from app.models.case import Alert
from app.services import alert_service
from app.utils.audit import create_audit_log

logger = logging.getLogger(__name__)

BATCH = 1_000
# ponytail: per-policy cap per daily run, so a first run over a huge backlog stays short; the rest goes the next days.
MAX_PER_RUN = 100_000


def _base(tenant_id: int, retain_days: int, statuses: list, now: datetime):
    return select(Alert).where(Alert.tenant_id == tenant_id, Alert.status.in_(statuses),
                               Alert.created_at < now - timedelta(days=retain_days))


async def _eligible(db: AsyncSession, tenant_id: int, scope: dict, now: datetime):
    q = _base(tenant_id, scope["retain_days"], scope["statuses"], now).where(Alert.case_id.is_(None))
    # in_cidr is checked in Python over at most alert_service.CIDR_SCAN rows (newest first) per batch.
    return await alert_service.apply_conditions(db, q, scope["conditions"])


async def preview(db: AsyncSession, tenant_id: int, scope: dict, now: datetime | None = None) -> dict:
    """What the policy would delete right now: count, oldest, a sample (oldest first), and how many matching alerts
    are kept because a case holds them."""
    now = now or datetime.now(timezone.utc)
    q = await _eligible(db, tenant_id, scope, now)
    sub = q.subquery()
    count, oldest = (await db.execute(select(func.count(), func.min(sub.c.created_at)))).one()
    sample = (await db.execute(q.order_by(Alert.created_at, Alert.id).limit(10))).scalars().all()
    held = await alert_service.apply_conditions(
        db, _base(tenant_id, scope["retain_days"], scope["statuses"], now).where(Alert.case_id.is_not(None)),
        scope["conditions"])
    kept = (await db.execute(select(func.count()).select_from(held.subquery()))).scalar_one()
    return {
        "matched": count, "oldest": oldest, "kept_linked_to_case": kept,
        "cutoff": now - timedelta(days=scope["retain_days"]),
        "sample": [{"id": a.id, "title": a.title, "source": a.source, "status": a.status, "created_at": a.created_at}
                   for a in sample],
    }


async def run_policy(db: AsyncSession, policy: AlertRetentionPolicy, now: datetime | None = None, *,
                     trigger: str = "scheduled", user_id: int | None = None) -> int:
    """Delete the policy's eligible alerts in committed batches; returns how many were deleted. Raises on failure
    (use ``run_safely`` to record it)."""
    now = now or datetime.now(timezone.utc)
    scope = {"retain_days": policy.retain_days, "statuses": policy.statuses, "conditions": policy.conditions or []}
    deleted = 0
    while deleted < MAX_PER_RUN:
        q = await _eligible(db, policy.tenant_id, scope, now)
        ids = list((await db.execute(q.with_only_columns(Alert.id).order_by(Alert.id).limit(BATCH))).scalars())
        if not ids:
            break
        # Re-check the guards in the DELETE: an alert promoted or linked since the select is left alone.
        res = await db.execute(delete(Alert).where(
            Alert.id.in_(ids), Alert.tenant_id == policy.tenant_id, Alert.case_id.is_(None),
            Alert.status.in_(policy.statuses)))
        deleted += res.rowcount or 0
        await db.commit()
        if len(ids) < BATCH:
            break
    _stamp(policy, now, trigger, user_id, error=None)
    policy.last_deleted = deleted
    policy.total_deleted = (policy.total_deleted or 0) + deleted
    if deleted or trigger == "manual":  # a manual run is always on record; a scheduled one when it deleted something
        await create_audit_log(db, "alert_retention_policy", policy.id, "alerts_purged", policy.tenant_id,
                               user_id=user_id,
                               changes={"deleted": deleted, "trigger": trigger, "retain_days": policy.retain_days,
                                        "statuses": policy.statuses, "conditions": policy.conditions},
                               actor=None if user_id else "retention")
    await db.commit()
    return deleted


def _stamp(policy: AlertRetentionPolicy, now: datetime, trigger: str, user_id: int | None, error: str | None) -> None:
    policy.last_run_at, policy.last_run_trigger, policy.last_run_by, policy.last_error = now, trigger, user_id, error


async def run_safely(db: AsyncSession, policy: AlertRetentionPolicy, *, trigger: str = "scheduled",
                     user_id: int | None = None) -> int | None:
    """run_policy, but a failure is recorded on the policy (last_error) instead of raised. Batches committed before
    the failure stay deleted and are not counted. Returns None on failure."""
    now = datetime.now(timezone.utc)
    pid = policy.id
    try:
        return await run_policy(db, policy, now, trigger=trigger, user_id=user_id)
    except Exception as e:
        logger.exception("alert retention policy %s failed", pid)
        await db.rollback()
        policy = await db.get(AlertRetentionPolicy, pid)
        if policy is not None:
            # The class only: the message can carry SQL or driver detail; the worker log has the traceback.
            _stamp(policy, now, trigger, user_id, error=f"Run failed ({type(e).__name__}). See the worker log.")
            policy.last_deleted = 0
            await db.commit()
        return None


async def run_all(*, tenant_ids=None) -> int:
    """Run every enabled policy. One failing policy is recorded and does not stop the others. `tenant_ids` scopes
    the run (tests)."""
    from app.db.session import AsyncSessionLocal

    total = 0
    async with AsyncSessionLocal() as db:
        q = select(AlertRetentionPolicy.id).where(AlertRetentionPolicy.enabled.is_(True))
        if tenant_ids is not None:
            q = q.where(AlertRetentionPolicy.tenant_id.in_(tenant_ids))
        ids = list((await db.execute(q.order_by(AlertRetentionPolicy.id))).scalars())
    for pid in ids:
        async with AsyncSessionLocal() as db:
            policy = await db.get(AlertRetentionPolicy, pid)
            if policy is None or not policy.enabled:
                continue
            total += await run_safely(db, policy) or 0
    return total
