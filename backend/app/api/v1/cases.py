from typing import Any, List
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload

from app.api import deps
from app.models.case import Case, TimelineEvent
from app.models.user import User
from app.schemas import case as case_schema
from app.services.case_service import (
    add_timeline_note, apply_case_update, create_case_record, edit_timeline_note,
)
from app.utils.roles import resolve_active_role
from app.utils.sla import compute_sla_state, load_policy_overrides
from app.tasks.triage import run_case_triage_task
from app.workflows.events import emit_event

router = APIRouter()


async def _attach_sla(db: AsyncSession, tenant_id: int, cases: List[Case]) -> List[Case]:
    """Compute SLA status and set it as transient attributes on each case ORM
    object — the response schema reads them via from_attributes, same as any
    stored column."""
    if not cases:
        return cases
    policy_overrides = await load_policy_overrides(db, tenant_id)
    for case in cases:
        state = compute_sla_state(case, policy_overrides)
        case.sla_response_target_minutes = state["response_target_minutes"]
        case.sla_response_status = state["response_status"]
        case.sla_response_due_at = state["response_due_at"]
        case.sla_resolution_target_minutes = state["resolution_target_minutes"]
        case.sla_resolution_status = state["resolution_status"]
        case.sla_resolution_due_at = state["resolution_due_at"]
        case.sla_overall_status = state["overall_status"]
    return cases


@router.get("/", response_model=List[case_schema.Case])
async def read_cases(
    db: AsyncSession = Depends(deps.get_db),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    current_user: User = Depends(deps.get_current_active_user),
    tenant_id: int = Depends(deps.get_effective_tenant_id),
) -> Any:
    """Retrieve cases scoped to tenant."""
    result = await db.execute(
        select(Case)
        .options(selectinload(Case.timeline_events).selectinload(TimelineEvent.user))
        .where(Case.tenant_id == tenant_id)
        .offset(skip).limit(limit)
        .order_by(Case.created_at.desc())
    )
    cases = result.scalars().all()
    await _attach_sla(db, tenant_id, cases)
    return cases

@router.get("/tags", response_model=List[str])
async def read_tags(
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
    tenant_id: int = Depends(deps.get_effective_tenant_id),
) -> Any:
    """Retrieve all unique tags within the tenant."""
    result = await db.execute(
        select(Case.tags).where(Case.tenant_id == tenant_id)
    )
    all_tags = []
    for tags in result.scalars().all():
        if tags:
            all_tags.extend(tags)
    return list(set(all_tags))

@router.post("/", response_model=case_schema.Case)
async def create_case(
    *,
    db: AsyncSession = Depends(deps.get_db),
    case_in: case_schema.CaseCreate,
    current_user: User = Depends(deps.get_current_active_user),
    tenant_id: int = Depends(deps.get_effective_tenant_id),
) -> Any:
    """Create new case."""
    case, triage_id = await create_case_record(
        db, tenant_id=tenant_id, data=case_in.model_dump(), user_id=current_user.id,
    )
    await db.commit()

    # Kick off AI triage in the background; never blocks case creation.
    run_case_triage_task.delay(triage_id)
    emit_event(tenant_id, "case.created", case_id=case.id)

    # Re-load with eager relationships to avoid async lazy-load errors
    result = await db.execute(
        select(Case)
        .options(selectinload(Case.timeline_events).selectinload(TimelineEvent.user))
        .where(Case.id == case.id)
    )
    case = result.scalars().first()
    await _attach_sla(db, tenant_id, [case])

    return case

@router.get("/{case_id}", response_model=case_schema.Case)
async def read_case(
    *,
    db: AsyncSession = Depends(deps.get_db),
    case_id: int,
    current_user: User = Depends(deps.get_current_active_user),
    tenant_id: int = Depends(deps.get_effective_tenant_id),
) -> Any:
    """Get case by ID (tenant-scoped)."""
    result = await db.execute(
        select(Case)
        .options(selectinload(Case.timeline_events).selectinload(TimelineEvent.user))
        .where(Case.id == case_id, Case.tenant_id == tenant_id)
    )
    case = result.scalars().first()
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    await _attach_sla(db, tenant_id, [case])
    return case

