"""Organizations view: metrics and one merged queue across the tenants a user works in, plus a digest each.

Only tenants this session could switch into right now are included (membership, active tenant, the tenant's
sign-in method, its MFA requirement): the portfolio never shows data the tenant switcher would refuse.
Super admins, who may switch into any tenant, see every active tenant (first MAX_TENANTS by name)."""
import html
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import login_tenant
from app.models.case import Alert, Case, CaseStatus
from app.models.membership import TenantMembership
from app.models.tenant import Tenant
from app.models.user import User
from app.utils.sla import compute_sla_state, load_policy_overrides

OPEN = [CaseStatus.NEW, CaseStatus.OPEN, CaseStatus.IN_PROGRESS, CaseStatus.PENDING]
SEV_RANK = {"critical": 4, "high": 3, "medium": 2, "low": 1, "info": 0}
SLA_RANK = {"breached": 3, "at_risk": 2, "on_track": 1, "met": 0, "not_tracked": 0}
MAX_OPEN_PER_TENANT = 500
QUEUE_MAX = 200
MAX_TENANTS = 100


def _v(x):
    return getattr(x, "value", x)


async def enterable_tenants(db: AsyncSession, user: User) -> tuple[list, int]:
    """[(Tenant, role)] this session may act in, and how many memberships were left out by sign-in policy."""
    role = TenantMembership.role
    q = select(Tenant, role).outerjoin(TenantMembership, (TenantMembership.tenant_id == Tenant.id)
                                       & (TenantMembership.user_id == user.id)).where(Tenant.is_active.is_(True))
    if not user.is_super_admin:
        q = q.where(TenantMembership.user_id.isnot(None))
    rows = (await db.execute(q.order_by(Tenant.name, Tenant.id).limit(MAX_TENANTS))).all()
    out, hidden = [], 0
    mfa_tenants = set((await db.execute(select(Tenant.id).where(Tenant.require_mfa.is_(True)))).scalars())
    for t, role in rows:
        if not login_tenant.session_may_enter(user, t.id, t.member_login_method) or (
                user.mfa_enabled_at is None and t.id in mfa_tenants):
            hidden += 1
            continue
        out.append((t, role or ("super_admin" if user.is_super_admin else None)))
    return out, hidden


async def tenant_metrics(db: AsyncSession, tenant_id: int, open_cases: list) -> dict:
    now = datetime.now(timezone.utc)
    policy = await load_policy_overrides(db, tenant_id)
    sla = [compute_sla_state(c, policy)["overall_status"] for c in open_cases]
    by_sev = {s: 0 for s in SEV_RANK}
    for c in open_cases:
        by_sev[_v(c.severity)] = by_sev.get(_v(c.severity), 0) + 1
    mttr = (await db.execute(select(func.avg(func.extract("epoch", Case.resolved_at - Case.created_at) / 3600)).where(
        Case.tenant_id == tenant_id, Case.resolved_at >= now - timedelta(days=30)))).scalar()
    pending_alerts = (await db.execute(select(func.count(Alert.id)).where(
        Alert.tenant_id == tenant_id, Alert.status == "pending"))).scalar() or 0
    oldest = min((c.created_at for c in open_cases), default=None)
    return {
        "open_cases": len(open_cases), "by_severity": by_sev,
        "unassigned": sum(1 for c in open_cases if c.owner_id is None),
        "sla_breached": sla.count("breached"), "sla_at_risk": sla.count("at_risk"),
        "pending_alerts": pending_alerts,
        "mttr_hours_30d": round(float(mttr), 1) if mttr is not None else None,
        "oldest_open_hours": round((now - oldest).total_seconds() / 3600, 1) if oldest else None,
    }


async def overview(db: AsyncSession, user: User) -> dict:
    tenants, hidden = await enterable_tenants(db, user)
    out, queue = [], []
    owners: dict = {}
    for t, role in tenants:
        open_cases = (await db.execute(select(Case).where(Case.tenant_id == t.id, Case.status.in_(OPEN))
                                       .order_by(Case.created_at).limit(MAX_OPEN_PER_TENANT))).scalars().all()
        out.append({"id": t.id, "name": t.name, "slug": t.slug, "role": role,
                    **await tenant_metrics(db, t.id, open_cases)})
        policy = await load_policy_overrides(db, t.id)
        for c in open_cases:
            s = compute_sla_state(c, policy)
            queue.append({"tenant_id": t.id, "tenant_name": t.name, "case_id": c.id, "title": c.title,
                          "severity": _v(c.severity), "status": _v(c.status), "owner_id": c.owner_id,
                          "created_at": c.created_at, "sla_status": s["overall_status"],
                          "sla_due_at": s["resolution_due_at"] or s["response_due_at"]})
            if c.owner_id:
                owners[c.owner_id] = None
    if owners:
        for uid, name, email in (await db.execute(select(User.id, User.full_name, User.email).where(
                User.id.in_(list(owners))))).all():
            owners[uid] = name or email
    queue.sort(key=lambda q: (-SLA_RANK.get(q["sla_status"], 0), -SEV_RANK.get(q["severity"], 0), q["created_at"]))
    for q in queue[:QUEUE_MAX]:
        q["owner"] = owners.get(q["owner_id"])
    return {"tenants": out, "queue": queue[:QUEUE_MAX], "queue_total": len(queue), "hidden_tenants": hidden}


