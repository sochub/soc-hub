"""Case escalation transitions. Flush-only; the caller commits.

Each row carries `policy_snapshot = {"tiers": [...], "to": hop, "from": hop}` where a hop is
`{position, tier_name, group_id, group_name}`: positions and names survive tiers/groups being deleted from the live
policy (their FKs on the row are SET NULL). Every transition writes a case activity entry, an audit entry, and
stashes a workflow event that is emitted after commit (app/escalations/events.py).
"""
import logging
from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional, Set

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.db.session import AsyncSessionLocal
from app.escalations import events
from app.models.case import Case, TimelineEvent
from app.models.escalation import CaseEscalation, EscalationTier, EscalationTierGroup, UserGroup, UserGroupMember
from app.models.membership import TenantMembership
from app.models.tenant import Tenant
from app.models.user import User
from app.notifications.delivery import _link as case_url
from app.notifications.service import on_escalation
from app.utils.audit import create_audit_log

MAX_TEXT = 4000
SWEEP_BATCH = 500
logger = logging.getLogger(__name__)


class EscalationError(Exception):
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code, self.detail = status_code, detail


def dedup_key(row: CaseEscalation) -> str:
    return f"sochub-case-{row.case_id}-esc-{row.id}"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _text(value: Optional[str], field: str, required: bool = True) -> str:
    v = (value or "").strip()
    if required and not v:
        raise EscalationError(422, f"{field} is required")
    if len(v) > MAX_TEXT:
        raise EscalationError(422, f"{field} must be at most {MAX_TEXT} characters")
    return v


def _tier(tiers: list, *, id: Optional[int] = None, position: Optional[int] = None) -> Optional[dict]:
    return next((t for t in tiers if (t["id"] == id if id is not None else t["position"] == position)), None)


def _hop(tiers: list, position: int, group_id: Optional[int]) -> dict:
    t = _tier(tiers, position=position)
    g = next((g for g in (t["groups"] if t else []) if g["id"] == group_id), None)
    return {"position": position, "tier_name": t["name"] if t else f"Tier {position}",
            "group_id": group_id, "group_name": g["name"] if g else None}


def _label(hop: dict) -> str:
    return f"{hop['tier_name']} · {hop['group_name']}" if hop.get("group_name") else hop["tier_name"]


def _one_line(text: str) -> str:
    return " ".join(text.split())[:500]


async def snapshot_policy(db, tenant_id: int) -> dict:
    tiers = (await db.execute(select(EscalationTier).where(EscalationTier.tenant_id == tenant_id)
                              .order_by(EscalationTier.position, EscalationTier.id))).scalars().all()
    groups = {t.id: [] for t in tiers}
    if groups:
        for tid, gid, name in (await db.execute(
                select(EscalationTierGroup.tier_id, UserGroup.id, UserGroup.name)
                .join(UserGroup, UserGroup.id == EscalationTierGroup.group_id)
                .where(EscalationTierGroup.tier_id.in_(list(groups)), UserGroup.tenant_id == tenant_id)
                .order_by(UserGroup.name, UserGroup.id))).all():
            groups[tid].append({"id": gid, "name": name})
    return {"tiers": [{"id": t.id, "position": t.position, "name": t.name,
                       "accept_timeout_minutes": t.accept_timeout_minutes, "notify_email": t.notify_email,
                       "notify_slack": t.notify_slack, "slack_channel": t.slack_channel, "groups": groups[t.id]}
                      for t in tiers]}


async def current_row(db, case_id: int) -> Optional[CaseEscalation]:
    return (await db.execute(select(CaseEscalation).where(CaseEscalation.case_id == case_id)
                             .order_by(CaseEscalation.id.desc()).limit(1))).scalars().first()


async def current_position(db, case: Case) -> int:
    row = (await db.execute(select(CaseEscalation).where(
        CaseEscalation.case_id == case.id, CaseEscalation.tenant_id == case.tenant_id,
        CaseEscalation.status.in_(("pending", "accepted"))).order_by(CaseEscalation.id.desc()).limit(1))
    ).scalars().first()
    return row.policy_snapshot["to"]["position"] if row else 1


