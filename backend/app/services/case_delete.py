"""Deleting a case: what goes with it (the warning the UI shows) and the delete itself.

Kept: the audit trail (audit_logs has no FK to cases), tenant-level artifacts and IOCs (only unlinked), and the
alerts, which go back to the triage queue as pending. Gone: everything that belongs to the case alone.
"""
import logging

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.case import Alert, Case, CaseLink, TimelineEvent
from app.models.case_artifact import CaseArtifact
from app.models.case_attachment import CaseAttachment
from app.models.case_guest import CaseGuest
from app.models.case_task import CaseTask
from app.models.containment import ContainmentAction
from app.models.copilot_session import CopilotSession
from app.models.ioc import IOC
from app.utils.audit import create_audit_log

logger = logging.getLogger(__name__)


async def _count(db: AsyncSession, model, *where) -> int:
    return (await db.execute(select(func.count()).select_from(model).where(*where))).scalar() or 0


async def delete_preview(db: AsyncSession, case: Case) -> dict:
    """Counts for the confirmation dialog; `warnings` are the things a deletion can't undo outside SOC Hub."""
    c = case.id
    out = {
        "case_id": c, "title": case.title,
        "tasks": await _count(db, CaseTask, CaseTask.case_id == c),
        "activity_entries": await _count(db, TimelineEvent, TimelineEvent.case_id == c),
        "artifacts": await _count(db, CaseArtifact, CaseArtifact.case_id == c),
        "evidence_files": await _count(db, CaseAttachment, CaseAttachment.case_id == c),
        "alerts": await _count(db, Alert, Alert.case_id == c),
        "iocs": await _count(db, IOC, IOC.case_id == c),
        "guests": await _count(db, CaseGuest, CaseGuest.case_id == c),
        "tickets": await _count(db, CaseLink, CaseLink.case_id == c, CaseLink.connection_id.isnot(None)),
        "active_containment": await _count(db, ContainmentAction, ContainmentAction.case_id == c,
                                           ContainmentAction.status == "active"),
        "war_room": bool(case.slack_channel_id),
    }
    warnings = []
    if out["active_containment"]:
        warnings.append(f"{out['active_containment']} containment action(s) are still active (hosts isolated, users "
                        "suspended or IPs blocked). Deleting the case does not release them: undo them first.")
    if out["evidence_files"]:
        warnings.append(f"{out['evidence_files']} evidence file(s) are permanently deleted from storage.")
    if out["tickets"]:
        warnings.append(f"{out['tickets']} linked ticket(s) stay open in the ticketing system; only the link is removed.")
    if out["war_room"]:
        warnings.append("The Slack war room channel is not archived: archive it in Slack.")
    if out["guests"]:
        warnings.append(f"{out['guests']} external guest(s) lose access to this case.")
    if out["alerts"]:
        warnings.append(f"{out['alerts']} alert(s) go back to the Alerts queue as pending.")
    out["warnings"] = warnings
    return out


async def delete_case(db: AsyncSession, case: Case, user_id: int) -> list:
    """Delete the case and what belongs only to it; audit ``case_deleted`` with the preview counts.

    Flushes; the caller commits, then removes the returned evidence storage keys (after the commit, so a failed delete
    never loses files)."""
    summary = await delete_preview(db, case)
    c, tenant_id = case.id, case.tenant_id
    keys = list((await db.execute(select(CaseAttachment.storage_key).where(CaseAttachment.case_id == c))).scalars())
    # The five references without ON DELETE: unlink what is tenant-level, delete what is the case's own.
    await db.execute(update(Alert).where(Alert.case_id == c, Alert.status == "promoted").values(status="pending"))
    await db.execute(update(Alert).where(Alert.case_id == c).values(case_id=None))
    await db.execute(update(IOC).where(IOC.case_id == c).values(case_id=None))
    await db.execute(delete(CaseArtifact).where(CaseArtifact.case_id == c))
    await db.execute(delete(TimelineEvent).where(TimelineEvent.case_id == c))
    await db.execute(delete(CopilotSession).where(CopilotSession.case_id == c))
    await db.execute(delete(Case).where(Case.id == c, Case.tenant_id == tenant_id))  # the rest cascades
    await create_audit_log(db=db, entity_type="case", entity_id=c, action="case_deleted", tenant_id=tenant_id,
                           user_id=user_id, changes={k: v for k, v in summary.items() if k != "warnings"})
    await db.flush()
    return keys


async def remove_evidence(keys: list) -> None:
    from app.storage import get_storage
    storage = get_storage()
    for key in keys:
        try:
            await storage.delete(key)
        except Exception:  # the row is gone; an orphaned object is logged, never fatal
            logger.warning("evidence object not removed after case delete: %s", key)
