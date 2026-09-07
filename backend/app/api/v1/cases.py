from typing import Any, List
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload

from app.api import deps
from app.models.case import Case, CaseStatus, TimelineEvent
from app.models.case_triage import CaseTriageResult
from app.models.user import User
from app.schemas import case as case_schema
from app.utils.audit import create_audit_log
from app.utils.sla import compute_sla_state, load_policy_overrides
from app.tasks.triage import run_case_triage_task

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
    skip: int = 0,
    limit: int = 100,
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
    case = Case(**case_in.model_dump(), tenant_id=tenant_id)
    if not case.owner_id:
        case.owner_id = current_user.id

    db.add(case)
    await db.flush()

    await create_audit_log(
        db=db,
        entity_type="case",
        entity_id=case.id,
        action="create",
        tenant_id=tenant_id,
        user_id=current_user.id,
    )
    await db.commit()

    # Kick off AI triage in the background; never blocks case creation.
    triage = CaseTriageResult(
        case_id=case.id, tenant_id=tenant_id, triggered_by="auto_on_create", status="pending",
    )
    db.add(triage)
    await db.commit()
    await db.refresh(triage)
    run_case_triage_task.delay(triage.id)

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

    old_status = case.status
    update_data = case_in.model_dump(exclude_unset=True)
    changes = {}
    for field, value in update_data.items():
        old_value = getattr(case, field)
        if old_value != value:
            changes[field] = {"from": str(old_value) if old_value is not None else None, "to": str(value) if value is not None else None}
        setattr(case, field, value)

    # Auto-generate timeline events for status/severity changes
    if "status" in changes:
        db.add(TimelineEvent(
            case_id=case.id,
            user_id=current_user.id,
            event_type="status_change",
            content=f"Status changed from {changes['status']['from']} to {changes['status']['to']}",
        ))
        # Set/clear resolved_at
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
        db.add(TimelineEvent(
            case_id=case.id,
            user_id=current_user.id,
            event_type="severity_change",
            content=f"Severity changed from {changes['severity']['from']} to {changes['severity']['to']}",
        ))

    if changes:
        await create_audit_log(
            db=db,
            entity_type="case",
            entity_id=case.id,
            action="update",
            tenant_id=tenant_id,
            user_id=current_user.id,
            changes=changes,
        )

    await db.commit()

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
    if not result.scalars().first():
        raise HTTPException(status_code=404, detail="Case not found")

    event = TimelineEvent(
        case_id=case_id,
        user_id=current_user.id,
        **event_in.model_dump(),
    )
    db.add(event)
    await db.flush()

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

    update_data = event_in.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(event, field, value)

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
