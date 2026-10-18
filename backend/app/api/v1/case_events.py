"""Investigation timeline: dated incident events per case, distinct from the activity log."""
from typing import Any, List
from zoneinfo import ZoneInfo
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from app.models.artifact import Artifact
from app.models.case import Alert, Case, TimelineEvent
from app.models.case_artifact import CaseArtifact
from app.models.case_event import CaseEvent
from app.models.user import User
from app.schemas.case_event import (CaseEventCreate, CaseEventOut, CaseEventUpdate, EventSuggestRequest,
                                   EventSuggestResponse)
from app.services import timeline_suggest
from app.utils.audit import create_audit_log

router = APIRouter()


async def _get_case(db: AsyncSession, case_id: int, tenant_id: int) -> Case:
    res = await db.execute(select(Case).where(Case.id == case_id, Case.tenant_id == tenant_id))
    case = res.scalars().first()
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    return case


async def _get_event(db: AsyncSession, case_id: int, event_id: int, tenant_id: int) -> CaseEvent:
    res = await db.execute(select(CaseEvent).where(
        CaseEvent.id == event_id, CaseEvent.case_id == case_id, CaseEvent.tenant_id == tenant_id))
    ev = res.scalars().first()
    if not ev:
        raise HTTPException(status_code=404, detail="Event not found")
    return ev


async def _case_artifacts(db: AsyncSession, case_id: int, tenant_id: int, ids: List[int]) -> List[Artifact]:
    """Only artifacts already attached to this case (and tenant) may be linked; anything else is a 400."""
    if not ids:
        return []
    res = await db.execute(
        select(Artifact).join(CaseArtifact, CaseArtifact.artifact_id == Artifact.id)
        .where(CaseArtifact.case_id == case_id, Artifact.tenant_id == tenant_id, Artifact.id.in_(set(ids))))
    found = res.scalars().unique().all()
    if len(found) != len(set(ids)):
        raise HTTPException(status_code=400, detail="artifact_ids must belong to this case")
    return list(found)


@router.get("/{case_id}/events", response_model=List[CaseEventOut])
async def list_events(
    *,
    db: AsyncSession = Depends(deps.get_db),
    case_id: int,
    current_user: User = Depends(deps.get_current_active_user),
    tenant_id: int = Depends(deps.get_effective_tenant_id),
) -> Any:
    await _get_case(db, case_id, tenant_id)
    # ponytail: whole timeline in one response, filtered client-side; paginate past a few thousand events.
    res = await db.execute(select(CaseEvent).where(CaseEvent.case_id == case_id)
                           .order_by(CaseEvent.event_date, CaseEvent.id))
    return res.scalars().all()


@router.post("/{case_id}/events", response_model=CaseEventOut, status_code=201)
async def add_event(
    *,
    db: AsyncSession = Depends(deps.get_db),
    case_id: int,
    body: CaseEventCreate,
    current_user: User = Depends(deps.require_analyst_or_above),
    tenant_id: int = Depends(deps.get_effective_tenant_id),
) -> Any:
    await _get_case(db, case_id, tenant_id)
    data = body.model_dump(exclude={"artifact_ids"})
    ev = CaseEvent(case_id=case_id, tenant_id=tenant_id, user_id=current_user.id, **data)
    ev.artifacts = await _case_artifacts(db, case_id, tenant_id, body.artifact_ids)
    db.add(ev)
    await db.flush()
    await create_audit_log(db=db, entity_type="case", entity_id=case_id, action="event_added",
                           tenant_id=tenant_id, user_id=current_user.id,
                           changes={"title": ev.title, "event_date": ev.event_date.isoformat()})
    await db.commit()
    await db.refresh(ev)
    return ev


@router.put("/{case_id}/events/{event_id}", response_model=CaseEventOut)
async def update_event(
    *,
    db: AsyncSession = Depends(deps.get_db),
    case_id: int,
    event_id: int,
    body: CaseEventUpdate,
    current_user: User = Depends(deps.require_analyst_or_above),
    tenant_id: int = Depends(deps.get_effective_tenant_id),
) -> Any:
    ev = await _get_event(db, case_id, event_id, tenant_id)
    data = body.model_dump(exclude_unset=True)
    ids = data.pop("artifact_ids", None)
    for k, v in data.items():
        setattr(ev, k, v)
    if ids is not None:
        ev.artifacts = await _case_artifacts(db, case_id, tenant_id, ids)
    await create_audit_log(db=db, entity_type="case", entity_id=case_id, action="event_updated",
                           tenant_id=tenant_id, user_id=current_user.id,
                           changes={"id": ev.id, "fields": sorted(data) + (["artifact_ids"] if ids is not None else [])})
    await db.commit()
    await db.refresh(ev)
    return ev


@router.delete("/{case_id}/events/{event_id}", status_code=204)
async def delete_event(
    *,
    db: AsyncSession = Depends(deps.get_db),
    case_id: int,
    event_id: int,
    current_user: User = Depends(deps.require_analyst_or_above),
    tenant_id: int = Depends(deps.get_effective_tenant_id),
) -> None:
    ev = await _get_event(db, case_id, event_id, tenant_id)
    await db.delete(ev)
    await create_audit_log(db=db, entity_type="case", entity_id=case_id, action="event_deleted",
                           tenant_id=tenant_id, user_id=current_user.id, changes={"id": event_id, "title": ev.title})
    await db.commit()


@router.post("/{case_id}/events/suggest", response_model=EventSuggestResponse)
async def suggest_events(
    case_id: int,
    body: EventSuggestRequest,
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.require_analyst_or_above),
    tenant_id: int = Depends(deps.get_effective_tenant_id),
) -> Any:
    """Propose timeline events from linked alerts, analyst notes and pasted text. Saves nothing."""
    case = await _get_case(db, case_id, tenant_id)
    alerts = (await db.execute(select(Alert).where(Alert.case_id == case_id, Alert.tenant_id == tenant_id)
                               .order_by(Alert.created_at.desc()).limit(50))).scalars().all()
    comments = (await db.execute(select(TimelineEvent).where(
        TimelineEvent.case_id == case_id, TimelineEvent.event_type == "comment")
        .order_by(TimelineEvent.created_at.desc()).limit(50))).scalars().all()
    existing = (await db.execute(select(CaseEvent).where(
        CaseEvent.case_id == case_id, CaseEvent.tenant_id == tenant_id))).scalars().all()
    artifacts = (await db.execute(select(Artifact).join(CaseArtifact, CaseArtifact.artifact_id == Artifact.id).where(
        CaseArtifact.case_id == case_id, Artifact.tenant_id == tenant_id))).scalars().all()

    parsed = timeline_suggest.from_alerts(alerts) + timeline_suggest.from_log_text(body.text or "", ZoneInfo(body.naive_tz))
    ai, ai_state = None, "off"
    if body.use_ai:
        ai = await timeline_suggest.from_ai(
            timeline_suggest.build_material(case, alerts, comments, body.text), tenant_id)
        ai_state = "used" if ai is not None else "unavailable"
    events = timeline_suggest.merge(ai, parsed, existing)
    for ev in events:  # link artifacts already on the case whose value the event mentions
        hay = " ".join(filter(None, (ev["title"], ev["content"], ev["raw"]))).lower()
        ev["artifact_ids"] = [a.id for a in artifacts if a.value and a.value.lower() in hay]
    return {"events": events, "ai": ai_state}
