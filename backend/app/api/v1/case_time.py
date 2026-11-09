"""Per-case time tracking: heartbeat capture, manual entries, edit/delete. Durations are derived, never stored."""
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from app.models.case import Case
from app.models.case_time_entry import CaseTimeEntry
from app.models.user import User
from app.schemas.case_time import TimeEntryIn, TimeEntryOut, TimeEntryPatch, TimeSummary
from app.utils.audit import create_audit_log

router = APIRouter()
HEARTBEAT_GAP_SECONDS = 300


def _secs(e: CaseTimeEntry) -> int:
    return max(0, int((e.ended_at - e.started_at).total_seconds()))


def _out(e: CaseTimeEntry) -> TimeEntryOut:
    return TimeEntryOut(id=e.id, user_id=e.user_id, started_at=e.started_at, ended_at=e.ended_at,
                        source=e.source, note=e.note, seconds=_secs(e))


async def _case(db: AsyncSession, case_id: int, tid: int) -> Case:
    c = (await db.execute(select(Case).where(Case.id == case_id, Case.tenant_id == tid))).scalars().first()
    if c is None:
        raise HTTPException(status_code=404, detail="Case not found")
    return c


async def _entry(db: AsyncSession, case_id: int, entry_id: int, tid: int) -> CaseTimeEntry:
    e = (await db.execute(select(CaseTimeEntry).where(
        CaseTimeEntry.id == entry_id, CaseTimeEntry.case_id == case_id, CaseTimeEntry.tenant_id == tid)
    )).scalars().first()
    if e is None:
        raise HTTPException(status_code=404, detail="Time entry not found")
    return e


def _may_edit(user: User, e: CaseTimeEntry) -> None:
    if e.user_id != user.id and deps._active_role(user) not in ("super_admin", "admin"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")


@router.post("/{case_id}/time/heartbeat", status_code=204)
async def heartbeat(case_id: int, db: AsyncSession = Depends(deps.get_db),
                    user: User = Depends(deps.require_analyst_or_above),
                    tenant_id: int = Depends(deps.get_effective_tenant_id)) -> Response:
    await _case(db, case_id, tenant_id)
    now = datetime.now(timezone.utc)
    last = (await db.execute(
        select(CaseTimeEntry).where(
            CaseTimeEntry.case_id == case_id, CaseTimeEntry.tenant_id == tenant_id,
            CaseTimeEntry.user_id == user.id, CaseTimeEntry.source == "auto")
        .order_by(CaseTimeEntry.ended_at.desc(), CaseTimeEntry.id.desc()).limit(1).with_for_update()
    )).scalars().first()
    if last is not None and last.ended_at >= now - timedelta(seconds=HEARTBEAT_GAP_SECONDS):
        last.ended_at = max(last.ended_at, now)
    else:
        db.add(CaseTimeEntry(tenant_id=tenant_id, case_id=case_id, user_id=user.id,
                             started_at=now, ended_at=now, source="auto"))
    await db.commit()
    return Response(status_code=204)


@router.get("/{case_id}/time", response_model=TimeSummary)
async def list_time(case_id: int, db: AsyncSession = Depends(deps.get_db),
                    user: User = Depends(deps.require_analyst_or_above),
                    tenant_id: int = Depends(deps.get_effective_tenant_id)) -> Any:
    await _case(db, case_id, tenant_id)
    rows = (await db.execute(
        select(CaseTimeEntry, User.email).join(User, User.id == CaseTimeEntry.user_id)
        .where(CaseTimeEntry.case_id == case_id, CaseTimeEntry.tenant_id == tenant_id)
        .order_by(CaseTimeEntry.started_at.desc(), CaseTimeEntry.id.desc()))).all()
    per: dict = {}
    for e, email in rows:
        d = per.setdefault(e.user_id, {"user_id": e.user_id, "email": email, "seconds": 0})
        d["seconds"] += _secs(e)
    return {"entries": [_out(e) for e, _ in rows], "total_seconds": sum(d["seconds"] for d in per.values()),
            "by_user": sorted(per.values(), key=lambda d: -d["seconds"])}


@router.post("/{case_id}/time", status_code=201, response_model=TimeEntryOut)
async def create_time(case_id: int, body: TimeEntryIn, db: AsyncSession = Depends(deps.get_db),
                      user: User = Depends(deps.require_analyst_or_above),
                      tenant_id: int = Depends(deps.get_effective_tenant_id)) -> Any:
    await _case(db, case_id, tenant_id)
    e = CaseTimeEntry(tenant_id=tenant_id, case_id=case_id, user_id=user.id, started_at=body.started_at,
                      ended_at=body.ended_at, source="manual", note=body.note)
    db.add(e)
    await db.flush()
    await create_audit_log(db, "time_entry", e.id, "created", tenant_id, user_id=user.id,
                           changes={"case_id": case_id, "seconds": _secs(e), "source": "manual"})
    await db.commit()
    await db.refresh(e)
    return _out(e)


@router.patch("/{case_id}/time/{entry_id}", response_model=TimeEntryOut)
async def update_time(case_id: int, entry_id: int, body: TimeEntryPatch, db: AsyncSession = Depends(deps.get_db),
                      user: User = Depends(deps.require_analyst_or_above),
                      tenant_id: int = Depends(deps.get_effective_tenant_id)) -> Any:
    await _case(db, case_id, tenant_id)
    e = await _entry(db, case_id, entry_id, tenant_id)
    _may_edit(user, e)
    fields = body.model_dump(exclude_unset=True)
    for k in ("started_at", "ended_at"):
        if k in fields and (fields[k] is None or fields[k].tzinfo is None):
            raise HTTPException(status_code=422, detail=f"{k} must be a timezone-aware timestamp")
    start, end = fields.get("started_at", e.started_at), fields.get("ended_at", e.ended_at)
    if end <= start:
        raise HTTPException(status_code=422, detail="ended_at must be after started_at")
    before = {"started_at": e.started_at.isoformat(), "ended_at": e.ended_at.isoformat(), "note": e.note}
    for k, v in fields.items():
        setattr(e, k, v)
    await create_audit_log(db, "time_entry", e.id, "updated", tenant_id, user_id=user.id,
                           changes={"case_id": case_id, "before": before, "fields": sorted(fields)})
    await db.commit()
    await db.refresh(e)
    return _out(e)


@router.delete("/{case_id}/time/{entry_id}", status_code=204)
async def delete_time(case_id: int, entry_id: int, db: AsyncSession = Depends(deps.get_db),
                      user: User = Depends(deps.require_analyst_or_above),
                      tenant_id: int = Depends(deps.get_effective_tenant_id)) -> Response:
    await _case(db, case_id, tenant_id)
    e = await _entry(db, case_id, entry_id, tenant_id)
    _may_edit(user, e)
    await create_audit_log(db, "time_entry", e.id, "deleted", tenant_id, user_id=user.id,
                           changes={"case_id": case_id, "owner_id": e.user_id, "seconds": _secs(e),
                                    "source": e.source})
    await db.delete(e)
    await db.commit()
    return Response(status_code=204)
