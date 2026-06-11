from typing import Any, List, Dict, Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload

from app.api import deps
from app.services.ai_service import AIService, extract_action, strip_action_blocks
from app.models.case import Case, CaseStatus, CaseSeverity, TimelineEvent
from app.models.artifact import Artifact, ArtifactType
from app.models.case_artifact import CaseArtifact
from app.models.ioc import IOC
from app.models.user import User
from app.models.copilot_session import CopilotSession, CopilotMessage
from app.schemas.copilot import CopilotSessionSchema, CopilotChatRequest, CopilotMessageSchema
from app.schemas.copilot_action import (
    ActionExecuteRequest, ActionResult, RelatedCase, WRITE_ACTIONS, ACTION_TYPES,
)
from app.utils.audit import create_audit_log
from app.utils.copilot_heuristics import (
    detect_indicators, extract_note_text, is_note_request, is_declarative_finding,
    is_meta_note,
)
from app.utils.roles import resolve_active_role

router = APIRouter()

# Statuses that count as "open" work in the queue overview.
_CLOSED_STATUSES = {CaseStatus.RESOLVED, CaseStatus.CLOSED}

# If the first reply has no action block but the message looks like a command,
# we run a constrained-JSON second pass to recover the action.
_ACTION_KEYWORDS = (
    "create", "add ", "note", "comment", "set ", "update", "change", "mark",
    "resolve", "close", "block", "isolate", "tag", "apply", "correlate",
    "related case", "escalat", "remediat", "contain", "open a case", "make a case",
)


def _looks_actionable(message: str) -> bool:
    m = (message or "").lower()
    return any(k in m for k in _ACTION_KEYWORDS)


async def _case_indicator_values(db: AsyncSession, case_id: int, tenant_id: int) -> set:
    """Lowercased indicator values already attached to a case (artifacts + IOCs)."""
    arts = await db.execute(
        select(Artifact.value)
        .join(CaseArtifact, CaseArtifact.artifact_id == Artifact.id)
        .where(CaseArtifact.case_id == case_id, Artifact.tenant_id == tenant_id)
    )
    iocs = await db.execute(
        select(IOC.value).where(IOC.case_id == case_id, IOC.tenant_id == tenant_id)
    )
    return {v.lower() for v in [*arts.scalars().all(), *iocs.scalars().all()] if v}


async def _build_suggestions(
    db: AsyncSession, tenant_id: int, case_id: Optional[int],
    message: str, primary_action: Optional[Dict[str, Any]],
) -> Optional[List[Dict[str, Any]]]:
    """Proactive suggestions from the analyst's message (case mode only):
    - indicator values not yet on the case -> add_artifact suggestions
    - declarative findings with no other action -> add_timeline_note suggestion
    """
    if not case_id:
        return None
    suggestions: List[Dict[str, Any]] = []

    detected = detect_indicators(message)
    if detected:
        existing = await _case_indicator_values(db, case_id, tenant_id)
        primary_value = ""
        if primary_action and primary_action.get("type") == "add_artifact":
            primary_value = str(primary_action.get("params", {}).get("value", "")).lower()
        for d in detected:
            v = d["value"]
            if v.lower() in existing or v.lower() == primary_value:
                continue
            suggestions.append({
                "type": "add_artifact",
                "summary": f"I noticed `{v}` in the conversation — add it to the case as a {d['artifact_type']} artifact?",
                "params": {"value": v, "artifact_type": d["artifact_type"],
                           "description": "Mentioned in copilot conversation"},
            })
            if len(suggestions) >= 3:
                break

    if (
        len(suggestions) < 3
        and (primary_action is None or primary_action.get("type") != "add_timeline_note")
        and not is_note_request(message)
        and is_declarative_finding(message)
    ):
        suggestions.append({
            "type": "add_timeline_note",
            "summary": "Do you want to record this in the case timeline?",
            "params": {"content": message.strip()},
        })

    return suggestions[:3] or None