async def _locked(db, case: Case, status: Optional[str] = "pending") -> Optional[CaseEscalation]:
    """The case's latest row (with `status`, if given), locked FOR UPDATE and refreshed from the database."""
    q = select(CaseEscalation).where(CaseEscalation.case_id == case.id, CaseEscalation.tenant_id == case.tenant_id)
    if status:
        q = q.where(CaseEscalation.status == status)
    q = q.order_by(CaseEscalation.id.desc()).limit(1).with_for_update().execution_options(populate_existing=True)
    return (await db.execute(q)).scalars().first()


async def _member_groups(db, tenant_id: int, user_id: Optional[int], group_ids: Iterable[int]) -> Set[int]:
    """Groups among `group_ids` that the user is an active member of in this tenant."""
    gids = [g for g in group_ids if g]
    if not user_id or not gids:
        return set()
    return set((await db.execute(
        select(UserGroupMember.group_id)
        .join(TenantMembership, TenantMembership.id == UserGroupMember.membership_id)
        .join(User, User.id == TenantMembership.user_id)
        .where(UserGroupMember.group_id.in_(gids), TenantMembership.tenant_id == tenant_id,
               TenantMembership.user_id == user_id, User.is_active.is_(True)))).scalars().all())


async def _live(db, model, tenant_id: int, ids: Iterable[Optional[int]]) -> Set[int]:
    """Ids (of UserGroup / EscalationTier) that still exist in the live policy; snapshots may name deleted ones."""
    ids = [i for i in ids if i]
    if not ids:
        return set()
    return set((await db.execute(select(model.id).where(model.tenant_id == tenant_id, model.id.in_(ids))))
               .scalars().all())


def _person(u: Optional[User]) -> Optional[dict]:
    return {"email": u.email, "name": u.full_name} if u else None


async def _payload(db, case: Case, row: CaseEscalation) -> dict:
    to = row.policy_snapshot["to"]
    req = await db.get(User, row.requested_by) if row.requested_by else None
    acc = await db.get(User, row.accepted_by) if row.accepted_by else None
    return {"escalation": {
        "id": row.id, "dedup_key": dedup_key(row), "status": row.status,
        "tier": {"position": to["position"], "name": to["tier_name"]},
        "group": {"id": to["group_id"], "name": to["group_name"]},
        "requested_by": {"id": req.id, **_person(req)} if req else None,
        "reason": row.reason, "summary": row.summary, "requested_help": row.requested_help,
        "accepted_by": _person(acc), "accepted_via": row.accepted_via, "case_url": case_url(case.id)}}


async def _record(db, case: Case, row: CaseEscalation, actor_id: Optional[int], action: str, text: str,
                  frm: Optional[dict], to: Optional[dict], note: Optional[str], event_type: Optional[str]) -> None:
    db.add(TimelineEvent(case_id=case.id, user_id=actor_id, event_type="escalation", content=_one_line(text)))
    await create_audit_log(db, "case", case.id, action, case.tenant_id, actor_id, {
        "escalation_id": row.id, "from": _label(frm) if frm else None, "to": _label(to) if to else None,
        "note": note or None})
    if event_type:
        events.stash(db, case.tenant_id, event_type, case.id, await _payload(db, case, row))


async def _close(db, case: Case, row: CaseEscalation, status: str, actor_id: Optional[int], note: Optional[str],
                 action: str, text: str, to: Optional[dict] = None) -> None:
    """Move a pending row to `status`; fires case.escalation_closed."""
    row.status, row.closed_by, row.closed_at, row.close_note = status, actor_id, _now(), note or None
    await db.flush()
    await _record(db, case, row, actor_id, action, text, row.policy_snapshot["to"], to, note,
                  "case.escalation_closed")


