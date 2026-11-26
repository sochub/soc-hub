"""SOC-bot suggestions lifted from a war-room channel: recording (dedupe + caps) and accept/dismiss."""
import re
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError

from app.models.artifact import Artifact, ArtifactType
from app.models.case import Case
from app.models.case_artifact import CaseArtifact
from app.models.war_room_suggestion import WarRoomSuggestion
from app.services.case_service import add_artifact_to_case
from app.services.milestones import KEYS, MilestoneConflict, set_milestones
from app.services.war_room_events import queue_card
from app.utils.audit import create_audit_log

MAX_PER_MESSAGE = 5          # artifacts one Slack reply shows; the rest of the message waits in the web UI
MAX_PER_CHANNEL_HOUR = 20    # stored per channel and hour, counted per kind (flood guard)

_ROOT_URL = re.compile(r"https?://([^/?#]+)/?")


def _same_values(atype: ArtifactType, value: str) -> dict:
    """{type: [lowercased values]} that are the same thing on a case: a domain and the root URL of that domain."""
    v = value.lower()
    m = _ROOT_URL.fullmatch(v) if atype == ArtifactType.URL else None
    host = v if atype == ArtifactType.DOMAIN else (m.group(1) if m else None)
    if host is None:
        return {atype: [v]}
    return {ArtifactType.DOMAIN: [host],
            ArtifactType.URL: [f"{scheme}://{host}{tail}" for scheme in ("http", "https") for tail in ("", "/")]}


async def _on_case(db, case, atype: ArtifactType, value: str) -> bool:
    same = [(Artifact.artifact_type == t) & func.lower(Artifact.value).in_(vals)
            for t, vals in _same_values(atype, value).items()]
    row = (await db.execute(
        select(Artifact.id).join(CaseArtifact, CaseArtifact.artifact_id == Artifact.id).where(
            CaseArtifact.case_id == case.id, Artifact.tenant_id == case.tenant_id, or_(*same)).limit(1))).first()
    return row is not None


def declared_milestones(case) -> dict:
    """{key: datetime} for the milestones a person declared on the case (computed defaults are not listed)."""
    return {k: getattr(case, f"{k}_at") for k in KEYS if getattr(case, f"{k}_at") is not None}


def _rerender(db, s) -> None:
    if s.reply_ts:
        queue_card(db, s.tenant_id, "war_room.rerender", s.case_id, {"reply_ts": s.reply_ts, "channel": s.slack_channel})


async def _supersede(db, s) -> None:
    """A corrected time replaces the pending suggestion for the same milestone."""
    key = s.payload["key"]
    older = (await db.execute(select(WarRoomSuggestion).where(
        WarRoomSuggestion.tenant_id == s.tenant_id, WarRoomSuggestion.case_id == s.case_id,
        WarRoomSuggestion.kind == "milestone", WarRoomSuggestion.status == "pending", WarRoomSuggestion.id != s.id,
        or_(WarRoomSuggestion.value_key == key,  # rows from before the time was part of the key
            WarRoomSuggestion.value_key.startswith(f"{key}:", autoescape=True))))).scalars().all()
    for o in older:
        o.status, o.resolved_at = "superseded", datetime.now(timezone.utc)
        _rerender(db, o)


async def record_candidates(db, case, candidates: list, *, channel: str, source_ts: str,
                            permalink: Optional[str], author: Optional[dict] = None) -> list:
    """Insert new pending suggestions; `author` ({"slack_user", "author"}) is kept on the payload for provenance.
    Everything within the hourly cap is stored: split_for_reply decides what the Slack reply shows.
    Does not commit."""
    since = datetime.now(timezone.utc) - timedelta(hours=1)
    used = dict((await db.execute(select(WarRoomSuggestion.kind, func.count()).where(
        WarRoomSuggestion.slack_channel == channel, WarRoomSuggestion.created_at >= since,
        WarRoomSuggestion.status != "dismissed").group_by(WarRoomSuggestion.kind))).all())
    # milestones have their own budget, so a burst of indicators never crowds one out
    budget = {k: MAX_PER_CHANNEL_HOUR - used.get(k, 0) for k in ("artifact", "milestone")}
    existing = set((await db.execute(select(WarRoomSuggestion.kind, WarRoomSuggestion.value_key).where(
        WarRoomSuggestion.tenant_id == case.tenant_id, WarRoomSuggestion.case_id == case.id))).all())
    out = []
    for c in candidates:
        if budget.get(c.kind, 0) <= 0 or (c.kind, c.value_key) in existing:
            continue
        if c.kind == "artifact":
            try:
                atype = ArtifactType(c.payload["type"])
            except (KeyError, ValueError):
                continue
            if await _on_case(db, case, atype, c.payload["value"]):
                continue
        else:
            try:
                if getattr(case, f"{c.payload['key']}_at") == datetime.fromisoformat(c.payload["at"]):
                    continue  # the case already says exactly this
            except (KeyError, AttributeError, TypeError, ValueError):
                continue
        existing.add((c.kind, c.value_key))
        s = WarRoomSuggestion(tenant_id=case.tenant_id, case_id=case.id, kind=c.kind,
                              payload={**c.payload, **(author or {})},
                              value_key=c.value_key, status="pending", slack_channel=channel,
                              source_ts=source_ts, permalink=permalink)
        try:
            async with db.begin_nested():  # a concurrent same-value insert skips only this candidate
                db.add(s)
                await db.flush()
        except IntegrityError:
            continue
        if c.kind == "milestone":
            await _supersede(db, s)
        budget[c.kind] -= 1
        out.append(s)
    return out


