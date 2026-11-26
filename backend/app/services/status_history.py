"""Correcting a case's status history: the status_change activity entries the incident glance draws its phase bar
from. A correction never changes the case's current status; it fixes the record of how the case got there, and
re-derives resolved_at (where the glance's resolve span and the default Recovered milestone end) from it."""
import re
from datetime import datetime, timezone
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.case import Case, CaseStatus, TimelineEvent
from app.utils.audit import create_audit_log

STATUS_RE = re.compile(r"^Status changed from (\w+) to (\w+)")  # as written by case_service.update_case
DONE = (CaseStatus.RESOLVED.value, CaseStatus.CLOSED.value)


def parse(event: TimelineEvent) -> Optional[tuple]:
    m = STATUS_RE.match(event.content or "")
    return (m.group(1), m.group(2)) if m else None


async def history(db: AsyncSession, case_id: int) -> list:
    rows = (await db.execute(select(TimelineEvent).where(
        TimelineEvent.case_id == case_id, TimelineEvent.event_type == "status_change")
        .order_by(TimelineEvent.created_at, TimelineEvent.id))).scalars().all()
    return [(e, p) for e in rows if (p := parse(e))]


async def resync_resolved_at(db: AsyncSession, case: Case) -> Optional[dict]:
    """resolved_at = when the case last entered resolved/closed and stayed there, per the (corrected) history.
    Only for a case that is resolved/closed now and whose history ends in its current status; otherwise the history
    can't say, and resolved_at is left alone. Returns {"from", "to"} when it changed."""
    status = case.status.value if isinstance(case.status, CaseStatus) else str(case.status)
    rows = await history(db, case.id)
    if status not in DONE or not rows or rows[-1][1][1] != status:
        return None
    at = None
    for e, (_, to) in reversed(rows):
        if to not in DONE:
            break
        at = e.created_at
    if at is None or at == case.resolved_at:
        return None
    before, case.resolved_at = case.resolved_at, at
    return {"from": before.isoformat() if before else None, "to": at.isoformat()}


def _check_statuses(*values: str) -> None:
    valid = {s.value for s in CaseStatus}
    for v in values:
        if v not in valid:
            raise HTTPException(status_code=422, detail=f"Unknown status '{v}'")


async def correct(db: AsyncSession, *, case: Case, event: TimelineEvent, at: datetime, from_status: str,
                  to_status: str, user_id: int) -> Optional[dict]:
    """Rewrite one status change's time and statuses. Anything after the first line (reason, outcome, closing note)
    is kept. Audited with the before and after."""
    _check_statuses(from_status, to_status)
    if from_status == to_status:
        raise HTTPException(status_code=422, detail="From and to must differ")
    if at.tzinfo is None:
        raise HTTPException(status_code=422, detail="The time must carry a timezone")
    if at > datetime.now(timezone.utc):
        raise HTTPException(status_code=422, detail="A status change can't be in the future")
    if case.created_at and at < case.created_at:
        raise HTTPException(status_code=422, detail="A status change can't be before the case was created")
    before = {"at": event.created_at.isoformat(), "content": event.content}
    event.content = STATUS_RE.sub(f"Status changed from {from_status} to {to_status}", event.content, count=1)
    event.created_at = at
    event.updated_at = datetime.now(timezone.utc)
    await db.flush()
    resolved = await resync_resolved_at(db, case)
    await create_audit_log(db=db, entity_type="case", entity_id=case.id, action="status_history_corrected",
                           tenant_id=case.tenant_id, user_id=user_id,
                           changes={"entry_id": event.id, "before": before,
                                    "after": {"at": at.isoformat(), "content": event.content},
                                    **({"resolved_at": resolved} if resolved else {})})
    return resolved


async def remove(db: AsyncSession, *, case: Case, event: TimelineEvent, user_id: int) -> Optional[dict]:
    """Delete one status change; the audit keeps what it said, when, and who wrote it."""
    await create_audit_log(db=db, entity_type="case", entity_id=case.id, action="status_history_deleted",
                           tenant_id=case.tenant_id, user_id=user_id,
                           changes={"entry_id": event.id, "at": event.created_at.isoformat(),
                                    "content": event.content, "author_id": event.user_id, "via": event.via})
    await db.delete(event)
    await db.flush()
    resolved = await resync_resolved_at(db, case)
    if resolved:
        await create_audit_log(db=db, entity_type="case", entity_id=case.id, action="status_history_corrected",
                               tenant_id=case.tenant_id, user_id=user_id, changes={"resolved_at": resolved})
    return resolved