async def _open(db, case: Case, *, tiers: list, to: dict, to_tier_id: Optional[int], frm: dict,
                from_tier_id: Optional[int], from_group_id: Optional[int], requested_by: Optional[int],
                reason: str, summary: str, requested_help: str) -> CaseEscalation:
    tier = _tier(tiers, position=to["position"])
    timeout = tier["accept_timeout_minutes"] if tier else None
    row = CaseEscalation(
        tenant_id=case.tenant_id, case_id=case.id, from_tier_id=from_tier_id, to_tier_id=to_tier_id,
        from_group_id=from_group_id, to_group_id=to["group_id"], requested_by=requested_by, reason=reason,
        summary=summary, requested_help=requested_help, status="pending",
        due_at=_now() + timedelta(minutes=timeout) if timeout else None,
        policy_snapshot={"tiers": tiers, "to": to, "from": frm})
    try:
        async with db.begin_nested():  # a lost race trips uq_case_escalations_one_pending; keep the outer txn usable
            db.add(row)
            await db.flush()
    except IntegrityError:
        raise EscalationError(409, "Case already has a pending escalation") from None
    case.owner_id = None
    await db.flush()
    return row


async def _announce(db, case: Case, row: CaseEscalation, actor_id: Optional[int], type_: str, summary: str) -> None:
    await on_escalation(db, case, row, type_, summary, actor_id)
    events.stash(db, case.tenant_id, "case.escalated", case.id, await _payload(db, case, row))
    snap = row.policy_snapshot
    tier = _tier(snap["tiers"], position=snap["to"]["position"])
    if row.status == "pending" and tier and tier.get("slack_channel"):
        events.stash_channel(db, row.id)  # one channel card per opened row, posted after commit


async def escalate(db, *, case: Case, actor_id: int, to_tier_id: int, to_group_id: int, reason: str, summary: str,
                   requested_help: str) -> CaseEscalation:
    reason, summary = _text(reason, "Reason"), _text(summary, "Summary")
    requested_help = _text(requested_help, "Requested help")
    # A pending row means the chain is still open: validate against, and keep, the chain's snapshot.
    pending = await _locked(db, case)
    tiers = pending.policy_snapshot["tiers"] if pending else (await snapshot_policy(db, case.tenant_id))["tiers"]
    if not tiers:
        raise EscalationError(422, "No escalation policy is configured")
    tier = _tier(tiers, id=to_tier_id)
    if tier is None:
        raise EscalationError(422, "Unknown tier")
    cur = pending.policy_snapshot["to"]["position"] if pending else await current_position(db, case)
    if tier["position"] <= cur:
        raise EscalationError(422, "Escalate to a tier above the current one")
    if to_group_id not in [g["id"] for g in tier["groups"]]:
        raise EscalationError(422, "That group is not on the selected tier")
    if not await _live(db, UserGroup, case.tenant_id, [to_group_id]):
        raise EscalationError(422, "That group no longer exists")
    to = _hop(tiers, tier["position"], to_group_id)

    if pending is not None:  # closed (flushed) before the new pending row is inserted
        await _close(db, case, pending, "superseded", actor_id, None, "case_escalation_superseded",
                     f"Escalation to {_label(pending.policy_snapshot['to'])} superseded by a new escalation", to)

    # Return target: the escalating user's group on the from-tier (first by name), else that tier's first group.
    from_tier = _tier(tiers, position=cur)
    from_group_id = None
    if from_tier and from_tier["groups"]:
        live = await _live(db, UserGroup, case.tenant_id, [g["id"] for g in from_tier["groups"]])
        gids = [g["id"] for g in from_tier["groups"] if g["id"] in live]
        mine = await _member_groups(db, case.tenant_id, actor_id, gids)
        from_group_id = next((g for g in gids if g in mine), gids[0] if gids else None)
    frm = _hop(tiers, cur, from_group_id)
    live_tiers = await _live(db, EscalationTier, case.tenant_id, [tier["id"], from_tier["id"] if from_tier else None])

    row = await _open(db, case, tiers=tiers, to=to, to_tier_id=tier["id"] if tier["id"] in live_tiers else None,
                      frm=frm, from_tier_id=from_tier["id"] if from_tier and from_tier["id"] in live_tiers else None,
                      from_group_id=from_group_id,
                      requested_by=actor_id, reason=reason, summary=summary, requested_help=requested_help)
    await _record(db, case, row, actor_id, "case_escalated", f"Escalated to {_label(to)} — {reason}",
                  frm, to, None, None)
    await _announce(db, case, row, actor_id, "escalated", f"Case #{case.id} escalated to {_label(to)}")
    return row


