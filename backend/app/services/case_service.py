"""Case mutations shared by the HTTP API, workflow nodes and Slack buttons.

All functions only flush; the caller commits (and emits workflow events after commit).
"""
import json
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.artifact import Artifact, ArtifactType
from app.models.case import Case, CaseStatus, TimelineEvent
from app.models.case_artifact import CaseArtifact
from app.models.case_triage import CaseTriageResult
from app.notifications import service as notify
from app.notifications.mentions import canonicalize_mentions
from app.utils.audit import create_audit_log
from app.utils.copilot_heuristics import detect_indicators


async def create_case_record(db: AsyncSession, *, tenant_id: int, data: dict, user_id: Optional[int]) -> Tuple[Case, int]:
    case = Case(**data, tenant_id=tenant_id)
    if not case.owner_id and user_id:
        case.owner_id = user_id
    db.add(case)
    await db.flush()
    await create_audit_log(db=db, entity_type="case", entity_id=case.id, action="create",
                           tenant_id=tenant_id, user_id=user_id)
    await notify.on_case_created(db, case, user_id)
    triage = CaseTriageResult(case_id=case.id, tenant_id=tenant_id, triggered_by="auto_on_create", status="pending")
    db.add(triage)
    await db.flush()
    return case, triage.id


def _plain(value: Any) -> Any:
    """JSON-safe form of a case field value (enums -> their value) for audit logs and trigger payloads."""
    if isinstance(value, Enum):  # before str: CaseStatus etc. are str subclasses
        return value.value
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    return str(value)


async def apply_case_update(db: AsyncSession, *, case: Case, update_data: dict, user_id: Optional[int]) -> dict:
    old_status = case.status
    changes = {}
    for field, value in update_data.items():
        old_value = getattr(case, field)
        if old_value != value:
            changes[field] = {"from": _plain(old_value), "to": _plain(value)}
            if field == "tags":
                before, after = changes[field]["from"] or [], changes[field]["to"] or []
                changes[field]["added"] = [t for t in after if t not in before]
                changes[field]["removed"] = [t for t in before if t not in after]
            elif field == "owner_id":
                changes["assignee"] = dict(changes[field])
        setattr(case, field, value)

    if "status" in changes:
        db.add(TimelineEvent(case_id=case.id, user_id=user_id, event_type="status_change",
                             content=f"Status changed from {changes['status']['from']} to {changes['status']['to']}"))
        new_status = update_data["status"]
        if new_status in (CaseStatus.RESOLVED, CaseStatus.CLOSED):
            if case.resolved_at is None:
                case.resolved_at = datetime.now(timezone.utc)
        else:
            case.resolved_at = None
        # First move off NEW stops the response SLA clock.
        if old_status == CaseStatus.NEW and case.acknowledged_at is None:
            case.acknowledged_at = datetime.now(timezone.utc)

    if "severity" in changes:
        db.add(TimelineEvent(case_id=case.id, user_id=user_id, event_type="severity_change",
                             content=f"Severity changed from {changes['severity']['from']} to {changes['severity']['to']}"))

    if changes:
        await create_audit_log(db=db, entity_type="case", entity_id=case.id, action="update",
                               tenant_id=case.tenant_id, user_id=user_id, changes=changes)
        await notify.on_case_update(db, case, changes, user_id)
    await db.flush()
    return changes


async def add_timeline_note(db: AsyncSession, *, case: Case, content: str, user_id: Optional[int],
                            event_type: str = "comment") -> TimelineEvent:
    if event_type == "comment" and user_id is not None:
        content, _ = await canonicalize_mentions(db, case.tenant_id, content)
    ev = TimelineEvent(case_id=case.id, user_id=user_id, event_type=event_type, content=content)
    db.add(ev)
    await db.flush()
    if event_type == "comment":
        await notify.on_comment(db, case, ev, user_id)
    return ev


async def edit_timeline_note(db: AsyncSession, *, case: Case, event: TimelineEvent, update_data: dict,
                             user_id: Optional[int]) -> TimelineEvent:
    previous, previous_type = event.content, event.event_type
    new_type = update_data.get("event_type", event.event_type)
    content = update_data.get("content", event.content)
    if new_type == "comment" and user_id is not None:
        content, _ = await canonicalize_mentions(db, case.tenant_id, content)
    event.event_type, event.content = new_type, content
    await db.flush()
    type_changed = new_type != previous_type
    if new_type == "comment" and (content != previous or type_changed):
        await notify.on_comment(db, case, event, user_id,
                                previous_content="" if type_changed else (previous or ""))
    return event


async def add_artifact_to_case(db: AsyncSession, *, case: Case, artifact_type: ArtifactType, value: str,
                               description: Optional[str], user_id: Optional[int], isolated: bool = False) -> Artifact:
    artifact = None
    if not isolated:
        artifact = (await db.execute(select(Artifact).where(
            Artifact.value == value, Artifact.artifact_type == artifact_type,
            Artifact.tenant_id == case.tenant_id, Artifact.isolated == False,  # noqa: E712
        ))).scalars().first()
    if artifact is None:
        artifact = Artifact(artifact_type=artifact_type, value=value, description=description,
                            isolated=isolated, tenant_id=case.tenant_id, created_by=user_id)
        db.add(artifact)
        await db.flush()

    linked = (await db.execute(select(CaseArtifact).where(
        CaseArtifact.case_id == case.id, CaseArtifact.artifact_id == artifact.id))).scalars().first()
    if not linked:
        db.add(CaseArtifact(case_id=case.id, artifact_id=artifact.id, added_by=user_id))
        db.add(TimelineEvent(case_id=case.id, user_id=user_id, event_type="artifact_added",
                             content=f"Added artifact: {value} ({artifact_type})"))
    await db.flush()
    return artifact


async def add_alert_artifacts(db: AsyncSession, *, case: Case, alert, user_id: Optional[int]) -> None:
    """Pull IOCs (ip/email/url/domain/hash) out of an alert payload onto the case."""
    text = json.dumps(alert.payload or {}, default=str)
    # ponytail: regex sweep over the JSON text, capped at 20; field mapping per source if this proves noisy.
    for ind in detect_indicators(text, limit=20):
        await add_artifact_to_case(db, case=case, artifact_type=ArtifactType(ind["artifact_type"]), value=ind["value"],
                                   description=f"From alert {alert.id}", user_id=user_id)
