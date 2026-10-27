"""Case mutations shared by the HTTP API, workflow nodes and Slack buttons.

All functions only flush; the caller commits (and emits workflow events after commit).
"""
import json
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional, Tuple

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.guests import notify as guest_notify
from app.models.artifact import Artifact, ArtifactType
from app.models.artifact_type_definition import ArtifactTypeDefinition
from app.models.case import Case, CaseStatus, TimelineEvent
from app.models.case_artifact import CaseArtifact
from app.models.case_triage import CaseTriageResult
from app.models.membership import TenantMembership
from app.notifications import service as notify
from app.notifications.mentions import canonicalize_mentions
from app.services.artifact_types import payload_values, resolve_type
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


async def check_case_update(db: AsyncSession, *, case: Case, data: dict, tenant_id: int, role: str) -> None:
    """Guards for analyst-driven case edits (REST/MCP); Slack/workflow/copilot paths skip them.

    422 closing without an outcome, 403 guest_summary below analyst, 422 new owner not a tenant member."""
    # ponytail: analysts must record an outcome to close; Slack/workflow/copilot paths can't supply one.
    if data.get("status") == CaseStatus.CLOSED and data.get("outcome", case.outcome) is None:
        raise HTTPException(status_code=422, detail="outcome is required to close a case")
    if "guest_summary" in data and role not in ("super_admin", "admin", "analyst"):
        raise HTTPException(status_code=403, detail="Only analysts can edit the guest summary")
    owner_id = data.get("owner_id")
    if owner_id is not None and owner_id != case.owner_id and not (await db.execute(select(TenantMembership.id).where(
            TenantMembership.user_id == owner_id, TenantMembership.tenant_id == tenant_id))).first():
        raise HTTPException(status_code=422, detail="Owner is not a member of this tenant")


async def apply_case_update(db: AsyncSession, *, case: Case, update_data: dict, user_id: Optional[int],
                            reason: Optional[str] = None) -> dict:
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
            if field in ("status", "severity", "owner_id") and user_id is not None:
                changes[field]["by"] = user_id  # lets the war-room card name who made the change
            if field in ("status", "severity") and reason:
                changes[field]["reason"] = reason
        setattr(case, field, value)

    if "status" in changes:
        content = f"Status changed from {changes['status']['from']} to {changes['status']['to']}"
        if reason:
            content += f" — {reason}"
        if update_data["status"] == CaseStatus.CLOSED:
            if case.outcome:
                content += f"\n\n**Outcome:** {_plain(case.outcome).replace('_', ' ')}"
            if case.closing_note:
                content += f"\n\n**Closing note:** {case.closing_note}"
        db.add(TimelineEvent(case_id=case.id, user_id=user_id, event_type="status_change", content=content))
        await guest_notify.enqueue(db, case, "status_change",
                                   f"Status changed to {str(changes['status']['to']).replace('_', ' ').title()}")
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
                             content=f"Severity changed from {changes['severity']['from']} to {changes['severity']['to']}"
                             + (f" — {reason}" if reason else "")))

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


async def delete_timeline_note(db: AsyncSession, *, case: Case, event: TimelineEvent, user_id: Optional[int]) -> None:
    """Delete an activity entry; the case audit keeps what it said and who wrote it. The caller checks who may."""
    await create_audit_log(db=db, entity_type="case", entity_id=case.id, action="note_deleted",
                           tenant_id=case.tenant_id, user_id=user_id,
                           changes={"entry_id": event.id, "event_type": event.event_type, "author_id": event.user_id,
                                    "content": event.content})
    await db.delete(event)
    await db.flush()


async def add_artifact_to_case(db: AsyncSession, *, case: Case, artifact_type: ArtifactType, value: str,
                               description: Optional[str], user_id: Optional[int], isolated: bool = False,
                               custom_type=None) -> Artifact:
    custom_type_id = custom_type.id if custom_type is not None else None
    artifact = None
    if not isolated:
        artifact = (await db.execute(select(Artifact).where(
            Artifact.value == value, Artifact.artifact_type == artifact_type,
            Artifact.custom_type_id == custom_type_id,  # None renders IS NULL
            Artifact.tenant_id == case.tenant_id, Artifact.isolated == False,  # noqa: E712
        ))).scalars().first()
    if artifact is None:
        artifact = Artifact(artifact_type=artifact_type, value=value, description=description, isolated=isolated,
                            tenant_id=case.tenant_id, created_by=user_id, custom_type_id=custom_type_id)
        artifact.custom_type = custom_type
        db.add(artifact)
        await db.flush()

    linked = (await db.execute(select(CaseArtifact).where(
        CaseArtifact.case_id == case.id, CaseArtifact.artifact_id == artifact.id))).scalars().first()
    if not linked:
        db.add(CaseArtifact(case_id=case.id, artifact_id=artifact.id, added_by=user_id))
        if not (custom_type is not None and custom_type.private):  # private values never reach the timeline
            db.add(TimelineEvent(case_id=case.id, user_id=user_id, event_type="artifact_added",
                                 content=f"Added artifact: {value} ({artifact.type_key})"))
    await db.flush()
    return artifact


async def add_artifact_by_key(db: AsyncSession, *, case: Case, key: str, value: str, description: Optional[str],
                              user_id: Optional[int], isolated: bool = False) -> Artifact:
    """Add by type key (built-in or tenant custom). Raises UnknownArtifactType."""
    atype, custom = await resolve_type(db, case.tenant_id, key)
    return await add_artifact_to_case(db, case=case, artifact_type=atype, value=value, description=description,
                                      user_id=user_id, isolated=isolated, custom_type=custom)