async def accept(db, *, case: Case, actor_id: int, is_admin: bool) -> CaseEscalation:
    row = await _locked(db, case)
    if row is None:
        raise EscalationError(409, "No pending escalation")
    in_group = bool(await _member_groups(db, case.tenant_id, actor_id, [row.to_group_id]))
    if not in_group and not is_admin:
        raise EscalationError(403, "Only members of the receiving group can accept")
    row.accepted_by, row.accepted_at = actor_id, _now()
    row.accepted_via = "soc_hub" if in_group else "admin_override"
    case.owner_id = actor_id
    suffix = "" if in_group else " (admin override)"
    await _close(db, case, row, "accepted", actor_id, None, "case_escalation_accepted",
                 f"Accepted escalation at {_label(row.policy_snapshot['to'])}{suffix}")
    return row


async def redirect(db, *, case: Case, actor_id: int, to_group_id: int, note: str) -> CaseEscalation:
    note = _text(note, "Note")
    row = await _locked(db, case)
    if row is None:
        raise EscalationError(409, "No pending escalation")
    snap = row.policy_snapshot
    tier = _tier(snap["tiers"], position=snap["to"]["position"])
    if (tier is None or to_group_id == row.to_group_id
            or to_group_id not in [g["id"] for g in tier["groups"]]):
        raise EscalationError(422, "Redirect to another group on the same tier")
    if not await _live(db, UserGroup, case.tenant_id, [to_group_id]):
        raise EscalationError(422, "That group no longer exists")
    to = _hop(snap["tiers"], snap["to"]["position"], to_group_id)
    await _close(db, case, row, "redirected", actor_id, note, "case_escalation_redirected",
                 f"Escalation redirected to {_label(to)} — {note}", to)
    new = await _open(db, case, tiers=snap["tiers"], to=to, to_tier_id=row.to_tier_id, frm=snap["from"],
                      from_tier_id=row.from_tier_id, from_group_id=row.from_group_id,
                      requested_by=row.requested_by, reason=row.reason, summary=row.summary,
                      requested_help=row.requested_help)
    await _announce(db, case, new, actor_id, "escalated", f"Case #{case.id} redirected to {_label(to)}")
    return new


async def return_down(db, *, case: Case, actor_id: int, note: str) -> CaseEscalation:
    note = _text(note, "Note")
    row = await _locked(db, case, status=None)
    snap = row.policy_snapshot if row else {}
    frm, to = snap.get("from"), snap.get("to")
    if row is None or row.status != "accepted" or not frm or frm["position"] >= to["position"]:
        raise EscalationError(409, "Only an accepted escalation from a lower tier can be returned")
    row.status, row.closed_by, row.closed_at, row.close_note = "returned", actor_id, _now(), note
    await db.flush()
    await _record(db, case, row, actor_id, "case_escalation_returned",
                  f"Returned to {_label(frm)} for more investigation — {note}", to, frm, note, None)
    new = await _open(db, case, tiers=snap["tiers"], to=frm, to_tier_id=row.from_tier_id, frm=to,
                      from_tier_id=row.to_tier_id, from_group_id=row.to_group_id, requested_by=actor_id,
                      reason=note, summary=row.summary, requested_help=row.requested_help)
    await _announce(db, case, new, actor_id, "escalation_returned",
                    f"Case #{case.id} returned to {_label(frm)} for more investigation")
    return new


