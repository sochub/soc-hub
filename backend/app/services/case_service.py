"""Case mutations shared by the HTTP API, workflow nodes and Slack buttons.

All functions only flush; the caller commits (and emits workflow events after commit).
"""
from datetime import datetime, timezone
from typing import Optional, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.artifact import Artifact, ArtifactType
from app.models.case import Case, CaseStatus, TimelineEvent
from app.models.case_artifact import CaseArtifact
from app.models.case_triage import CaseTriageResult
from app.utils.audit import create_audit_log


async def create_case_record(db: AsyncSession, *, tenant_id: int, data: dict, user_id: Optional[int]) -> Tuple[Case, int]:
    case = Case(**data, tenant_id=tenant_id)
    if not case.owner_id and user_id:
        case.owner_id = user_id
    db.add(case)
    await db.flush()
    await create_audit_log(db=db, entity_type="case", entity_id=case.id, action="create",
                           tenant_id=tenant_id, user_id=user_id)
    triage = CaseTriageResult(case_id=case.id, tenant_id=tenant_id, triggered_by="auto_on_create", status="pending")
    db.add(triage)
    await db.flush()
    return case, triage.id


async def apply_case_update(db: AsyncSession, *, case: Case, update_data: dict, user_id: Optional[int]) -> dict:
    old_status = case.status
    changes = {}
    for field, value in update_data.items():
        old_value = getattr(case, field)
        if old_value != value:
            changes[field] = {"from": str(old_value) if old_value is not None else None,
                              "to": str(value) if value is not None else None}
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
    await db.flush()
    return changes


async def add_timeline_note(db: AsyncSession, *, case: Case, content: str, user_id: Optional[int],
                            event_type: str = "comment") -> TimelineEvent:
    ev = TimelineEvent(case_id=case.id, user_id=user_id, event_type=event_type, content=content)
    db.add(ev)
    await db.flush()
    return ev


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