async def _build_general_context(db: AsyncSession, tenant_id: int) -> Dict[str, Any]:
    """Lightweight tenant-level queue overview for the general copilot session.

    Deliberately a current-state summary only — cross-case correlation/memory is
    a separate subsystem (C).
    """
    # Open cases grouped by severity
    rows = await db.execute(
        select(Case.severity, func.count())
        .where(Case.tenant_id == tenant_id, Case.status.notin_(_CLOSED_STATUSES))
        .group_by(Case.severity)
    )
    open_by_severity: Dict[str, int] = {}
    open_total = 0
    for severity, count in rows.all():
        sev = severity.value if severity else "unknown"
        open_by_severity[sev] = count
        open_total += count

    # Most recent cases (any status)
    recent_rows = await db.execute(
        select(Case)
        .where(Case.tenant_id == tenant_id)
        .order_by(Case.created_at.desc())
        .limit(10)
    )
    recent_cases = [
        {
            "id": c.id,
            "title": c.title,
            "severity": c.severity.value if c.severity else "unknown",
            "status": c.status.value if c.status else "unknown",
        }
        for c in recent_rows.scalars().all()
    ]

    ioc_total = (
        await db.execute(
            select(func.count()).select_from(IOC).where(IOC.tenant_id == tenant_id)
        )
    ).scalar() or 0

    return {
        "open_by_severity": open_by_severity,
        "open_total": open_total,
        "recent_cases": recent_cases,
        "ioc_total": ioc_total,
    }


class AnalysisRequest(BaseModel):
    case_id: int


async def _build_full_case_context(
    db: AsyncSession, case_id: int, tenant_id: int
) -> Dict[str, Any] | None:
    """Load case with all related data (timeline, artifacts) scoped to tenant."""
    result = await db.execute(
        select(Case)
        .options(selectinload(Case.timeline_events))
        .where(Case.id == case_id, Case.tenant_id == tenant_id)
    )
    case = result.scalars().first()
    if not case:
        return None

    # Load artifacts through junction table
    result = await db.execute(
        select(Artifact)
        .join(CaseArtifact, CaseArtifact.artifact_id == Artifact.id)
        .where(
            CaseArtifact.case_id == case_id,
            Artifact.tenant_id == tenant_id,
        )
    )
    artifacts = result.scalars().all()

    context: Dict[str, Any] = {
        "title": case.title,
        "description": case.description,
        "severity": case.severity.value if case.severity else "unknown",
        "status": case.status.value if case.status else "unknown",
        "tags": case.tags or [],
        "source": case.source or "unknown",
        "created_at": case.created_at.isoformat() if case.created_at else None,
    }

    # Timeline events
    if case.timeline_events:
        events = []
        for e in case.timeline_events:
            events.append({
                "type": e.event_type,
                "content": e.content,
                "created_at": e.created_at.isoformat() if e.created_at else None,
            })
        context["timeline_events"] = events

    # Artifacts / IOCs
    if artifacts:
        iocs = []
        for a in artifacts:
            iocs.append({
                "type": a.artifact_type.value if a.artifact_type else "other",
                "value": a.value,
                "description": a.description,
                "isolated": a.isolated,
            })
        context["artifacts"] = iocs

    return context


async def _create_session_with_briefing(
    db: AsyncSession, case_id: int, tenant_id: int, user_id: int
) -> CopilotSession:
    """Create a new copilot session with an AI-generated contextual briefing."""
    session = CopilotSession(
        case_id=case_id,
        tenant_id=tenant_id,
        user_id=user_id,
    )
    db.add(session)
    await db.flush()

    # Build case context and generate a contextual welcome
    context = await _build_full_case_context(db, case_id, tenant_id)

    if context:
        service = AIService()
        welcome_text = await service.generate_welcome_briefing(context)
    else:
        welcome_text = "Investigation session started. Ask me anything about this case."

    welcome = CopilotMessage(
        session_id=session.id,
        role="assistant",
        content=welcome_text,
    )
    db.add(welcome)
    await db.commit()

    # Re-load with messages
    result = await db.execute(
        select(CopilotSession)
        .options(selectinload(CopilotSession.messages))
        .where(CopilotSession.id == session.id)
    )
    return result.scalars().first()