async def withdraw(db, *, case: Case, actor_id: int, note: Optional[str]) -> CaseEscalation:
    note = _text(note, "Note", required=False)
    row = await _locked(db, case)
    if row is None:
        raise EscalationError(409, "No pending escalation")
    case.owner_id = row.requested_by
    await _close(db, case, row, "withdrawn", actor_id, note, "case_escalation_withdrawn",
                 "Escalation withdrawn" + (f" — {note}" if note else ""))
    return row


async def on_owner_set(db, *, case: Case, new_owner_id: Optional[int], actor_id: Optional[int]) -> None:
    if new_owner_id is None:
        return
    row = await _locked(db, case)
    if row is None:
        return
    row.accepted_by, row.accepted_at, row.accepted_via = new_owner_id, _now(), "assignment"
    owner = await db.get(User, new_owner_id)
    who = (owner.full_name or owner.email) if owner else "another user"
    await _close(db, case, row, "accepted", actor_id, None, "case_escalation_accepted",
                 f"Escalation closed: case assigned to {who}")


async def on_case_closed(db, *, case: Case, actor_id: Optional[int]) -> None:
    row = await _locked(db, case)
    if row is not None:
        await _close(db, case, row, "cancelled", actor_id, None, "case_escalation_cancelled",
                     "Escalation cancelled: case closed")


def _name(u: Optional[User]) -> Optional[str]:
    return (u.full_name or u.email) if u else None


async def _names(db, ids: Iterable[Optional[int]]) -> dict:
    ids = {i for i in ids if i}
    if not ids:
        return {}
    return {u.id: _name(u) for u in (await db.execute(select(User).where(User.id.in_(ids)))).scalars().all()}


def _summary(row: CaseEscalation, names: dict) -> dict:
    to = row.policy_snapshot["to"]
    return {"id": row.id, "status": row.status, "tier_position": to["position"], "tier_name": to["tier_name"],
            "group_id": to["group_id"], "group_name": to["group_name"],
            "requested_by_name": names.get(row.requested_by), "requested_at": row.created_at, "due_at": row.due_at,
            "accepted_by_name": names.get(row.accepted_by), "accepted_at": row.accepted_at}


async def case_summaries(db, tenant_id: int, case_ids: list) -> dict:
    """{case_id: summary} for cases whose latest escalation is pending/accepted. Two queries, any number of cases."""
    if not case_ids:
        return {}
    rows = (await db.execute(
        select(CaseEscalation).where(CaseEscalation.tenant_id == tenant_id, CaseEscalation.case_id.in_(case_ids))
        .distinct(CaseEscalation.case_id).order_by(CaseEscalation.case_id, CaseEscalation.id.desc()))).scalars().all()
    rows = [r for r in rows if r.status in ("pending", "accepted")]
    names = await _names(db, [i for r in rows for i in (r.requested_by, r.accepted_by)])
    return {r.case_id: _summary(r, names) for r in rows}


async def history(db, case: Case) -> list:
    """Every hop for the case, newest first, with actor names."""
    rows = (await db.execute(select(CaseEscalation).where(
        CaseEscalation.case_id == case.id, CaseEscalation.tenant_id == case.tenant_id)
        .order_by(CaseEscalation.id.desc()))).scalars().all()
    names = await _names(db, [i for r in rows for i in (r.requested_by, r.accepted_by, r.closed_by)])
    out = []
    for r in rows:
        frm = r.policy_snapshot.get("from") or {}
        out.append({**_summary(r, names), "from_tier_name": frm.get("tier_name"),
                    "from_group_name": frm.get("group_name"), "reason": r.reason, "summary": r.summary,
                    "requested_help": r.requested_help, "accepted_via": r.accepted_via,
                    "closed_by_name": names.get(r.closed_by), "closed_at": r.closed_at, "close_note": r.close_note})
    return out


async def _climb_target(db, tenant_id: int, snap: dict) -> Optional[tuple]:
    """(tier, group_id) of the next snapshot tier above the row's with a group that still exists, else None."""
    for tier in sorted((t for t in snap["tiers"] if t["position"] > snap["to"]["position"]),
                       key=lambda t: t["position"]):
        live = await _live(db, UserGroup, tenant_id, [g["id"] for g in tier["groups"]])
        gid = next((g["id"] for g in tier["groups"] if g["id"] in live), None)
        if gid is not None:
            return tier, gid
    return None


