from datetime import datetime, timedelta, timezone
from typing import Any, List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload

from app.audit.context import audit_read
from app.api import deps
from app.models.case import Case, TimelineEvent
from app.models.slack_integration import SlackIntegration
from app.models.user import User
from app.services.slack_service import SlackError, load_integration
from app.schemas import case as case_schema
from app.services.case_service import (
    add_timeline_note, apply_case_update, check_case_update, create_case_record, delete_timeline_note, edit_timeline_note,
)
from app.notifications import service as notif_service
from app.utils.roles import resolve_active_role
from app.utils.audit import create_audit_log
from app.services.milestones import MilestoneConflict, case_milestones, set_milestones
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
    await _attach_war_room(db, tenant_id, cases)
    return cases


SILENT_AFTER = timedelta(minutes=10)


def war_room_url(team_id: Optional[str], channel_id: str) -> Optional[str]:
    return f"https://app.slack.com/client/{team_id}/{channel_id}" if team_id else None


async def _attach_war_room(db: AsyncSession, tenant_id: int, cases: List[Case]) -> None:
    """Set war_room_url / war_room_silent. Silent = listening, room older than 10 min, and no message
    event seen from Slack since the room was created."""
    rooms = [c for c in cases if c.slack_channel_id]
    if not rooms:
        return
    integ = (await db.execute(select(SlackIntegration).where(SlackIntegration.tenant_id == tenant_id))).scalars().first()
    now = datetime.now(timezone.utc)
    for c in rooms:
        c.war_room_url = war_room_url(integ.team_id if integ else None, c.slack_channel_id)
        made, last = c.slack_channel_created_at, integ.last_message_event_at if integ else None
        c.war_room_silent = bool(c.slack_listen and made and now - made > SILENT_AFTER and (last is None or last < made))


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
    current_user: User = Depends(deps.require_analyst_or_above),
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

async def _follow_case(db, case_id: int, tenant_id: int) -> Case:
    case = (await db.execute(select(Case).where(Case.id == case_id, Case.tenant_id == tenant_id))).scalars().first()
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    return case


@router.get("/{case_id}/follow")
async def get_follow(
    case_id: int,
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
    tenant_id: int = Depends(deps.get_effective_tenant_id),
) -> Any:
    case = await _follow_case(db, case_id, tenant_id)
    # The case owner is an implicit follower for notifications.
    following = case.owner_id == current_user.id or await notif_service.is_following(db, case.id, current_user.id)
    return {"following": bool(following)}


@router.put("/{case_id}/follow", status_code=204)
async def put_follow(
    case_id: int,
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
    tenant_id: int = Depends(deps.get_effective_tenant_id),
) -> Response:
    case = await _follow_case(db, case_id, tenant_id)
    await notif_service.follow(db, case, current_user.id)
    await db.commit()
    return Response(status_code=204)


@router.delete("/{case_id}/follow", status_code=204)
async def delete_follow(
    case_id: int,
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
    tenant_id: int = Depends(deps.get_effective_tenant_id),
) -> Response:
    case = await _follow_case(db, case_id, tenant_id)
    await notif_service.unfollow(db, case, current_user.id)  # owner stays implicitly followed
    await db.commit()
    return Response(status_code=204)


@router.get("/{case_id}", response_model=case_schema.Case, dependencies=[audit_read("case.view")])
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
    case.milestones = await case_milestones(db, case)
    return case

@router.put("/{case_id}/milestones")
async def update_milestones(
    *,
    db: AsyncSession = Depends(deps.get_db),
    case_id: int,
    body: case_schema.MilestonesIn,
    current_user: User = Depends(deps.require_analyst_or_above),
    tenant_id: int = Depends(deps.get_effective_tenant_id),
) -> Any:
    """Declare or clear incident milestones (shared by the glance and the report)."""
    case = (await db.execute(select(Case).where(Case.id == case_id, Case.tenant_id == tenant_id)
                             .with_for_update())).scalars().first()
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    try:
        ms = await set_milestones(db, case, body.model_dump(exclude_unset=True), current_user.id)
    except MilestoneConflict as e:
        await db.rollback()
        raise HTTPException(status_code=422, detail=str(e))
    await db.commit()
    return ms