def split_for_reply(rows: list) -> tuple:
    """(shown, more): one Slack reply carries the first MAX_PER_MESSAGE artifacts and every milestone of a
    message; `more` counts the pending rest, which is answered in the web UI."""
    arts = [r for r in rows if r.kind != "milestone"]
    shown = arts[:MAX_PER_MESSAGE] + [r for r in rows if r.kind == "milestone"]
    return shown, sum(1 for r in arts[MAX_PER_MESSAGE:] if r.status == "pending")


def _source(s) -> str:
    """Where an accepted suggestion came from: Slack author and message link."""
    p = s.payload or {}
    who = " ".join(x for x in (p.get("author"), f"({p['slack_user']})" if p.get("slack_user") else None) if x)
    return "From Slack war room" + (f", posted by {who}" if who else "") + (f": {s.permalink}" if s.permalink else "")


async def resolve_suggestion(db, **kw) -> WarRoomSuggestion:
    return (await resolve_suggestion_ex(db, **kw))[0]


async def resolve_suggestion_ex(db, *, tenant_id: int, suggestion_id: int, accept: bool, user,
                                slack_user_id: Optional[str] = None, rerender: bool = True) -> tuple:
    """Returns (suggestion, applied, conflict). applied is False when it was already resolved before this call.
    conflict is the reason a milestone could not be set: the suggestion then stays pending (reason on its
    payload) so it can be accepted again once the other milestone is fixed."""
    s = (await db.execute(select(WarRoomSuggestion).where(
        WarRoomSuggestion.id == suggestion_id, WarRoomSuggestion.tenant_id == tenant_id).with_for_update()
        .execution_options(populate_existing=True))).scalars().first()
    if s is None:
        raise LookupError("suggestion not found")
    if s.status != "pending":
        return s, False, None
    case = (await db.execute(select(Case).where(Case.id == s.case_id, Case.tenant_id == tenant_id))).scalars().first()
    if case is None:
        raise LookupError("case not found")
    status, conflict, source = "dismissed", None, _source(s)
    if accept:
        status = "accepted"
        if s.kind == "artifact":
            await add_artifact_to_case(db, case=case, artifact_type=ArtifactType(s.payload["type"]),
                                       value=s.payload["value"], description=source, user_id=user.id, source=source)
        elif s.kind == "milestone":
            try:
                at = datetime.fromisoformat(s.payload["at"])
                await set_milestones(db, case, {f"{s.payload['key']}_at": at}, user.id, source=source)
            except MilestoneConflict as e:
                status, conflict = "conflict", str(e)
        else:
            raise LookupError("unknown suggestion kind")
    if conflict:
        s.payload = {**s.payload, "conflict": conflict}
    else:
        s.status = status
        s.payload = {k: v for k, v in s.payload.items() if k != "conflict"}
        s.resolved_by_user_id = user.id
        s.resolved_at = datetime.now(timezone.utc)
    await create_audit_log(db=db, entity_type="war_room_suggestion", entity_id=s.id,
                           action=f"war_room_suggestion_{status}", tenant_id=tenant_id, user_id=user.id,
                           changes={"case_id": s.case_id, "kind": s.kind, "value_key": s.value_key,
                                    **({"permalink": s.permalink} if s.permalink else {}),
                                    **({"slack_user_id": slack_user_id} if slack_user_id else {})})
    if rerender:
        _rerender(db, s)
    await db.flush()
    return s, True, conflict


async def resolve_many(db, *, tenant_id: int, suggestion_ids: list, accept: bool, user,
                       slack_user_id: Optional[str] = None) -> list:
    """Resolve several suggestions in one transaction: [(suggestion, applied, conflict)]. A milestone conflict
    is reported on its row and the rest still go through; unknown ids are skipped."""
    out = []
    for sid in sorted(set(suggestion_ids)):  # one lock order for concurrent bulk calls
        try:
            out.append(await resolve_suggestion_ex(db, tenant_id=tenant_id, suggestion_id=sid, accept=accept, user=user,
                                                   slack_user_id=slack_user_id, rerender=False))
        except LookupError:
            continue
    replies = {}
    for s, applied, _ in out:
        if applied:
            replies.setdefault((s.slack_channel, s.reply_ts), s)
    for s in replies.values():  # one redraw per Slack reply, not one per row
        _rerender(db, s)
    return out


async def reply_pending_ids(db, tenant_id: int, anchor_id: int) -> list:
    """Ids of the pending suggestions in the Slack reply that carries `anchor_id` ("Add all" / "Dismiss all")."""
    a = (await db.execute(select(WarRoomSuggestion).where(
        WarRoomSuggestion.id == anchor_id, WarRoomSuggestion.tenant_id == tenant_id))).scalars().first()
    if a is None or not a.reply_ts:
        raise LookupError("suggestion not found")
    return list((await db.execute(select(WarRoomSuggestion.id).where(
        WarRoomSuggestion.tenant_id == tenant_id, WarRoomSuggestion.slack_channel == a.slack_channel,
        WarRoomSuggestion.reply_ts == a.reply_ts, WarRoomSuggestion.status == "pending"))).scalars().all())
