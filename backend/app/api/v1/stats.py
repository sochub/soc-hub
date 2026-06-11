from typing import Any
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import func, cast, Date, text
from sqlalchemy.future import select

from app.api import deps
from app.models.case import Case, CaseStatus
from app.models.artifact import Artifact
from app.models.case_artifact import CaseArtifact
from app.models.ioc import IOC
from app.models.user import User

router = APIRouter()

_OPEN_STATUSES = [CaseStatus.NEW, CaseStatus.OPEN, CaseStatus.IN_PROGRESS, CaseStatus.PENDING]


def _this_monday() -> datetime:
    today = datetime.now(timezone.utc).date()
    monday = today - timedelta(days=today.weekday())
    return datetime(monday.year, monday.month, monday.day, tzinfo=timezone.utc)


@router.get("/")
async def get_stats(
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
    tenant_id: int = Depends(deps.get_effective_tenant_id),
) -> Any:
    """Return all dashboard statistics for the tenant."""

    # --- scalar counts ---
    total_result = await db.execute(
        select(func.count(Case.id)).where(Case.tenant_id == tenant_id)
    )
    total_cases = total_result.scalar() or 0

    critical_result = await db.execute(
        select(func.count(Case.id)).where(
            Case.tenant_id == tenant_id,
            Case.severity == "critical",
        )
    )
    critical_cases = critical_result.scalar() or 0

    open_result = await db.execute(
        select(func.count(Case.id)).where(
            Case.tenant_id == tenant_id,
            Case.status == CaseStatus.OPEN,
        )
    )
    open_cases = open_result.scalar() or 0

    resolved_result = await db.execute(
        select(func.count(Case.id)).where(
            Case.tenant_id == tenant_id,
            Case.status.in_([CaseStatus.RESOLVED, CaseStatus.CLOSED]),
        )
    )
    resolved_count = resolved_result.scalar() or 0
    resolution_rate = round((resolved_count / total_cases) * 100) if total_cases > 0 else 0

    # --- MTTR ---
    mttr_result = await db.execute(
        select(
            func.avg(
                func.extract("epoch", Case.resolved_at - Case.created_at) / 3600
            )
        ).where(
            Case.tenant_id == tenant_id,
            Case.resolved_at.isnot(None),
            Case.status.in_([CaseStatus.RESOLVED, CaseStatus.CLOSED]),
        )
    )
    mttr_hours_raw = mttr_result.scalar()
    mttr_hours = round(float(mttr_hours_raw), 1) if mttr_hours_raw is not None else None

    # --- weekly trend ---
    this_monday = _this_monday()
    last_monday = this_monday - timedelta(days=7)

    this_week_result = await db.execute(
        select(func.count(Case.id)).where(
            Case.tenant_id == tenant_id,
            Case.created_at >= this_monday,
        )
    )
    cases_this_week = this_week_result.scalar() or 0

    last_week_result = await db.execute(
        select(func.count(Case.id)).where(
            Case.tenant_id == tenant_id,
            Case.created_at >= last_monday,
            Case.created_at < this_monday,
        )
    )
    cases_last_week = last_week_result.scalar() or 0

    # --- active IOC count ---
    ioc_count_result = await db.execute(
        select(func.count(IOC.id)).where(
            IOC.tenant_id == tenant_id,
            IOC.status == "active",
        )
    )
    active_ioc_count = ioc_count_result.scalar() or 0

    # --- cases over time (last 30 days) ---
    thirty_days_ago = datetime.now(timezone.utc) - timedelta(days=30)
    cases_over_time_result = await db.execute(
        select(
            cast(Case.created_at, Date).label("date"),
            func.count(Case.id).label("count"),
        )
        .where(
            Case.tenant_id == tenant_id,
            Case.created_at >= thirty_days_ago,
        )
        .group_by(cast(Case.created_at, Date))
        .order_by(cast(Case.created_at, Date))
    )
    cases_over_time = [
        {"date": str(row.date), "count": row.count}
        for row in cases_over_time_result
    ]

    # --- cases by severity ---
    severity_result = await db.execute(
        select(Case.severity, func.count(Case.id).label("count"))
        .where(Case.tenant_id == tenant_id)
        .group_by(Case.severity)
    )
    cases_by_severity = [
        {"severity": str(row.severity.value if hasattr(row.severity, "value") else row.severity), "count": row.count}
        for row in severity_result
    ]

    # --- cases by status ---
    status_result = await db.execute(
        select(Case.status, func.count(Case.id).label("count"))
        .where(Case.tenant_id == tenant_id)
        .group_by(Case.status)
    )
    cases_by_status = [
        {"status": str(row.status.value if hasattr(row.status, "value") else row.status), "count": row.count}
        for row in status_result
    ]

    # --- IOCs by type ---
    ioc_type_result = await db.execute(
        select(IOC.ioc_type, func.count(IOC.id).label("count"))
        .where(IOC.tenant_id == tenant_id)
        .group_by(IOC.ioc_type)
    )
    iocs_by_type = [
        {"ioc_type": row.ioc_type, "count": row.count}
        for row in ioc_type_result
    ]

    # --- resolved over time (last 30 days, by resolved_at) ---
    resolved_over_time_result = await db.execute(
        select(
            cast(Case.resolved_at, Date).label("date"),
            func.count(Case.id).label("count"),
        )
        .where(
            Case.tenant_id == tenant_id,
            Case.resolved_at.isnot(None),
            Case.resolved_at >= thirty_days_ago,
        )
        .group_by(cast(Case.resolved_at, Date))
        .order_by(cast(Case.resolved_at, Date))
    )
    resolved_over_time = [
        {"date": str(row.date), "count": row.count} for row in resolved_over_time_result
    ]

    # --- aging buckets for open work (by age in days) ---
    open_created_result = await db.execute(
        select(Case.created_at).where(
            Case.tenant_id == tenant_id, Case.status.in_(_OPEN_STATUSES)
        )
    )
    now = datetime.now(timezone.utc)
    buckets = {"<1d": 0, "1-3d": 0, "3-7d": 0, "7-30d": 0, ">30d": 0}
    oldest_open_days = 0
    for (created,) in open_created_result.all():
        if created is None:
            continue
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        age_days = (now - created).total_seconds() / 86400
        oldest_open_days = max(oldest_open_days, int(age_days))
        if age_days < 1:
            buckets["<1d"] += 1
        elif age_days < 3:
            buckets["1-3d"] += 1
        elif age_days < 7:
            buckets["3-7d"] += 1
        elif age_days < 30:
            buckets["7-30d"] += 1
        else:
            buckets[">30d"] += 1
    aging_buckets = [{"bucket": k, "count": v} for k, v in buckets.items()]

    # --- severity x status heatmap matrix ---
    matrix_result = await db.execute(
        select(Case.severity, Case.status, func.count(Case.id).label("count"))
        .where(Case.tenant_id == tenant_id)
        .group_by(Case.severity, Case.status)
    )
    severity_status_matrix = [
        {
            "severity": s.value if hasattr(s, "value") else str(s),
            "status": st.value if hasattr(st, "value") else str(st),
            "count": c,
        }
        for s, st, c in matrix_result.all()
    ]

    # --- artifact insights ---
    total_artifacts = (
        await db.execute(select(func.count(Artifact.id)).where(Artifact.tenant_id == tenant_id))
    ).scalar() or 0
    total_iocs = (
        await db.execute(select(func.count(IOC.id)).where(IOC.tenant_id == tenant_id))
    ).scalar() or 0

    artifacts_by_type_result = await db.execute(
        select(Artifact.artifact_type, func.count(Artifact.id).label("count"))
        .where(Artifact.tenant_id == tenant_id)
        .group_by(Artifact.artifact_type)
    )
    artifacts_by_type = [
        {"artifact_type": (t.value if hasattr(t, "value") else str(t)), "count": c}
        for t, c in artifacts_by_type_result.all()
    ]

    top_artifacts_result = await db.execute(
        select(
            Artifact.value,
            Artifact.artifact_type,
            func.count(CaseArtifact.id).label("case_count"),
        )
        .join(CaseArtifact, CaseArtifact.artifact_id == Artifact.id)
        .where(Artifact.tenant_id == tenant_id)
        .group_by(Artifact.id, Artifact.value, Artifact.artifact_type)
        .order_by(func.count(CaseArtifact.id).desc())
        .limit(6)
    )
    top_artifacts = [
        {
            "value": v,
            "artifact_type": (t.value if hasattr(t, "value") else str(t)),
            "case_count": c,
        }
        for v, t, c in top_artifacts_result.all()
    ]

    return {
        "total_cases": total_cases,
        "critical_cases": critical_cases,
        "open_cases": open_cases,
        "resolution_rate": resolution_rate,
        "mttr_hours": mttr_hours,
        "cases_this_week": cases_this_week,
        "cases_last_week": cases_last_week,
        "active_ioc_count": active_ioc_count,
        "cases_over_time": cases_over_time,
        "resolved_over_time": resolved_over_time,
        "cases_by_severity": cases_by_severity,
        "cases_by_status": cases_by_status,
        "iocs_by_type": iocs_by_type,
        "aging_buckets": aging_buckets,
        "oldest_open_days": oldest_open_days,
        "severity_status_matrix": severity_status_matrix,
        "total_artifacts": total_artifacts,
        "total_iocs": total_iocs,
        "artifacts_by_type": artifacts_by_type,
        "top_artifacts": top_artifacts,
    }