@router.put("/{case_id}", response_model=case_schema.Case)
async def update_case(
    *,
    db: AsyncSession = Depends(deps.get_db),
    case_id: int,
    case_in: case_schema.CaseUpdate,
    current_user: User = Depends(deps.require_analyst_or_above),
    tenant_id: int = Depends(deps.get_effective_tenant_id),
) -> Any:
    """Update a case (tenant-scoped)."""
    result = await db.execute(
        select(Case).where(Case.id == case_id, Case.tenant_id == tenant_id)
    )
    case = result.scalars().first()
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")

    update_data = case_in.model_dump(exclude_unset=True)
    reason = (update_data.pop("reason", None) or "").strip() or None  # not a column
    # require_analyst_or_above already guarantees analyst+; resolve the exact role only where it matters.
    role = (resolve_active_role(current_user.is_super_admin, getattr(current_user, "_active_tenant_id", None),
                                current_user.memberships) if "guest_summary" in update_data else "analyst")
    await check_case_update(db, case=case, data=update_data, tenant_id=tenant_id, role=role)

    changes = await apply_case_update(
        db, case=case, update_data=update_data, user_id=current_user.id, reason=reason,
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


# ── Slack war room ───────────────────────────────────────────────

class WarRoomOpen(BaseModel):
    name: Optional[str] = None
    listen: bool = True


class WarRoomListen(BaseModel):
    listen: bool


async def _war_room_body(db, tenant_id: int, case: Case) -> dict:
    integ = (await db.execute(select(SlackIntegration).where(SlackIntegration.tenant_id == tenant_id))).scalars().first()
    return {"channel_id": case.slack_channel_id, "channel_name": case.slack_channel_name,
            "listen": bool(case.slack_listen),
            "url": war_room_url(integ.team_id if integ else None, case.slack_channel_id)}


async def _tenant_case(db, tenant_id: int, case_id: int) -> Case:
    case = (await db.execute(select(Case).where(Case.id == case_id, Case.tenant_id == tenant_id))).scalars().first()
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    return case


_WAR_ROOM_ERRORS = {
    "missing_scope": ("The Slack app is missing a scope needed for war rooms (groups:write to create and invite to "
                      "private channels). Add the scopes from the current app manifest and reinstall the app to "
                      "the workspace."),
    "not_in_channel": "The Slack bot is not in the channel. Invite the bot to the channel and try again.",
    "restricted_action": ("Your Slack workspace settings do not allow the bot to create channels or invite people. "
                          "Ask a workspace admin to allow it, then try again."),
}


@router.post("/{case_id}/war-room")
async def open_case_war_room(
    *,
    db: AsyncSession = Depends(deps.get_db),
    case_id: int,
    body: WarRoomOpen,
    current_user: User = Depends(deps.require_analyst_or_above),
    tenant_id: int = Depends(deps.get_effective_tenant_id),
) -> Any:
    """Open a private Slack channel for the case, optionally with SOC-bot listening."""
    from app.services.slack_bot import open_war_room
    case = await _tenant_case(db, tenant_id, case_id)
    if case.slack_channel_id:
        raise HTTPException(status_code=409, detail="This case already has a war room")
    try:
        await open_war_room(db, tenant_id, case, current_user, None, name=body.name, listen=body.listen)
    except SlackError as e:
        await db.rollback()
        detail = _WAR_ROOM_ERRORS.get(e.code or "") or str(e) or e.code or "Slack error"
        raise HTTPException(status_code=400, detail=detail)
    await create_audit_log(db, "case", case.id, "war_room_opened", tenant_id, user_id=current_user.id,
                           changes={"channel": case.slack_channel_name, "listen": bool(case.slack_listen)})
    await db.commit()
    return await _war_room_body(db, tenant_id, case)


@router.patch("/{case_id}/war-room")
async def set_war_room_listen(
    *,
    db: AsyncSession = Depends(deps.get_db),
    case_id: int,
    body: WarRoomListen,
    current_user: User = Depends(deps.require_analyst_or_above),
    tenant_id: int = Depends(deps.get_effective_tenant_id),
) -> Any:
    case = await _tenant_case(db, tenant_id, case_id)
    if not case.slack_channel_id:
        raise HTTPException(status_code=409, detail="This case has no war room")
    old = bool(case.slack_listen)
    if old != body.listen:
        case.slack_listen = body.listen
        await create_audit_log(db, "case", case.id, "war_room_listen_changed", tenant_id, user_id=current_user.id,
                               changes={"listen": {"old": old, "new": body.listen}})
        await db.commit()
    return await _war_room_body(db, tenant_id, case)


# ── Timeline Events CRUD ──────────────────────────────────────────


@router.post("/{case_id}/timeline", response_model=case_schema.TimelineEvent)
async def create_timeline_event(
    *,
    db: AsyncSession = Depends(deps.get_db),
    case_id: int,
    event_in: case_schema.TimelineEventCreate,
    current_user: User = Depends(deps.require_analyst_or_above),
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
    current_user: User = Depends(deps.require_analyst_or_above),
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


@router.patch("/{case_id}/timeline/{event_id}/pin", response_model=case_schema.TimelineEvent)
async def pin_timeline_event(
    *,
    db: AsyncSession = Depends(deps.get_db),
    case_id: int,
    event_id: int,
    body: case_schema.PinIn,
    current_user: User = Depends(deps.require_analyst_or_above),
    tenant_id: int = Depends(deps.get_effective_tenant_id),
) -> Any:
    """Pin or unpin an activity entry on the incident glance."""
    ev = (await db.execute(select(TimelineEvent).options(selectinload(TimelineEvent.user))
                           .join(Case, Case.id == TimelineEvent.case_id)
                           .where(TimelineEvent.id == event_id, TimelineEvent.case_id == case_id,
                                  Case.tenant_id == tenant_id))).scalars().first()
    if not ev:
        raise HTTPException(status_code=404, detail="Timeline event not found")
    if ev.pinned != body.pinned:
        ev.pinned = body.pinned
        await create_audit_log(db=db, entity_type="timeline_event", entity_id=ev.id,
                               action="timeline_pinned" if body.pinned else "timeline_unpinned",
                               tenant_id=tenant_id, user_id=current_user.id, changes={"case_id": case_id})
        await db.commit()
    return ev


@router.delete("/{case_id}/timeline/{event_id}")
async def delete_timeline_event(
    *,
    db: AsyncSession = Depends(deps.get_db),
    case_id: int,
    event_id: int,
    current_user: User = Depends(deps.require_analyst_or_above),
    tenant_id: int = Depends(deps.get_effective_tenant_id),
) -> Any:
    """Delete a timeline event."""
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
        raise HTTPException(status_code=403, detail="Only the author or an admin can delete this entry")

    await delete_timeline_note(db, case=case, event=event, user_id=current_user.id)
    await db.commit()
    return {"ok": True}