@router.put("/{case_id}", response_model=case_schema.Case)
async def update_case(
    *,
    db: AsyncSession = Depends(deps.get_db),
    case_id: int,
    case_in: case_schema.CaseUpdate,
    current_user: User = Depends(deps.get_current_active_user),
    tenant_id: int = Depends(deps.get_effective_tenant_id),
) -> Any:
    """Update a case (tenant-scoped)."""
    result = await db.execute(
        select(Case).where(Case.id == case_id, Case.tenant_id == tenant_id)
    )
    case = result.scalars().first()
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")

    changes = await apply_case_update(
        db, case=case, update_data=case_in.model_dump(exclude_unset=True), user_id=current_user.id,
    )

    await db.commit()
    if changes:
        emit_event(tenant_id, "case.updated", case_id=case.id, changes=changes)

    # Re-load with eager relationships
    result = await db.execute(
        select(Case)
        .options(selectinload(Case.timeline_events).selectinload(TimelineEvent.user))
        .where(Case.id == case.id)
    )
    case = result.scalars().first()
    await _attach_sla(db, tenant_id, [case])

    return case


# ── Timeline Events CRUD ──────────────────────────────────────────


@router.post("/{case_id}/timeline", response_model=case_schema.TimelineEvent)
async def create_timeline_event(
    *,
    db: AsyncSession = Depends(deps.get_db),
    case_id: int,
    event_in: case_schema.TimelineEventCreate,
    current_user: User = Depends(deps.get_current_active_user),
    tenant_id: int = Depends(deps.get_effective_tenant_id),
) -> Any:
    """Add a timeline event to a case."""
    result = await db.execute(
        select(Case).where(Case.id == case_id, Case.tenant_id == tenant_id)
    )
    case = result.scalars().first()
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")

    event = await add_timeline_note(
        db, case=case, user_id=current_user.id, **event_in.model_dump(),
    )

    event_id = event.id
    await db.commit()

    # Re-load with user to avoid lazy-load errors
    result = await db.execute(
        select(TimelineEvent)
        .options(selectinload(TimelineEvent.user))
        .where(TimelineEvent.id == event_id)
    )
    return result.scalars().first()


@router.put("/{case_id}/timeline/{event_id}", response_model=case_schema.TimelineEvent)
async def update_timeline_event(
    *,
    db: AsyncSession = Depends(deps.get_db),
    case_id: int,
    event_id: int,
    event_in: case_schema.TimelineEventUpdate,
    current_user: User = Depends(deps.get_current_active_user),
    tenant_id: int = Depends(deps.get_effective_tenant_id),
) -> Any:
    """Update a timeline event."""
    result = await db.execute(
        select(Case).where(Case.id == case_id, Case.tenant_id == tenant_id)
    )
    case = result.scalars().first()
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")

    result = await db.execute(
        select(TimelineEvent).where(
            TimelineEvent.id == event_id,
            TimelineEvent.case_id == case_id,
        )
    )
    event = result.scalars().first()
    if not event:
        raise HTTPException(status_code=404, detail="Timeline event not found")

    role = resolve_active_role(current_user.is_super_admin, getattr(current_user, "_active_tenant_id", None),
                               current_user.memberships)
    if not (role in ("admin", "super_admin") or (role == "analyst" and event.user_id == current_user.id)):
        raise HTTPException(status_code=403, detail="Only the author or an admin can edit this entry")

    await edit_timeline_note(db, case=case, event=event, update_data=event_in.model_dump(exclude_unset=True),
                             user_id=current_user.id)

    await db.commit()

    # Re-load with user
    result = await db.execute(
        select(TimelineEvent)
        .options(selectinload(TimelineEvent.user))
        .where(TimelineEvent.id == event_id)
    )
    return result.scalars().first()


@router.delete("/{case_id}/timeline/{event_id}")
async def delete_timeline_event(
    *,
    db: AsyncSession = Depends(deps.get_db),
    case_id: int,
    event_id: int,
    current_user: User = Depends(deps.get_current_active_user),
    tenant_id: int = Depends(deps.get_effective_tenant_id),
) -> Any:
    """Delete a timeline event."""
    result = await db.execute(
        select(Case).where(Case.id == case_id, Case.tenant_id == tenant_id)
    )
    if not result.scalars().first():
        raise HTTPException(status_code=404, detail="Case not found")

    result = await db.execute(
        select(TimelineEvent).where(
            TimelineEvent.id == event_id,
            TimelineEvent.case_id == case_id,
        )
    )
    event = result.scalars().first()
    if not event:
        raise HTTPException(status_code=404, detail="Timeline event not found")

    await db.delete(event)
    await db.commit()
    return {"ok": True}