def _artifact_audit(artifact: Artifact) -> dict:
    """What an audit row may say about an artifact: private (workflow-only) values stay out of the log."""
    return {"type": artifact.type_key} if artifact.is_private else {"type": artifact.type_key, "value": artifact.value}


async def update_artifact(db: AsyncSession, *, artifact: Artifact, data: dict, user_id: Optional[int] = None) -> Artifact:
    """Apply ``ArtifactUpdate.model_dump(exclude_unset=True)``; ``artifact_type`` is a type key. Raises
    UnknownArtifactType. Audited as ``artifact``/``update`` with from/to per changed field."""
    data = dict(data)
    before = {"type": artifact.type_key, **{f: getattr(artifact, f) for f in data if f != "artifact_type"}}
    if "artifact_type" in data:
        atype, custom = await resolve_type(db, artifact.tenant_id, data.pop("artifact_type"))
        artifact.artifact_type, artifact.custom_type_id, artifact.custom_type = atype, (custom.id if custom else None), custom
    for field, value in data.items():
        setattr(artifact, field, value)
    await db.flush()
    after = {"type": artifact.type_key, **{f: getattr(artifact, f) for f in data}}
    changes = {f: {"from": before[f], "to": after[f]} for f in after if before.get(f) != after[f]}
    if artifact.is_private:
        changes = {f: "changed" for f in changes}  # never log a private value
    if changes:
        await create_audit_log(db=db, entity_type="artifact", entity_id=artifact.id, action="update",
                               tenant_id=artifact.tenant_id, user_id=user_id, changes=changes)
    return artifact


async def delete_artifact(db: AsyncSession, *, artifact: Artifact, user_id: Optional[int]) -> None:
    """Delete the artifact and all its case links; each linked case gets an ``artifact_removed`` entry."""
    linked_case_ids = (await db.execute(
        select(CaseArtifact.case_id).where(CaseArtifact.artifact_id == artifact.id))).scalars().all()
    await create_audit_log(db=db, entity_type="artifact", entity_id=artifact.id, action="delete",
                           tenant_id=artifact.tenant_id, user_id=user_id,
                           changes={**_artifact_audit(artifact), "case_ids": list(linked_case_ids)})
    if not artifact.is_private:  # private values never reach the timeline
        for cid in linked_case_ids:
            db.add(TimelineEvent(case_id=cid, user_id=user_id, event_type="artifact_removed",
                                 content=f"Deleted artifact: {artifact.value} ({artifact.type_key})"))
    await db.delete(artifact)  # cascade removes the junction rows
    await db.flush()


async def unlink_artifact(db: AsyncSession, *, artifact: Artifact, case_id: int, user_id: Optional[int]) -> None:
    """Unlink the artifact from one case (404 when not linked); an isolated artifact left with no case is deleted."""
    junction = (await db.execute(select(CaseArtifact).where(
        CaseArtifact.case_id == case_id, CaseArtifact.artifact_id == artifact.id))).scalars().first()
    if not junction:
        raise HTTPException(status_code=404, detail="Artifact is not linked to this case")
    await db.delete(junction)
    await create_audit_log(db=db, entity_type="case", entity_id=case_id, action="artifact_removed",
                           tenant_id=artifact.tenant_id, user_id=user_id,
                           changes={**_artifact_audit(artifact), "artifact_id": artifact.id})
    if not artifact.is_private:  # private values never reach the timeline
        db.add(TimelineEvent(case_id=case_id, user_id=user_id, event_type="artifact_removed",
                             content=f"Removed artifact: {artifact.value} ({artifact.type_key})"))
    await db.flush()
    if artifact.isolated:
        remaining = (await db.execute(select(func.count()).select_from(CaseArtifact).where(
            CaseArtifact.artifact_id == artifact.id))).scalar()
        if remaining == 0:
            await db.delete(artifact)
            await db.flush()


def _drop_path(obj, path: str) -> None:
    """Delete a dotted path from nested dicts in place (missing paths are ignored)."""
    *parents, last = path.split(".")
    for part in parents:
        obj = obj.get(part) if isinstance(obj, dict) else None
    if isinstance(obj, dict):
        obj.pop(last, None)


MAX_ALERT_SCAN_CHARS = 64_000  # bound the regex sweep over arbitrary webhook payloads


async def add_alert_artifacts(db: AsyncSession, *, case: Case, alert, user_id: Optional[int]) -> None:
    """Copy alert payload data onto the case: mapped custom types, plus IOC regex hits
    (ip/email/url/domain/hash) from every field not mapped to a private type."""
    defs = (await db.execute(select(ArtifactTypeDefinition).where(
        ArtifactTypeDefinition.tenant_id == case.tenant_id, ArtifactTypeDefinition.payload_key.isnot(None)))).scalars().all()
    mapped = [(d, payload_values(alert.payload, d.payload_key)) for d in defs]

    # Scan a copy with private-mapped fields removed. No value-based filtering: payload content must not be
    # able to suppress IOCs found in other fields, and the raw payload is visible on the alert anyway.
    scan = json.loads(json.dumps(alert.payload or {}, default=str))
    for d, _ in mapped:
        if d.private:
            _drop_path(scan, d.payload_key)
    text = json.dumps(scan, ensure_ascii=False)[:MAX_ALERT_SCAN_CHARS]
    # ponytail: regex sweep over the JSON text, capped at 20; field mapping per source if this proves noisy.
    for ind in detect_indicators(text, limit=20):
        await add_artifact_to_case(db, case=case, artifact_type=ArtifactType(ind["artifact_type"]), value=ind["value"],
                                   description=f"From alert {alert.id}", user_id=user_id)
    for d, vals in mapped:
        for v in vals:
            await add_artifact_to_case(db, case=case, artifact_type=ArtifactType.OTHER, value=v,
                                       description=f"From alert {alert.id}", user_id=user_id, custom_type=d)