async def _create_general_session_with_briefing(
    db: AsyncSession, tenant_id: int, user_id: int
) -> CopilotSession:
    """Create the user's general (case-less) session with a queue-overview briefing."""
    session = CopilotSession(case_id=None, tenant_id=tenant_id, user_id=user_id)
    db.add(session)
    await db.flush()

    context = await _build_general_context(db, tenant_id)
    service = AIService()
    welcome_text = await service.generate_general_welcome(context)

    db.add(CopilotMessage(session_id=session.id, role="assistant", content=welcome_text))
    await db.commit()

    result = await db.execute(
        select(CopilotSession)
        .options(selectinload(CopilotSession.messages))
        .where(CopilotSession.id == session.id)
    )
    return result.scalars().first()


@router.get("/sessions/general", response_model=CopilotSessionSchema)
async def get_or_create_general_session(
    *,
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
    tenant_id: int = Depends(deps.get_effective_tenant_id),
) -> Any:
    """Get the user's general (case-less) copilot session, or create one.

    Declared before /sessions/{case_id} so the literal path wins the match.
    """
    result = await db.execute(
        select(CopilotSession)
        .options(selectinload(CopilotSession.messages))
        .where(
            CopilotSession.case_id.is_(None),
            CopilotSession.user_id == current_user.id,
            CopilotSession.tenant_id == tenant_id,
        )
        .order_by(CopilotSession.created_at.desc())
    )
    session = result.scalars().first()
    if not session:
        session = await _create_general_session_with_briefing(db, tenant_id, current_user.id)
    return session


@router.get("/sessions/{case_id}", response_model=CopilotSessionSchema)
async def get_or_create_session(
    *,
    db: AsyncSession = Depends(deps.get_db),
    case_id: int,
    current_user: User = Depends(deps.get_current_active_user),
    tenant_id: int = Depends(deps.get_effective_tenant_id),
) -> Any:
    """Get the active copilot session for a case, or create one."""
    # Verify case belongs to tenant
    result = await db.execute(
        select(Case).where(Case.id == case_id, Case.tenant_id == tenant_id)
    )
    if not result.scalars().first():
        raise HTTPException(status_code=404, detail="Case not found")

    # Find existing session for this user + case
    result = await db.execute(
        select(CopilotSession)
        .options(selectinload(CopilotSession.messages))
        .where(
            CopilotSession.case_id == case_id,
            CopilotSession.user_id == current_user.id,
            CopilotSession.tenant_id == tenant_id,
        )
        .order_by(CopilotSession.created_at.desc())
    )
    session = result.scalars().first()

    if not session:
        session = await _create_session_with_briefing(db, case_id, tenant_id, current_user.id)

    return session


@router.post("/sessions/{case_id}/restart", response_model=CopilotSessionSchema)
async def restart_session(
    *,
    db: AsyncSession = Depends(deps.get_db),
    case_id: int,
    current_user: User = Depends(deps.get_current_active_user),
    tenant_id: int = Depends(deps.get_effective_tenant_id),
) -> Any:
    """Create a new copilot session for a case, replacing the old one."""
    # Verify case belongs to tenant
    result = await db.execute(
        select(Case).where(Case.id == case_id, Case.tenant_id == tenant_id)
    )
    if not result.scalars().first():
        raise HTTPException(status_code=404, detail="Case not found")

    # Delete existing sessions for this user + case
    result = await db.execute(
        select(CopilotSession).where(
            CopilotSession.case_id == case_id,
            CopilotSession.user_id == current_user.id,
            CopilotSession.tenant_id == tenant_id,
        )
    )
    old_sessions = result.scalars().all()
    for s in old_sessions:
        await db.delete(s)
    await db.flush()

    session = await _create_session_with_briefing(db, case_id, tenant_id, current_user.id)
    return session


