from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.sla_policy import SLAPolicy

# Elapsed-time fraction of the target beyond which an unmet clock is "at risk".
_AT_RISK_THRESHOLD = 0.8

# Used whenever a tenant has no sla_policies row for a severity.
DEFAULT_SLA_MINUTES: Dict[str, Dict[str, Optional[int]]] = {
    "critical": {"response": 15, "resolution": 240},
    "high": {"response": 60, "resolution": 480},
    "medium": {"response": 240, "resolution": 1440},
    "low": {"response": 480, "resolution": 4320},
    "info": {"response": None, "resolution": None},
}

_STATE_RANK = {"not_tracked": 0, "met": 1, "on_track": 2, "at_risk": 3, "breached": 4}

PolicyOverrides = Dict[str, Dict[str, Optional[int]]]


async def load_policy_overrides(db: AsyncSession, tenant_id: int) -> PolicyOverrides:
    """A tenant's sla_policies rows, keyed by lowercase severity."""
    rows = (
        await db.execute(select(SLAPolicy).where(SLAPolicy.tenant_id == tenant_id))
    ).scalars().all()
    return {
        r.severity: {"response": r.response_target_minutes, "resolution": r.resolution_target_minutes}
        for r in rows
    }


def get_target_minutes(severity: str, policy_overrides: PolicyOverrides) -> Dict[str, Optional[int]]:
    """Effective response/resolution targets for a severity: the tenant's
    override if one exists, else the hardcoded default."""
    sev = str(severity).lower()
    return policy_overrides.get(sev) or DEFAULT_SLA_MINUTES.get(sev, {"response": None, "resolution": None})


def _due_at(start: datetime, target_minutes: Optional[int]) -> Optional[datetime]:
    if target_minutes is None:
        return None
    return start + timedelta(minutes=target_minutes)


def _clock_state(elapsed_minutes: float, target_minutes: Optional[int], stopped: bool) -> str:
    if target_minutes is None:
        return "not_tracked"
    if stopped:
        return "met" if elapsed_minutes <= target_minutes else "breached"
    if elapsed_minutes > target_minutes:
        return "breached"
    if elapsed_minutes > target_minutes * _AT_RISK_THRESHOLD:
        return "at_risk"
    return "on_track"


def compute_sla_state(case: Any, policy_overrides: PolicyOverrides) -> Dict[str, Any]:
    """Response/resolution SLA status for one case. `case` needs: severity,
    created_at, acknowledged_at, resolved_at. The single source of truth for
    "breached" — used by case serialization, the dashboard stat, and the
    breach-notification sweep alike."""
    severity = case.severity.value if hasattr(case.severity, "value") else str(case.severity)
    targets = get_target_minutes(severity, policy_overrides)
    now = datetime.now(timezone.utc)
    created_at = case.created_at

    response_end = case.acknowledged_at or now
    response_elapsed = (response_end - created_at).total_seconds() / 60
    response_status = _clock_state(response_elapsed, targets["response"], stopped=case.acknowledged_at is not None)

    resolution_end = case.resolved_at or now
    resolution_elapsed = (resolution_end - created_at).total_seconds() / 60
    resolution_status = _clock_state(resolution_elapsed, targets["resolution"], stopped=case.resolved_at is not None)

    overall_status = max([response_status, resolution_status], key=lambda s: _STATE_RANK[s])

    return {
        "response_target_minutes": targets["response"],
        "response_status": response_status,
        "response_due_at": _due_at(created_at, targets["response"]) if case.acknowledged_at is None else None,
        "resolution_target_minutes": targets["resolution"],
        "resolution_status": resolution_status,
        "resolution_due_at": _due_at(created_at, targets["resolution"]) if case.resolved_at is None else None,
        "overall_status": overall_status,
    }
