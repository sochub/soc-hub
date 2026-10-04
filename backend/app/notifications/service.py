"""Write in-app notifications and follower rows for case activity. Flush-only; the caller commits.

Every public hook runs in a SAVEPOINT: a failure is logged and swallowed so the triggering
action still commits. Ids of externally delivered notifications are stashed on
db.info["notif_external"] for the after-commit hook (app/notifications/delivery.py).
"""
import logging
from typing import Iterable, Optional, Set

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.models.case import Case
from app.models.membership import TenantMembership
from app.models.notification import EXTERNAL_TYPES, CaseFollower, Notification
from app.models.user import User
from app.notifications.mentions import parse_mention_ids

logger = logging.getLogger(__name__)


async def _members(db, tenant_id: int, user_ids: Iterable[int]) -> Set[int]:
    """Active users that are members of the tenant."""
    ids = {i for i in user_ids if i}
    if not ids:
        return set()
    rows = await db.execute(
        select(TenantMembership.user_id).join(User, User.id == TenantMembership.user_id)
        .where(TenantMembership.tenant_id == tenant_id, TenantMembership.user_id.in_(ids),
               User.is_active.is_(True)))
    return set(rows.scalars().all())


async def _followers(db, case_id: int) -> Set[int]:
    return set((await db.execute(select(CaseFollower.user_id).where(CaseFollower.case_id == case_id))).scalars().all())


async def _actor_name(db, actor_id: Optional[int]) -> str:
    if not actor_id:
        return "Automation"
    u = (await db.execute(select(User).where(User.id == actor_id))).scalars().first()
    return ((u.full_name or u.email) if u else "Someone")[:60]


async def _follow(db, case: Case, user_id: int) -> None:
    await db.execute(pg_insert(CaseFollower).values(case_id=case.id, user_id=user_id, tenant_id=case.tenant_id)
                     .on_conflict_do_nothing(constraint="uq_case_follower"))


def _add(db, case: Case, user_id: int, type_: str, summary: str, actor_id, event_id=None) -> Notification:
    n = Notification(tenant_id=case.tenant_id, user_id=user_id, case_id=case.id, type=type_,
                     actor_id=actor_id, timeline_event_id=event_id, summary=summary[:200])
    db.add(n)
    return n


async def _guarded(db, case: Case, type_: str, body) -> None:
    created = []
    try:
        async with db.begin_nested():
            created = await body()
            await db.flush()
    except Exception as e:  # never block the triggering action
        logger.warning("notification write failed case=%s type=%s err=%s", case.id, type_, type(e).__name__)
        return
    ext = [n.id for n in created if n.type in EXTERNAL_TYPES]
    if ext:
        db.info.setdefault("notif_external", []).extend(ext)


async def follow(db, case: Case, user_id: int) -> None:
    await _follow(db, case, user_id)


async def unfollow(db, case: Case, user_id: int) -> None:
    await db.execute(delete(CaseFollower).where(CaseFollower.case_id == case.id, CaseFollower.user_id == user_id))


async def is_following(db, case_id: int, user_id: int) -> bool:
    return (await db.execute(select(CaseFollower.id).where(
        CaseFollower.case_id == case_id, CaseFollower.user_id == user_id))).first() is not None


async def on_comment(db, case: Case, event, actor_id: Optional[int], previous_content: Optional[str] = None) -> None:
    async def body():
        mentioned = set()
        if actor_id is not None and event.event_type == "comment":
            new_ids = set(parse_mention_ids(event.content)) - set(parse_mention_ids(previous_content or ""))
            mentioned = await _members(db, case.tenant_id, new_ids) - {actor_id}
            await _follow(db, case, actor_id)
            for uid in mentioned:
                await _follow(db, case, uid)
        if previous_content is not None:  # edits only notify newly mentioned users
            others = set()
        else:
            others = await _members(db, case.tenant_id, await _followers(db, case.id)) - mentioned - {actor_id}
        name = await _actor_name(db, actor_id)
        out = [_add(db, case, u, "mention", f"{name} mentioned you on case #{case.id}", actor_id, event.id) for u in mentioned]
        out += [_add(db, case, u, "comment", f"{name} commented on case #{case.id}", actor_id, event.id) for u in others]
        return out
    await _guarded(db, case, "comment", body)


async def on_case_update(db, case: Case, changes: dict, actor_id: Optional[int]) -> None:
    async def body():
        out = []
        name = await _actor_name(db, actor_id)
        new_owner = changes.get("owner_id", {}).get("to")
        if new_owner and new_owner != actor_id and await _members(db, case.tenant_id, [new_owner]):
            await _follow(db, case, new_owner)
            out.append(_add(db, case, new_owner, "assigned", f"{name} assigned you case #{case.id}", actor_id))
        elif new_owner:
            await _follow(db, case, new_owner)
        followers = await _members(db, case.tenant_id, await _followers(db, case.id)) - {actor_id}
        for field, type_ in (("status", "status_change"), ("severity", "severity_change")):
            if field in changes:
                label = "Status" if field == "status" else "Severity"
                summary = f"{label} changed to {changes[field]['to']} on case #{case.id}"
                out += [_add(db, case, u, type_, summary, actor_id) for u in followers]
        return out
    await _guarded(db, case, "case_update", body)


async def on_case_created(db, case: Case, actor_id: Optional[int]) -> None:
    if not case.owner_id:
        return
    await on_case_update(db, case, {"owner_id": {"from": None, "to": case.owner_id}}, actor_id)


async def on_sla_breach(db, case: Case, breach_type: str) -> None:
    async def body():
        users = await _members(db, case.tenant_id, (await _followers(db, case.id)) | {case.owner_id})
        label = "response" if breach_type == "response" else "resolution"
        return [_add(db, case, u, "sla_breach", f"Case #{case.id} breached its {label} SLA", None) for u in users]
    await _guarded(db, case, "sla_breach", body)