@router.post("/chat", response_model=CopilotMessageSchema)
async def chat_copilot(
    *,
    db: AsyncSession = Depends(deps.get_db),
    chat_in: CopilotChatRequest,
    current_user: User = Depends(deps.get_current_active_user),
    tenant_id: int = Depends(deps.get_effective_tenant_id),
) -> Any:
    """Send a message to the copilot. Persists both user and assistant messages.

    With a `case_id` the copilot uses full single-case context; without one it
    runs in general mode against a tenant-level queue overview.
    """
    general = chat_in.case_id is None
    if general:
        context = await _build_general_context(db, tenant_id)
    else:
        context = await _build_full_case_context(db, chat_in.case_id, tenant_id)
        if not context:
            raise HTTPException(status_code=404, detail="Case not found")

    if not chat_in.session_id:
        raise HTTPException(status_code=400, detail="session_id is required")

    result = await db.execute(
        select(CopilotSession)
        .options(selectinload(CopilotSession.messages))
        .where(
            CopilotSession.id == chat_in.session_id,
            CopilotSession.tenant_id == tenant_id,
            # Scope to the caller — a session belongs to one user; without this a
            # tenant peer could post into another user's session (IDOR).
            CopilotSession.user_id == current_user.id,
        )
    )
    session = result.scalars().first()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # Save user message
    user_msg = CopilotMessage(
        session_id=session.id,
        role="user",
        content=chat_in.message,
    )
    db.add(user_msg)
    await db.flush()

    # Build message history for AI
    history = [{"role": m.role, "content": m.content} for m in session.messages]
    history.append({"role": "user", "content": chat_in.message})

    service = AIService()
    response_text = await service.chat(history, context, general=general)

    # ---- action resolution -------------------------------------------------
    # Note requests are handled deterministically: the local model is unreliable
    # at echoing the literal note text, so we parse it from the user's message;
    # for referential requests ("add the activity log") we compose the content
    # from the recent conversation instead.
    action = None
    note_req = is_note_request(chat_in.message)
    if note_req and not general:
        content = extract_note_text(chat_in.message)
        if not content or is_meta_note(content):
            content = await service.generate_note_content(history, chat_in.message)
        if content and not is_meta_note(content):
            action = {
                "type": "add_timeline_note",
                "summary": "Add this note to the case timeline",
                "params": {"content": content},
            }
    else:
        action = extract_action(response_text)
        if not action and _looks_actionable(chat_in.message):
            # Second pass: the local model often forgets the fenced block.
            action = await service.force_action_extraction(chat_in.message, context, general=general)

    # Validate any model-proposed note: prefer literal text from the user's
    # message; reject meta/placeholder content ("Added activity log") outright.
    if action and action.get("type") == "add_timeline_note":
        content = str(action.get("params", {}).get("content", "")).strip()
        better = extract_note_text(chat_in.message)
        if better and not is_meta_note(better):
            action["params"]["content"] = better
        elif not content or len(content) < 10 or is_meta_note(content):
            regenerated = None if general else await service.generate_note_content(history, chat_in.message)
            if regenerated and not is_meta_note(regenerated):
                action["params"]["content"] = regenerated
            else:
                action = None

    display_text = strip_action_blocks(response_text)
    if action and not display_text:
        display_text = action.get("summary") or "I've prepared an action below — confirm to run it."

    # ---- proactive suggestions ----------------------------------------------
    suggestions = await _build_suggestions(
        db, tenant_id, None if general else chat_in.case_id, chat_in.message, action
    )

    assistant_msg = CopilotMessage(
        session_id=session.id,
        role="assistant",
        content=display_text,
        action=action,
        suggestions=suggestions,
    )
    db.add(assistant_msg)
    await db.commit()
    await db.refresh(assistant_msg)

    return assistant_msg


