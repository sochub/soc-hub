"""SOC-bot suggestions lifted from a war-room channel: recording (dedupe + caps) and accept/dismiss."""
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.models.artifact import Artifact, ArtifactType
from app.models.case import Case
from app.models.case_artifact import CaseArtifact
from app.models.war_room_suggestion import WarRoomSuggestion
from app.services.case_service import add_artifact_to_case
from app.services.milestones import MilestoneConflict, set_milestones
from app.services.war_room_events import queue_card
from app.utils.audit import create_audit_log

MAX_PER_MESSAGE = 5
MAX_PER_CHANNEL_HOUR = 20


async def _on_case(db, case, atype: ArtifactType, value: str) -> bool:
    row = (await db.execute(
        select(Artifact.id).join(CaseArtifact, CaseArtifact.artifact_id == Artifact.id).where(
            CaseArtifact.case_id == case.id, Artifact.tenant_id == case.tenant_id,
            Artifact.artifact_type == atype, func.lower(Artifact.value) == value.lower()).limit(1))).first()
    return row is not None


async def record_candidates(db, case, candidates: list, *, channel: str, source_ts: str,
                            permalink: Optional[str]) -> list:
    """Insert new pending suggestions. Does not commit."""
    since = datetime.now(timezone.utc) - timedelta(hours=1)
    used = (await db.execute(select(func.count()).select_from(WarRoomSuggestion).where(
        WarRoomSuggestion.slack_channel == channel, WarRoomSuggestion.created_at >= since))).scalar() or 0
    budget = min(MAX_PER_MESSAGE, MAX_PER_CHANNEL_HOUR - used)
    existing = set((await db.execute(select(WarRoomSuggestion.kind, WarRoomSuggestion.value_key).where(
        WarRoomSuggestion.tenant_id == case.tenant_id, WarRoomSuggestion.case_id == case.id))).all())
    out = []
    for c in candidates:
        if len(out) >= budget:
            break
        if (c.kind, c.value_key) in existing:
            continue
        if c.kind == "artifact":
            try:
                atype = ArtifactType(c.payload["type"])
            except (KeyError, ValueError):
                continue
            if await _on_case(db, case, atype, c.payload["value"]):
                continue
        existing.add((c.kind, c.value_key))
        s = WarRoomSuggestion(tenant_id=case.tenant_id, case_id=case.id, kind=c.kind, payload=c.payload,
                              value_key=c.value_key, status="pending", slack_channel=channel,
                              source_ts=source_ts, permalink=permalink)
        try:
            async with db.begin_nested():  # a concurrent same-value insert skips only this candidate
                db.add(s)
                await db.flush()
        except IntegrityError:
            continue
        out.append(s)
    return out


async def resolve_suggestion(db, *, tenant_id: int, suggestion_id: int, accept: bool, user,
                             slack_user_id: Optional[str] = None) -> WarRoomSuggestion:
    s = (await db.execute(select(WarRoomSuggestion).where(
        WarRoomSuggestion.id == suggestion_id, WarRoomSuggestion.tenant_id == tenant_id).with_for_update()
        .execution_options(populate_existing=True))).scalars().first()
    if s is None:
        raise LookupError("suggestion not found")
    if s.status != "pending":
        return s
    case = (await db.execute(select(Case).where(Case.id == s.case_id, Case.tenant_id == tenant_id))).scalars().first()
    if case is None:
        raise LookupError("case not found")
    status = "dismissed"
    if accept:
        status = "accepted"
        if s.kind == "artifact":
            await add_artifact_to_case(db, case=case, artifact_type=ArtifactType(s.payload["type"]),
                                       value=s.payload["value"], description="From Slack war room",
                                       user_id=user.id)
        elif s.kind == "milestone":
            try:
                at = datetime.fromisoformat(s.payload["at"])
                await set_milestones(db, case, {f"{s.payload['key']}_at": at}, user.id)
            except MilestoneConflict:
                status = "conflict"
        else:
            raise LookupError("unknown suggestion kind")
    s.status = status
    s.resolved_by_user_id = user.id
    s.resolved_at = datetime.now(timezone.utc)
    await create_audit_log(db=db, entity_type="war_room_suggestion", entity_id=s.id,
                           action=f"war_room_suggestion_{status}", tenant_id=tenant_id, user_id=user.id,
                           changes={"case_id": s.case_id, "kind": s.kind, "value_key": s.value_key,
                                    **({"slack_user_id": slack_user_id} if slack_user_id else {})})
    if s.reply_ts:
        queue_card(db, tenant_id, "war_room.rerender", case.id,
                   {"reply_ts": s.reply_ts, "channel": s.slack_channel})
    await db.flush()
    return s