async def digest(db: AsyncSession, tenant: Tenant, days: int = 7) -> dict:
    """What happened in one organization in the last `days` days."""
    now = datetime.now(timezone.utc)
    since = now - timedelta(days=days)
    policy = await load_policy_overrides(db, tenant.id)
    opened = (await db.execute(select(Case).where(Case.tenant_id == tenant.id, Case.created_at >= since)
                               .order_by(Case.created_at))).scalars().all()
    resolved = (await db.execute(select(Case).where(Case.tenant_id == tenant.id, Case.resolved_at >= since)))
    resolved = resolved.scalars().all()
    still_open = (await db.execute(select(func.count(Case.id)).where(
        Case.tenant_id == tenant.id, Case.status.in_(OPEN)))).scalar() or 0
    alerts = dict((await db.execute(select(Alert.status, func.count(Alert.id)).where(
        Alert.tenant_id == tenant.id, Alert.created_at >= since).group_by(Alert.status))).all())
    suppressed = (await db.execute(select(func.count(Alert.id)).where(
        Alert.tenant_id == tenant.id, Alert.created_at >= since, Alert.suppression_rule_id.isnot(None)))).scalar() or 0
    by_sev = {s: 0 for s in SEV_RANK}
    for c in opened:
        by_sev[_v(c.severity)] = by_sev.get(_v(c.severity), 0) + 1
    outcomes: dict = {}
    for c in resolved:
        outcomes[c.outcome or "unspecified"] = outcomes.get(c.outcome or "unspecified", 0) + 1
    hours = [(c.resolved_at - c.created_at).total_seconds() / 3600 for c in resolved if c.created_at]
    breached = sum(1 for c in opened if compute_sla_state(c, policy)["overall_status"] == "breached")
    notable = sorted((c for c in opened if SEV_RANK.get(_v(c.severity), 0) >= 3),
                     key=lambda c: -SEV_RANK.get(_v(c.severity), 0))[:5]
    return {
        "tenant_id": tenant.id, "tenant_name": tenant.name, "days": days, "from": since, "to": now,
        "cases_opened": len(opened), "cases_resolved": len(resolved), "open_now": still_open,
        "opened_by_severity": by_sev, "outcomes": outcomes,
        "mttr_hours": round(sum(hours) / len(hours), 1) if hours else None, "sla_breached": breached,
        "alerts_received": sum(alerts.values()), "alerts_promoted": alerts.get("promoted", 0),
        "alerts_dismissed": alerts.get("dismissed", 0), "alerts_suppressed": suppressed,
        "notable": [{"case_id": c.id, "title": c.title, "severity": _v(c.severity), "status": _v(c.status)}
                    for c in notable],
    }


def digest_text(d: dict) -> str:
    """Plain-text version an analyst can paste into an email or a ticket."""
    sev = ", ".join(f"{n} {s}" for s, n in d["opened_by_severity"].items() if n) or "none"
    out = ", ".join(f"{n} {k.replace('_', ' ')}" for k, n in d["outcomes"].items()) or "none"
    lines = [
        f"{d['tenant_name']}: security summary for the last {d['days']} days",
        "",
        f"Cases opened: {d['cases_opened']} ({sev})",
        f"Cases resolved: {d['cases_resolved']} ({out})",
        f"Open now: {d['open_now']}",
        f"Mean time to resolve: {d['mttr_hours']} h" if d["mttr_hours"] is not None else "Mean time to resolve: n/a",
        f"SLA breaches among new cases: {d['sla_breached']}",
        f"Alerts received: {d['alerts_received']} (promoted {d['alerts_promoted']}, dismissed "
        f"{d['alerts_dismissed']}, of which suppressed as known noise {d['alerts_suppressed']})",
    ]
    if d["notable"]:
        lines += ["", "Notable cases:"] + [f"- #{n['case_id']} [{n['severity']}] {n['title']} ({n['status'].replace('_', ' ')})"
                                          for n in d["notable"]]
    return "\n".join(lines)


def digest_html(d: dict) -> str:
    body = "<br>".join(html.escape(line) for line in digest_text(d).split("\n"))
    return f'<html><body style="font-family: Arial, sans-serif; max-width: 640px; margin: 0 auto;">{body}</body></html>'


async def member_role(db: AsyncSession, user_id: int, tenant_id: int) -> Optional[str]:
    return (await db.execute(select(TenantMembership.role).where(
        TenantMembership.user_id == user_id, TenantMembership.tenant_id == tenant_id))).scalar()