@router.post("/analyze", response_model=Dict[str, str])
async def analyze_case(
    *,
    db: AsyncSession = Depends(deps.get_db),
    analysis_in: AnalysisRequest,
    current_user: User = Depends(deps.get_current_active_user),
    tenant_id: int = Depends(deps.get_effective_tenant_id),
) -> Any:
    """Generate an AI analysis for the case (tenant-scoped)."""
    case_data = await _build_full_case_context(db, analysis_in.case_id, tenant_id)
    if not case_data:
        raise HTTPException(status_code=404, detail="Case not found")

    service = AIService()
    analysis = await service.analyze_case(case_data)

    return {"analysis": analysis}


async def _get_case_in_tenant(db: AsyncSession, case_id: int, tenant_id: int) -> Case:
    result = await db.execute(select(Case).where(Case.id == case_id, Case.tenant_id == tenant_id))
    case = result.scalars().first()
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    return case


async def _find_related_cases(
    db: AsyncSession, tenant_id: int, case_id: Optional[int], value: Optional[str]
) -> List[RelatedCase]:
    """Correlate IOC/artifact values across other cases in the tenant."""
    if value:
        targets = {value.strip()}
    else:
        if not case_id:
            return []
        a = await db.execute(
            select(Artifact.value)
            .join(CaseArtifact, CaseArtifact.artifact_id == Artifact.id)
            .where(CaseArtifact.case_id == case_id, Artifact.tenant_id == tenant_id)
        )
        i = await db.execute(
            select(IOC.value).where(IOC.case_id == case_id, IOC.tenant_id == tenant_id)
        )
        targets = {v for v in [*a.scalars().all(), *i.scalars().all()] if v}
    if not targets:
        return []

    shared: Dict[int, Dict[str, Any]] = {}

    art = await db.execute(
        select(Case.id, Case.title, Artifact.value)
        .join(CaseArtifact, CaseArtifact.case_id == Case.id)
        .join(Artifact, Artifact.id == CaseArtifact.artifact_id)
        .where(Case.tenant_id == tenant_id, Artifact.value.in_(targets), Case.id != (case_id or -1))
    )
    iocs = await db.execute(
        select(Case.id, Case.title, IOC.value)
        .join(IOC, IOC.case_id == Case.id)
        .where(Case.tenant_id == tenant_id, IOC.value.in_(targets), Case.id != (case_id or -1))
    )
    for cid, title, val in [*art.all(), *iocs.all()]:
        entry = shared.setdefault(cid, {"title": title, "values": set()})
        entry["values"].add(val)

    return [
        RelatedCase(case_id=cid, title=e["title"], shared_values=sorted(e["values"]))
        for cid, e in shared.items()
    ]


