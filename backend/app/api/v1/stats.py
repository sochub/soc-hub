import csv
import io
import statistics
from collections import Counter, defaultdict
from datetime import date
from typing import Any, Literal, Optional
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import func, cast, Date, or_, text
from sqlalchemy.future import select

from app.api import deps
from app.models.audit_log import AuditLog
from app.models.case import CASE_OUTCOMES, Case, CaseStatus, TimelineEvent
from app.models.escalation import CaseEscalation
from app.models.membership import TenantMembership
from app.models.artifact import Artifact
from app.models.case_artifact import CaseArtifact
from app.models.ioc import IOC
from app.models.user import User
from app.models.case_time_entry import CaseTimeEntry
from app.api.v1.audit_logs import _csv_safe  # reused CSV formula-injection guard
from app.utils.sla import compute_sla_state, load_policy_overrides
from app.services.artifact_types import not_private

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
        await db.execute(select(func.count(Artifact.id)).where(Artifact.tenant_id == tenant_id, not_private()))
    ).scalar() or 0
    total_iocs = (
        await db.execute(select(func.count(IOC.id)).where(IOC.tenant_id == tenant_id))
    ).scalar() or 0

    artifacts_by_type_result = await db.execute(
        select(Artifact.artifact_type, func.count(Artifact.id).label("count"))
        .where(Artifact.tenant_id == tenant_id, not_private())
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
        .where(Artifact.tenant_id == tenant_id, not_private())
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

    open_case_rows = (
        await db.execute(
            select(Case).where(Case.tenant_id == tenant_id, Case.status.in_(_OPEN_STATUSES))
        )
    ).scalars().all()
    if open_case_rows:
        policy_overrides = await load_policy_overrides(db, tenant_id)
        breached = sum(
            1 for c in open_case_rows
            if compute_sla_state(c, policy_overrides)["overall_status"] == "breached"
        )
        sla_compliance_pct = round(100 * (1 - breached / len(open_case_rows)), 1)
    else:
        sla_compliance_pct = None

    return {
        "total_cases": total_cases,
        "sla_compliance_pct": sla_compliance_pct,
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


_TIME_CSV = ["user_email", "case_id", "case_title", "severity", "seconds"]


@router.get("/time")
async def time_rollup(
    start: Optional[date] = None,
    end: Optional[date] = None,
    format: Literal["json", "csv"] = "json",
    db: AsyncSession = Depends(deps.get_db),
    admin: User = Depends(deps.require_admin),
    tenant_id: int = Depends(deps.get_effective_tenant_id),
) -> Any:
    """Admin rollup of tracked time per user and case (entries starting in [start, end], default last 30 days)."""
    today = datetime.now(timezone.utc).date()
    end = end or today
    start = start or end - timedelta(days=30)
    lo = datetime(start.year, start.month, start.day, tzinfo=timezone.utc)
    hi = datetime(end.year, end.month, end.day, tzinfo=timezone.utc) + timedelta(days=1)
    secs = func.coalesce(func.sum(func.extract("epoch", CaseTimeEntry.ended_at - CaseTimeEntry.started_at)), 0)
    q = (select(User.email, Case.id, Case.title, Case.severity, secs)
         .join(User, User.id == CaseTimeEntry.user_id).join(Case, Case.id == CaseTimeEntry.case_id)
         .where(CaseTimeEntry.tenant_id == tenant_id, CaseTimeEntry.started_at >= lo, CaseTimeEntry.started_at < hi)
         .group_by(User.email, Case.id, Case.title, Case.severity)
         .order_by(User.email, Case.id))
    rows = [{"user_email": em, "case_id": cid, "case_title": title,
             "severity": getattr(sev, "value", sev), "seconds": int(s)}
            for em, cid, title, sev, s in (await db.execute(q)).all()]
    if format == "json":
        return {"rows": rows}
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=_TIME_CSV)
    w.writeheader()

    async def body():
        for r in rows:
            w.writerow({k: _csv_safe(v) for k, v in r.items()})
            if buf.tell() > 64_000:
                yield buf.getvalue()
                buf.seek(0)
                buf.truncate()
        yield buf.getvalue()

    # header is already in buf; body() yields it with the first chunk
    return StreamingResponse(body(), media_type="text/csv", headers={
        "Content-Disposition": f'attachment; filename="time-{start}-{end}.csv"'})


# ---- Analyst workload (admin) ----------------------------------------------------------------------------------
# Snapshot = cases owned right now. Period metrics come from the `change` audit rows that apply_case_update and the
# escalation service write: `owner_id` {from,to} for assignments, `status` {to} for resolve/close. Resolve/close is
# credited to the user who made the change (the audit row's user), not to the owner.

_SNAPSHOT = ("new", "open", "in_progress", "pending")
_WorkloadMetric = Literal["current", "new", "open", "in_progress", "pending", "handled", "resolved", "closed",
                          "sla_breached"]


def _period(start: Optional[date], end: Optional[date]) -> tuple:
    """[lo, hi) in UTC for inclusive dates; defaults to this week (Monday through today)."""
    start = start or _this_monday().date()
    end = end or datetime.now(timezone.utc).date()
    if start > end:
        raise HTTPException(status_code=422, detail="start must be on or before end")
    lo = datetime(start.year, start.month, start.day, tzinfo=timezone.utc)
    hi = datetime(end.year, end.month, end.day, tzinfo=timezone.utc) + timedelta(days=1)
    return start, end, lo, hi


async def _workload(db: AsyncSession, tenant_id: int, lo: datetime, hi: datetime) -> dict:
    """{user_id: {metric: set(case_id)}}; every case id belongs to `tenant_id`."""
    out: dict = defaultdict(lambda: defaultdict(set))
    for cid, owner, st in (await db.execute(select(Case.id, Case.owner_id, Case.status).where(
            Case.tenant_id == tenant_id, Case.owner_id.isnot(None), Case.status.in_(_OPEN_STATUSES)))).all():
        out[owner][st.value].add(cid)
        out[owner]["current"].add(cid)

    # Handled: rebuild each case's ownership spans from its owner_id changes and credit every owner whose span
    # overlaps [lo, hi). The first change's `from` is the owner at creation; no changes = the current owner.
    # Cases already resolved before the period are skipped (resolved_at is cleared on reopen).
    js = cast(AuditLog.changes, JSONB)
    case_q = select(Case.id, Case.owner_id, Case.created_at).where(
        Case.tenant_id == tenant_id, Case.created_at < hi, or_(Case.resolved_at.is_(None), Case.resolved_at >= lo))
    cases = (await db.execute(case_q)).all()
    moves: dict = defaultdict(list)
    for cid, at, ch in (await db.execute(select(AuditLog.entity_id, AuditLog.created_at, AuditLog.changes).where(
            AuditLog.tenant_id == tenant_id, AuditLog.category == "change", AuditLog.entity_type == "case",
            AuditLog.entity_id.in_(select(case_q.subquery().c.id)), js.has_key("owner_id"),
    ).order_by(AuditLog.id))).all():
        moves[cid].append((at, ch["owner_id"].get("from"), ch["owner_id"].get("to")))
    for cid, owner, created in cases:
        seq = moves.get(cid, [])
        spans = [(created, seq[0][1] if seq else owner)] + [(at, to) for at, _, to in seq]
        for i, (begin, uid) in enumerate(spans):
            finish = spans[i + 1][0] if i + 1 < len(spans) else None
            if uid is not None and begin < hi and (finish is None or finish > lo):
                out[uid]["handled"].add(cid)

    # Resolved / closed: whoever made the status change inside the period; the two are counted separately.
    to_status = js["status"]["to"].astext
    for uid, cid, st in (await db.execute(
            select(AuditLog.user_id, AuditLog.entity_id, to_status)
            .join(Case, (Case.id == AuditLog.entity_id) & (Case.tenant_id == tenant_id))
            .where(AuditLog.tenant_id == tenant_id, AuditLog.category == "change", AuditLog.entity_type == "case",
                   AuditLog.user_id.isnot(None), AuditLog.created_at >= lo, AuditLog.created_at < hi,
                   to_status.in_(("resolved", "closed"))))).all():
        out[uid][st].add(cid)

    # SLA breaches: cases each analyst owns now whose response or resolution breach was stamped in the period.
    for cid, owner in (await db.execute(select(Case.id, Case.owner_id).where(
            Case.tenant_id == tenant_id, Case.owner_id.isnot(None), _breached_in(lo, hi)))).all():
        out[owner]["sla_breached"].add(cid)
    return out


def _breached_in(lo: datetime, hi: datetime):
    return or_((Case.sla_response_breach_notified_at >= lo) & (Case.sla_response_breach_notified_at < hi),
               (Case.sla_resolution_breach_notified_at >= lo) & (Case.sla_resolution_breach_notified_at < hi))


def _minutes(a: Optional[datetime], b: Optional[datetime]) -> Optional[float]:
    return (b - a).total_seconds() / 60 if a and b else None


def _median(values) -> Optional[float]:
    vals = [v for v in values if v is not None]
    return round(statistics.median(vals), 1) if vals else None


@router.get("/analyst-workload")
async def analyst_workload(
    start: Optional[date] = None,
    end: Optional[date] = None,
    db: AsyncSession = Depends(deps.get_db),
    admin: User = Depends(deps.require_admin),
    tenant_id: int = Depends(deps.get_effective_tenant_id),
) -> Any:
    """One row per active non-viewer member: current workload snapshot plus cases handled/resolved/closed in
    [start, end] (inclusive dates, default this week)."""
    start, end, lo, hi = _period(start, end)
    data = await _workload(db, tenant_id, lo, hi)
    members = (await db.execute(
        select(User.id, User.email, User.full_name, TenantMembership.role)
        .join(TenantMembership, TenantMembership.user_id == User.id)
        .where(TenantMembership.tenant_id == tenant_id, TenantMembership.role != "viewer", User.is_active.is_(True))
        .order_by(User.email))).all()
    # Median time to resolve (created -> resolved_at) over the cases each analyst resolved or closed in the period.
    done_ids = set().union(*(m.get("resolved", set()) | m.get("closed", set()) for m in data.values()))
    ttr = {}
    if done_ids:
        rows = await db.execute(select(Case.id, Case.created_at, Case.resolved_at).where(
            Case.tenant_id == tenant_id, Case.id.in_(done_ids)))
        ttr = {cid: _minutes(c, r) for cid, c, r in rows.all()}
    analysts = []
    for uid, email, name, role in members:
        m = data.get(uid, {})
        analysts.append({
            "user_id": uid, "email": email, "full_name": name, "role": role,
            "current": {**{s: len(m.get(s, ())) for s in _SNAPSHOT}, "total": len(m.get("current", ()))},
            "handled": len(m.get("handled", ())), "resolved": len(m.get("resolved", ())),
            "closed": len(m.get("closed", ())),
            "median_resolve_minutes": _median(ttr.get(c) for c in m.get("resolved", set()) | m.get("closed", set())),
            "sla_breached": len(m.get("sla_breached", ())),
        })
    return {"start": start, "end": end, "analysts": analysts}


@router.get("/analyst-workload/cases")
async def analyst_workload_cases(
    user_id: int,
    metric: _WorkloadMetric,
    start: Optional[date] = None,
    end: Optional[date] = None,
    db: AsyncSession = Depends(deps.get_db),
    admin: User = Depends(deps.require_admin),
    tenant_id: int = Depends(deps.get_effective_tenant_id),
) -> Any:
    """The cases behind one count in the workload table."""
    start, end, lo, hi = _period(start, end)
    ids = (await _workload(db, tenant_id, lo, hi)).get(user_id, {}).get(metric, set())
    if not ids:
        return {"cases": []}
    rows = (await db.execute(select(Case.id, Case.title, Case.status, Case.severity, Case.owner_id).where(
        Case.tenant_id == tenant_id, Case.id.in_(ids)).order_by(Case.id.desc()))).all()
    return {"cases": [{"id": i, "title": t, "status": s.value, "severity": getattr(v, "value", v), "owner_id": o}
                      for i, t, s, v, o in rows]}


# ---- Team insights (admin): SOC manager dashboard on the Workload page ------------------------------------------
# All dates are UTC. Period KPIs compare [start, end] with the previous period of equal length; "now" figures are a
# live snapshot. Insights are fixed rules (no LLM); a rule that does not fire is left out of the response.

_SEVERITIES = ("critical", "high", "medium", "low", "info")
_SEV_RANK = {s: i for i, s in enumerate(_SEVERITIES)}
_AGING = (("<1d", 1), ("1-3d", 3), ("3-7d", 7), ("7-30d", 30), (">30d", None))
_OUTCOME_LABELS = {o: o.replace("_", " ").capitalize() for o in CASE_OUTCOMES} | {
    "false_positive": "False positive", "true_positive_with_impact": "True positive (impact)",
    "true_positive_without_impact": "True positive (no impact)", "not_set": "Not set"}
OVERLOAD_FACTOR, OVERLOAD_MIN_CASES = 1.5, 3
UNASSIGNED_URGENT_AFTER = timedelta(hours=1)
STALE_AFTER = timedelta(days=7)
FP_SOURCE_MIN_CASES, FP_SOURCE_RATE = 5, 0.5
_INSIGHT_CASES = 10


def _val(v):
    return getattr(v, "value", v)


async def _median_minutes(db: AsyncSession, tenant_id: int, begin, finish, lo: datetime, hi: datetime):
    expr = func.extract("epoch", finish - begin) / 60
    v = (await db.execute(select(func.percentile_cont(0.5).within_group(expr)).where(
        Case.tenant_id == tenant_id, begin.isnot(None), finish >= lo, finish < hi))).scalar()
    return round(float(v), 1) if v is not None else None


async def _period_kpis(db: AsyncSession, tenant_id: int, lo: datetime, hi: datetime) -> dict:
    def count(*where):
        return select(func.count(Case.id)).where(Case.tenant_id == tenant_id, *where)
    created = (await db.execute(count(Case.created_at >= lo, Case.created_at < hi))).scalar() or 0
    closed = (await db.execute(count(Case.resolved_at >= lo, Case.resolved_at < hi))).scalar() or 0
    resp = (await db.execute(count(Case.sla_response_breach_notified_at >= lo,
                                   Case.sla_response_breach_notified_at < hi))).scalar() or 0
    reso = (await db.execute(count(Case.sla_resolution_breach_notified_at >= lo,
                                   Case.sla_resolution_breach_notified_at < hi))).scalar() or 0
    return {
        "created": created, "closed": closed, "sla_breaches": resp + reso,
        "sla_response_breaches": resp, "sla_resolution_breaches": reso,
        "mtta_minutes": await _median_minutes(db, tenant_id, Case.created_at, Case.acknowledged_at, lo, hi),
        "mttr_minutes": await _median_minutes(db, tenant_id, Case.created_at, Case.resolved_at, lo, hi),
    }


def _aging_bucket(age: timedelta) -> str:
    days = age.total_seconds() / 86400
    return next(label for label, limit in _AGING if limit is None or days < limit)


def _case_refs(rows) -> list:
    """Most urgent first (severity, then oldest), capped."""
    rows = sorted(rows, key=lambda r: (_SEV_RANK.get(_val(r.severity), 9), r.created_at))
    return [{"id": r.id, "title": r.title, "severity": _val(r.severity)} for r in rows[:_INSIGHT_CASES]]


def _insight(key: str, level: str, title: str, detail: str, rows=()) -> dict:
    return {"id": key, "level": level, "title": title, "detail": detail, "count": len(rows), "cases": _case_refs(rows)}


@router.get("/team-insights")
async def team_insights(
    start: Optional[date] = None,
    end: Optional[date] = None,
    db: AsyncSession = Depends(deps.get_db),
    admin: User = Depends(deps.require_admin),
    tenant_id: int = Depends(deps.get_effective_tenant_id),
) -> Any:
    """KPIs (period vs previous period), daily created/closed trend, open backlog by analyst and by age, outcome
    mix of cases closed in the period, and rule-based insights. Inclusive UTC dates, default this week."""
    start, end, lo, hi = _period(start, end)
    span = hi - lo
    plo = lo - span
    now = datetime.now(timezone.utc)

    cur, prev = await _period_kpis(db, tenant_id, lo, hi), await _period_kpis(db, tenant_id, plo, lo)
    open_rows = (await db.execute(select(
        Case.id, Case.title, Case.severity, Case.status, Case.owner_id, Case.source, Case.created_at,
        Case.updated_at, Case.acknowledged_at, Case.resolved_at,
    ).where(Case.tenant_id == tenant_id, Case.status.in_(_OPEN_STATUSES)))).all()
    backlog_at_start = (await db.execute(select(func.count(Case.id)).where(
        Case.tenant_id == tenant_id, Case.created_at < lo,
        or_(Case.status.in_(_OPEN_STATUSES), Case.resolved_at >= lo)))).scalar() or 0
    unassigned = [r for r in open_rows if r.owner_id is None]

    def kpi(key):
        return {"value": cur[key], "previous": prev[key]}
    kpis = {
        "open_backlog": {"value": len(open_rows), "previous": backlog_at_start},
        "unassigned_open": {"value": len(unassigned), "previous": None},
        **{k: kpi(k) for k in ("created", "closed", "mtta_minutes", "mttr_minutes", "sla_breaches")},
        "sla_breaches_split": {"response": cur["sla_response_breaches"], "resolution": cur["sla_resolution_breaches"]},
    }

    # Daily created vs closed, every day of the range (zeros included).
    def per_day(col):
        day = func.date(func.timezone("UTC", col))
        return select(day, func.count(Case.id)).where(Case.tenant_id == tenant_id, col >= lo, col < hi).group_by(day)
    created_by_day = dict((await db.execute(per_day(Case.created_at))).all())
    closed_by_day = dict((await db.execute(per_day(Case.resolved_at))).all())
    trend = [{"date": d.isoformat(), "created": created_by_day.get(d, 0), "closed": closed_by_day.get(d, 0)}
             for d in (start + timedelta(days=i) for i in range((end - start).days + 1))]

    # People: active non-viewer members (the team) plus anyone who still owns open work.
    members = {uid: (name or email) for uid, name, email in (await db.execute(
        select(User.id, User.full_name, User.email).join(TenantMembership, TenantMembership.user_id == User.id)
        .where(TenantMembership.tenant_id == tenant_id, TenantMembership.role != "viewer",
               User.is_active.is_(True)))).all()}
    owner_ids = {r.owner_id for r in open_rows if r.owner_id is not None} - set(members)
    names = dict(members)
    if owner_ids:
        names |= {uid: (name or email) for uid, name, email in (await db.execute(
            select(User.id, User.full_name, User.email).where(User.id.in_(owner_ids)))).all()}

    by_owner: dict = defaultdict(list)
    for r in open_rows:
        by_owner[r.owner_id].append(r)
    backlog_by_analyst = []
    for uid, rows in by_owner.items():
        counts = Counter(_val(r.status) for r in rows)
        backlog_by_analyst.append({"user_id": uid, "name": names.get(uid, f"User {uid}") if uid else "Unassigned",
                                   **{s: counts.get(s, 0) for s in _SNAPSHOT}, "total": len(rows)})
    backlog_by_analyst.sort(key=lambda x: (-x["total"], x["name"].lower()))

    aging = {label: Counter() for label, _ in _AGING}
    for r in open_rows:
        aging[_aging_bucket(now - r.created_at)][_val(r.severity)] += 1
    aging_buckets = [{"bucket": b, **{s: c.get(s, 0) for s in _SEVERITIES}, "total": sum(c.values())}
                     for b, c in aging.items()]

    closed_rows = (await db.execute(select(Case.id, Case.title, Case.severity, Case.source, Case.outcome,
                                           Case.created_at).where(
        Case.tenant_id == tenant_id, Case.resolved_at >= lo, Case.resolved_at < hi,
        Case.status.in_([CaseStatus.RESOLVED, CaseStatus.CLOSED])))).all()
    oc = Counter(r.outcome or "not_set" for r in closed_rows)
    outcomes = [{"outcome": o, "label": _OUTCOME_LABELS.get(o, o), "count": oc[o]}
                for o in (*CASE_OUTCOMES, "not_set", *sorted(set(oc) - set(CASE_OUTCOMES) - {"not_set"})) if oc[o]]
    classified = sum(n for o, n in oc.items() if o not in ("unknown", "not_set"))
    fp_rate = round(oc["false_positive"] / classified, 3) if classified else None

    # ---- insights ----
    insights = []
    urgent = [r for r in unassigned
              if _val(r.severity) in ("critical", "high") and now - r.created_at > UNASSIGNED_URGENT_AFTER]
    if urgent:
        insights.append(_insight("unassigned_urgent", "critical", f"{len(urgent)} critical/high case(s) unassigned",
                                 "Open for more than an hour with no owner. Assign them.", urgent))

    policy = await load_policy_overrides(db, tenant_id) if open_rows else {}
    past_sla = [r for r in open_rows if compute_sla_state(r, policy)["overall_status"] == "breached"]
    if past_sla:
        insights.append(_insight("past_sla", "critical", f"{len(past_sla)} open case(s) past SLA",
                                 "Response or resolution target already missed.", past_sla))

    esc_rows = (await db.execute(select(Case.id, Case.title, Case.severity, Case.created_at)
                                 .join(CaseEscalation, CaseEscalation.case_id == Case.id).where(
        CaseEscalation.tenant_id == tenant_id, Case.tenant_id == tenant_id, CaseEscalation.status == "pending",
        CaseEscalation.due_at.isnot(None), CaseEscalation.due_at < now))).all()
    if esc_rows:
        insights.append(_insight("escalations_overdue", "critical", f"{len(esc_rows)} escalation(s) overdue",
                                 "Pending escalations past their accept deadline; nobody has picked them up.",
                                 esc_rows))

    if len(members) >= 2:
        avg = sum(len(by_owner.get(uid, ())) for uid in members) / len(members)
        for uid, name in sorted(members.items(), key=lambda m: -len(by_owner.get(m[0], ()))):
            load = len(by_owner.get(uid, ()))
            if load >= OVERLOAD_MIN_CASES and load > OVERLOAD_FACTOR * avg:
                insights.append(_insight(f"overloaded:{uid}", "warning", f"{name} carries {load} open cases",
                                         f"{load / avg:.1f}x the team average of {avg:.1f}. Consider rebalancing.",
                                         by_owner[uid]))

    last_note = dict((await db.execute(select(TimelineEvent.case_id, func.max(TimelineEvent.created_at))
                                       .join(Case, Case.id == TimelineEvent.case_id)
                                       .where(Case.tenant_id == tenant_id, Case.status.in_(_OPEN_STATUSES))
                                       .group_by(TimelineEvent.case_id))).all()) if open_rows else {}
    stale = [r for r in open_rows
             if now - max(t for t in (r.created_at, r.updated_at, last_note.get(r.id)) if t) > STALE_AFTER]
    if stale:
        insights.append(_insight("stale", "warning", f"{len(stale)} open case(s) with no activity for 7+ days",
                                 "No update or activity entry in a week.", stale))

    if cur["created"] > cur["closed"]:
        insights.append(_insight("backlog_growing", "warning", "Backlog is growing",
                                 f"{cur['created']} cases created vs {cur['closed']} closed in the period "
                                 f"(+{cur['created'] - cur['closed']})."))

    by_source: dict = defaultdict(list)
    for r in closed_rows:
        if r.outcome and r.outcome != "unknown":
            by_source[r.source or "unknown"].append(r)
    for src, rows in sorted(by_source.items()):
        fps = [r for r in rows if r.outcome == "false_positive"]
        if len(rows) >= FP_SOURCE_MIN_CASES and len(fps) / len(rows) >= FP_SOURCE_RATE:
            insights.append(_insight(f"fp_source:{src}", "info", f"Source \"{src}\" is noisy",
                                     f"{len(fps)} of {len(rows)} classified cases closed in the period were false "
                                     f"positives ({100 * len(fps) // len(rows)}%). Tune the detection.", fps))

    return {
        "start": start, "end": end, "previous_start": plo.date(), "previous_end": lo.date() - timedelta(days=1),
        "kpis": kpis, "trend": trend, "backlog_by_analyst": backlog_by_analyst, "aging": aging_buckets,
        "outcomes": outcomes, "false_positive_rate": fp_rate, "closed_classified": classified,
        "insights": insights,
    }