async def _sweep_one(db, row_id: int, now: datetime) -> Optional[str]:
    """Act on one due pending row in the caller's transaction: 'climbed', 'overdue' or None (nothing to do)."""
    row = (await db.execute(
        select(CaseEscalation).where(CaseEscalation.id == row_id, CaseEscalation.status == "pending",
                                     CaseEscalation.due_at.isnot(None), CaseEscalation.due_at < now,
                                     CaseEscalation.overdue_notified.is_(False))
        .with_for_update(skip_locked=True).execution_options(populate_existing=True))).scalars().first()
    if row is None:
        return None
    if (await db.execute(select(Tenant.id).where(Tenant.id == row.tenant_id, Tenant.is_active.is_(False)))).first():
        return None
    case = await db.get(Case, row.case_id)
    if case is None or case.tenant_id != row.tenant_id:
        return None
    snap = row.policy_snapshot
    target = await _climb_target(db, case.tenant_id, snap)
    if target is None:  # last tier (or every higher group is gone): tell the group once
        row.overdue_notified = True
        await db.flush()
        await on_escalation(db, case, row, "escalation_overdue",
                            f"Escalation of case #{case.id} to {_label(snap['to'])} is overdue", None)
        return "overdue"
    tier, group_id = target
    cur = _tier(snap["tiers"], position=snap["to"]["position"])
    note = f"not accepted within {cur['accept_timeout_minutes']} min" if cur else "not accepted in time"
    to = _hop(snap["tiers"], tier["position"], group_id)
    await _close(db, case, row, "climbed", None, note, "case_escalation_climbed",
                 f"Escalation to {_label(snap['to'])} {note}; climbed to {_label(to)}", to)
    live_tiers = await _live(db, EscalationTier, case.tenant_id, [tier["id"]])
    new = await _open(db, case, tiers=snap["tiers"], to=to, to_tier_id=tier["id"] if tier["id"] in live_tiers else None,
                      frm=snap["to"], from_tier_id=row.to_tier_id, from_group_id=row.to_group_id,
                      requested_by=row.requested_by, reason=row.reason, summary=row.summary,
                      requested_help=row.requested_help)
    await _announce(db, case, new, None, "escalated", f"Case #{case.id} escalated to {_label(to)} (not accepted in time)")
    return "climbed"


async def sweep(now: Optional[datetime] = None, case_ids: Optional[list] = None) -> dict:
    """Climb pending escalations whose accept timeout passed; notify once when there is nowhere higher to go.

    Each row runs in its own session and transaction (FOR UPDATE SKIP LOCKED), so one failure never aborts the batch.
    Workflow events are emitted by the after-commit hook of each session. At most SWEEP_BATCH rows per call.
    """
    now = now or _now()
    q = (select(CaseEscalation.id).join(Tenant, Tenant.id == CaseEscalation.tenant_id)
         .where(CaseEscalation.status == "pending", CaseEscalation.due_at.isnot(None), CaseEscalation.due_at < now,
                CaseEscalation.overdue_notified.is_(False), Tenant.is_active.isnot(False))
         .order_by(CaseEscalation.due_at, CaseEscalation.id).limit(SWEEP_BATCH))
    if case_ids is not None:
        q = q.where(CaseEscalation.case_id.in_(case_ids))
    async with AsyncSessionLocal() as db:
        ids = (await db.execute(q)).scalars().all()
    out = {"climbed": [], "overdue": []}
    for row_id in ids:
        try:
            async with AsyncSessionLocal() as db:
                result = await _sweep_one(db, row_id, now)
                await db.commit()
            if result:
                out[result].append(row_id)
        except Exception as e:
            logger.warning("escalation sweep failed row=%s err=%s", row_id, type(e).__name__)
    return out