@router.post("/actions/execute", response_model=ActionResult)
async def execute_action(
    *,
    db: AsyncSession = Depends(deps.get_db),
    req: ActionExecuteRequest,
    current_user: User = Depends(deps.get_current_active_user),
    tenant_id: int = Depends(deps.get_effective_tenant_id),
) -> Any:
    """Execute a copilot-proposed action. Writes require analyst-or-above and are
    audit-logged; everything is scoped to the active tenant."""
    if req.type not in ACTION_TYPES:
        raise HTTPException(status_code=400, detail="Unknown action type.")

    if req.type in WRITE_ACTIONS:
        role = resolve_active_role(
            current_user.is_super_admin,
            getattr(current_user, "_active_tenant_id", None),
            current_user.memberships,
        )
        if role not in ("super_admin", "admin", "analyst"):
            raise HTTPException(status_code=403, detail="You don't have permission to perform this action.")

    p = req.params or {}

    if req.type == "create_case":
        title = (p.get("title") or "").strip()
        if not title:
            raise HTTPException(status_code=400, detail="A case title is required.")
        try:
            severity = CaseSeverity(str(p.get("severity", "medium")).lower())
        except ValueError:
            severity = CaseSeverity.MEDIUM
        case = Case(
            title=title,
            description=p.get("description"),
            severity=severity,
            status=CaseStatus.NEW,
            tenant_id=tenant_id,
            owner_id=current_user.id,
        )
        db.add(case)
        await db.flush()
        await create_audit_log(db=db, entity_type="case", entity_id=case.id, action="create",
                               tenant_id=tenant_id, user_id=current_user.id)
        await db.commit()
        return ActionResult(ok=True, message=f"Created case #{case.id}: {title}", case_id=case.id)

    if req.type == "add_artifact":
        if not req.case_id:
            raise HTTPException(status_code=400, detail="Open a case to add an artifact.")
        await _get_case_in_tenant(db, req.case_id, tenant_id)
        value = (p.get("value") or "").strip()
        if not value:
            raise HTTPException(status_code=400, detail="An artifact value is required.")
        try:
            atype = ArtifactType(str(p.get("artifact_type", "other")).lower())
        except ValueError:
            atype = ArtifactType.OTHER
        existing = await db.execute(
            select(Artifact).where(
                Artifact.value == value, Artifact.artifact_type == atype,
                Artifact.tenant_id == tenant_id, Artifact.isolated == False,  # noqa: E712
            )
        )
        artifact = existing.scalars().first()
        if not artifact:
            artifact = Artifact(artifact_type=atype, value=value, description=p.get("description"),
                                isolated=False, tenant_id=tenant_id, created_by=current_user.id)
            db.add(artifact)
            await db.flush()
        link = await db.execute(
            select(CaseArtifact).where(
                CaseArtifact.case_id == req.case_id, CaseArtifact.artifact_id == artifact.id
            )
        )
        if not link.scalars().first():
            db.add(CaseArtifact(case_id=req.case_id, artifact_id=artifact.id, added_by=current_user.id))
            db.add(TimelineEvent(case_id=req.case_id, user_id=current_user.id, event_type="artifact_added",
                                 content=f"Added artifact: {value} ({atype.value})"))
        await create_audit_log(db=db, entity_type="artifact", entity_id=artifact.id, action="create",
                               tenant_id=tenant_id, user_id=current_user.id)
        await db.commit()
        return ActionResult(ok=True, message=f"Added artifact `{value}` ({atype.value}) to case #{req.case_id}.",
                            case_id=req.case_id)

    if req.type == "add_timeline_note":
        if not req.case_id:
            raise HTTPException(status_code=400, detail="Open a case to add a note.")
        await _get_case_in_tenant(db, req.case_id, tenant_id)
        content = (p.get("content") or "").strip()
        if not content:
            raise HTTPException(status_code=400, detail="Note content is required.")
        db.add(TimelineEvent(case_id=req.case_id, user_id=current_user.id, event_type="comment", content=content))
        await create_audit_log(db=db, entity_type="case", entity_id=req.case_id, action="comment",
                               tenant_id=tenant_id, user_id=current_user.id)
        await db.commit()
        return ActionResult(ok=True, message=f"Added a note to case #{req.case_id}.", case_id=req.case_id)

    if req.type == "update_case":
        if not req.case_id:
            raise HTTPException(status_code=400, detail="Open a case to update it.")
        case = await _get_case_in_tenant(db, req.case_id, tenant_id)
        changes: Dict[str, Any] = {}
        if p.get("status"):
            try:
                case.status = CaseStatus(str(p["status"]).lower())
                changes["status"] = case.status.value
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid status.")
        if p.get("severity"):
            try:
                case.severity = CaseSeverity(str(p["severity"]).lower())
                changes["severity"] = case.severity.value
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid severity.")
        if not changes:
            raise HTTPException(status_code=400, detail="Nothing to update.")
        await create_audit_log(db=db, entity_type="case", entity_id=case.id, action="update",
                               tenant_id=tenant_id, user_id=current_user.id, changes=changes)
        await db.commit()
        return ActionResult(ok=True, message=f"Updated case #{case.id} ({', '.join(changes)}).", case_id=case.id)

    # find_related (read-only)
    related = await _find_related_cases(db, tenant_id, req.case_id, (p.get("value") or "").strip() or None)
    msg = (f"Found {len(related)} related case(s)." if related
           else "No other cases share these indicators.")
    return ActionResult(ok=True, message=msg, related=related)
