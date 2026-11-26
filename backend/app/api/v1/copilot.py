import asyncio
import json
import re
from contextlib import aclosing
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, AsyncIterator, List, Dict, Optional
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ValidationError
from sqlalchemy import delete, func, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload

from app.api import deps
from app.services import case_service, task_service
from app.ai.errors import AIError, AIUnavailable
from app.db.session import AsyncSessionLocal
from app.services.ai_service import (
    ActionFenceFilter, AIService, _unavailable_text, extract_action, strip_action_blocks,
)
from app.services.artifact_types import not_private
from app.models.case import CASE_OUTCOMES, Case, CaseStatus, CaseSeverity, TimelineEvent
from app.models.artifact import Artifact, ArtifactType
from app.models.case_artifact import CaseArtifact
from app.models.ioc import IOC
from app.models.user import User
from app.models.copilot_session import CopilotSession, CopilotMessage
from app.schemas.copilot import CopilotSessionSchema, CopilotChatRequest, CopilotMessageSchema
from app.schemas.copilot_action import (
    ActionDismissRequest, ActionExecuteRequest, ActionProposal, ActionResult, RelatedCase, WRITE_ACTIONS,
    ACTION_TYPES, live_state,
)
from app.utils.audit import create_audit_log
from app.utils.copilot_heuristics import (
    detect_indicators, extract_note_text, is_note_request, is_declarative_finding,
    is_meta_note, refang,
)
from app.utils.roles import resolve_active_role

router = APIRouter()
MAX_CASE_ARTIFACTS = 10  # indicators auto-attached when the copilot creates a case

# Statuses that count as "open" work in the queue overview.
_CLOSED_STATUSES = {CaseStatus.RESOLVED, CaseStatus.CLOSED}

# If the first reply has no action block but the message looks like a command,
# we run a constrained-JSON second pass to recover the action.
# Whole words only: "stage", "outage", "markdown" or "why was this blocked?" are not commands.
_ACTION_KEYWORDS = re.compile(
    r"\b(?:create|add|note|comment|set|update|change|mark|resolve|close|block|isolate|tag|apply|correlate"
    r"|related cases?|escalate|remediate|contain|open a case|make a case)\b",
    re.IGNORECASE,
)
# Actions that write to (or read from) one case, so their proposal carries the target case.
_CASE_ACTIONS = {"add_artifact", "add_timeline_note", "update_case", "apply_playbook", "find_related"}
# "case #12", "case 12" or "#12" in the analyst's message; not the "10" of "case 10.0.0.1".
_CASE_REF = re.compile(r"(?:\bcase\s*#?\s*|#)(\d{1,9})(?![.\d])", re.IGNORECASE)
_SESSION_LOCK_NS = 0x436F5069  # advisory-lock namespace for copilot session get-or-create


def _looks_actionable(message: str) -> bool:
    return bool(_ACTION_KEYWORDS.search(message or ""))


async def _case_indicator_values(db: AsyncSession, case_id: int, tenant_id: int) -> set:
    """Lowercased indicator values already attached to a case (artifacts + IOCs)."""
    arts = await db.execute(
        select(Artifact.value)
        .join(CaseArtifact, CaseArtifact.artifact_id == Artifact.id)
        .where(CaseArtifact.case_id == case_id, Artifact.tenant_id == tenant_id, not_private())
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

    detected = detect_indicators(refang(message))  # "hxxp://evil[.]com" is suggested (and stored) as the real value
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
        and primary_action is None  # a command that already produced an action is not a finding
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
    result = await db.execute(select(Case).where(Case.id == case_id, Case.tenant_id == tenant_id))
    case = result.scalars().first()
    if not case:
        return None

    # Oldest first, in a stable order: the prompt keeps the most recent entries (and pinned ones).
    timeline = (await db.execute(
        select(TimelineEvent).where(TimelineEvent.case_id == case_id)
        .order_by(TimelineEvent.created_at, TimelineEvent.id)
    )).scalars().all()

    # Load artifacts through junction table, most recently attached first
    result = await db.execute(
        select(Artifact)
        .join(CaseArtifact, CaseArtifact.artifact_id == Artifact.id)
        .where(
            CaseArtifact.case_id == case_id,
            Artifact.tenant_id == tenant_id,
            not_private(),
        )
        .order_by(CaseArtifact.id.desc())
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
    if timeline:
        events = []
        for e in timeline:
            events.append({
                "type": e.event_type,
                "content": e.content,
                "created_at": e.created_at.isoformat() if e.created_at else None,
                "pinned": bool(e.pinned),
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


async def _existing_session(
    db: AsyncSession, tenant_id: int, user_id: int, case_id: Optional[int]
) -> Optional[CopilotSession]:
    """The user's newest session for a case (or the general one when `case_id` is None), with its messages."""
    result = await db.execute(
        select(CopilotSession)
        .options(selectinload(CopilotSession.messages))
        .where(
            CopilotSession.case_id == case_id if case_id is not None else CopilotSession.case_id.is_(None),
            CopilotSession.user_id == user_id,
            CopilotSession.tenant_id == tenant_id,
        )
        .order_by(CopilotSession.created_at.desc())
    )
    return result.scalars().first()


async def _store_session(
    db: AsyncSession, tenant_id: int, user_id: int, case_id: Optional[int], welcome_text: str
) -> CopilotSession:
    """Insert the session with its briefing — unless another request (a second tab) stored one while this one
    was waiting on the model. There is no unique constraint to lean on, so the re-check and the insert run
    under a per-user advisory lock held until commit."""
    await db.execute(text("SELECT pg_advisory_xact_lock(:ns, :uid)"), {"ns": _SESSION_LOCK_NS, "uid": user_id})
    session = await _existing_session(db, tenant_id, user_id, case_id)
    if session is None:
        session = CopilotSession(case_id=case_id, tenant_id=tenant_id, user_id=user_id)
        db.add(session)
        await db.flush()
        db.add(CopilotMessage(session_id=session.id, role="assistant", content=welcome_text))
    await db.commit()
    return await _existing_session(db, tenant_id, user_id, case_id)


async def _create_session_with_briefing(
    db: AsyncSession, case_id: int, tenant_id: int, user_id: int
) -> CopilotSession:
    """Create a new copilot session with an AI-generated contextual briefing."""
    # Build case context and generate a contextual welcome
    context = await _build_full_case_context(db, case_id, tenant_id)

    if context:
        # Commit (a restart's session drop) and release the DB connection before the slow LLM call, so
        # concurrent copilot requests can't drain the pool while they wait on the provider.
        await db.commit()
        service = AIService(tenant_id=tenant_id)
        welcome_text = await service.generate_welcome_briefing(context)
    else:
        welcome_text = "Investigation session started. Ask me anything about this case."

    return await _store_session(db, tenant_id, user_id, case_id, welcome_text)


async def _create_general_session_with_briefing(
    db: AsyncSession, tenant_id: int, user_id: int
) -> CopilotSession:
    """Create the user's general (case-less) session with a queue-overview briefing."""
    context = await _build_general_context(db, tenant_id)
    await db.commit()  # release the DB connection before the slow LLM call (see above)
    service = AIService(tenant_id=tenant_id)
    welcome_text = await service.generate_general_welcome(context)
    return await _store_session(db, tenant_id, user_id, None, welcome_text)


async def _with_targets(db: AsyncSession, tenant_id: int, messages: List[CopilotMessageSchema]) -> None:
    """Fill in each proposal's target case as it is NOW (title, or missing when it was deleted since), so the
    card can name the case before Confirm instead of failing after it."""
    proposals = [p for m in messages for p in (m.action, *(m.suggestions or [])) if p and p.case_id]
    if not proposals:
        return
    rows = await db.execute(select(Case.id, Case.title).where(
        Case.id.in_({p.case_id for p in proposals}), Case.tenant_id == tenant_id))
    titles = {cid: title for cid, title in rows.all()}
    for p in proposals:
        p.case_title, p.case_missing = titles.get(p.case_id), p.case_id not in titles


async def _session_out(db: AsyncSession, tenant_id: int, session: CopilotSession) -> CopilotSessionSchema:
    out = CopilotSessionSchema.model_validate(session)
    await _with_targets(db, tenant_id, out.messages)
    return out


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
    session = await _existing_session(db, tenant_id, current_user.id, None)
    if not session:
        session = await _create_general_session_with_briefing(db, tenant_id, current_user.id)
    return await _session_out(db, tenant_id, session)


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

    session = await _existing_session(db, tenant_id, current_user.id, case_id)
    if not session:
        session = await _create_session_with_briefing(db, case_id, tenant_id, current_user.id)
    return await _session_out(db, tenant_id, session)


async def _drop_sessions(db: AsyncSession, tenant_id: int, user_id: int, case_id: Optional[int]) -> None:
    """Delete the user's sessions for a case (or the general one).

    Bulk delete so the DB's ON DELETE CASCADE removes the messages; an ORM delete would try to
    null their session_id (the relationship has no cascade) and fail the NOT NULL constraint.
    """
    await db.execute(delete(CopilotSession).where(
        CopilotSession.case_id == case_id if case_id is not None else CopilotSession.case_id.is_(None),
        CopilotSession.user_id == user_id,
        CopilotSession.tenant_id == tenant_id,
    ))


@router.post("/sessions/general/restart", response_model=CopilotSessionSchema)
async def restart_general_session(
    *,
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
    tenant_id: int = Depends(deps.get_effective_tenant_id),
) -> Any:
    """Start a fresh general (case-less) session, replacing the old one."""
    await _drop_sessions(db, tenant_id, current_user.id, None)
    return await _create_general_session_with_briefing(db, tenant_id, current_user.id)


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

    await _drop_sessions(db, tenant_id, current_user.id, case_id)

    session = await _create_session_with_briefing(db, case_id, tenant_id, current_user.id)
    return session


@dataclass
class _Turn:
    """One user message being answered: what the reply's post-processing needs, as plain values so it can run
    on another DB session than the request's (the streaming endpoint outlives the request's session)."""
    session_id: int
    case_id: Optional[int]
    message: str
    user_msg_id: int
    context: Dict[str, Any]
    history: List[Dict[str, str]]

    @property
    def general(self) -> bool:
        return self.case_id is None


async def _open_turn(db: AsyncSession, chat_in: CopilotChatRequest, user_id: int, tenant_id: int) -> _Turn:
    """Checks, context and the stored user turn — everything before the model is called."""
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
            CopilotSession.user_id == user_id,
        )
    )
    session = result.scalars().first()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # The session decides the mode and the case. A request naming another case is a stale or mismatched pair:
    # answering it would file one case's conversation (and its actions) under the other.
    case_id = session.case_id
    if chat_in.case_id is not None and chat_in.case_id != case_id:
        raise HTTPException(status_code=400, detail="This session belongs to a different conversation.")
    if case_id is None:
        context = await _build_general_context(db, tenant_id)
    else:
        context = await _build_full_case_context(db, case_id, tenant_id)
        if not context:
            raise HTTPException(status_code=404, detail="Case not found")

    # Save user message
    user_msg = CopilotMessage(
        session_id=session.id,
        role="user",
        content=chat_in.message,
    )
    db.add(user_msg)
    # Build message history for AI (before the commit: the user message isn't in session.messages yet)
    history = [{"role": m.role, "content": m.content} for m in session.messages]
    history.append({"role": "user", "content": chat_in.message})
    # Commit the user message and release the DB connection before the LLM calls (each up to
    # AI_TIMEOUT_SECONDS): holding it would let a few slow chats drain the pool. The next query reopens one.
    await db.commit()
    return _Turn(session.id, case_id, chat_in.message, user_msg.id, context, history)


async def _drop_turn(db: AsyncSession, turn: _Turn) -> None:
    """A failed (or abandoned) exchange leaves nothing behind: stored as an assistant turn, the "unavailable"
    text was replayed to the model as its own words on every later message. The client keeps the typed text
    for a retry, so the user turn goes too (no duplicate on resend)."""
    await db.execute(delete(CopilotMessage).where(CopilotMessage.id == turn.user_msg_id))
    await db.commit()


def _failure(e: AIError) -> tuple:
    """(HTTP status, stream error code, user-facing detail) for a provider failure."""
    if getattr(e, "cause", None) == "Timeout":
        return 504, "timeout", _unavailable_text(e)
    return (503, "unavailable", _unavailable_text(e)) if isinstance(e, AIUnavailable) else \
        (502, "provider", _unavailable_text(e))


async def _close_turn(db: AsyncSession, tenant_id: int, turn: _Turn, service: AIService,
                      response_text: str) -> CopilotMessageSchema:
    """Turn the model's first-pass reply into the stored assistant message: action resolution, validation,
    target case, suggestions. The one place this happens, for the plain and the streaming endpoint alike."""
    general, case_id, message, history = turn.general, turn.case_id, turn.message, turn.history
    # ---- action resolution -------------------------------------------------
    # Note requests are handled deterministically: the local model is unreliable
    # at echoing the literal note text, so we parse it from the user's message;
    # for referential requests ("add the activity log") we compose the content
    # from the recent conversation instead.
    action = None
    note_req = is_note_request(message)
    if note_req and not general:
        content = extract_note_text(message)
        if not content or is_meta_note(content):
            content = await service.generate_note_content(history, message)
        if content and not is_meta_note(content):
            action = {
                "type": "add_timeline_note",
                "summary": "Add this note to the case timeline",
                "params": {"content": content},
            }
    else:
        action = extract_action(response_text)
        if not action and _looks_actionable(message):
            # Second pass: the local model often forgets the fenced block. It gets the turns just before
            # this message too, so "yes, add it" has something to refer to.
            action = await service.force_action_extraction(message, turn.context, general=general,
                                                           recent=history[-4:-1])

    # Validate any model-proposed note: prefer literal text from the user's
    # message; reject meta/placeholder content ("Added activity log") outright.
    if action and action.get("type") == "add_timeline_note":
        content = str(action["params"].get("content", "")).strip()
        better = extract_note_text(message)
        if better and not is_meta_note(better):
            action["params"]["content"] = better
        elif not content or len(content) < 10 or is_meta_note(content):
            regenerated = None if general else await service.generate_note_content(history, message)
            if regenerated and not is_meta_note(regenerated):
                action["params"]["content"] = regenerated
            else:
                action = None

    if action and action.get("type") == "create_case":
        # This message first, then the few user turns before it: the indicator just named is never the one
        # cut by the cap, and a long session's early indicators don't leak into an unrelated case.
        found: List[Dict[str, str]] = []
        for said in [m["content"] for m in reversed(history) if m["role"] == "user"][:4]:
            found += [d for d in detect_indicators(refang(said), limit=MAX_CASE_ARTIFACTS)
                      if d["value"].lower() not in {f["value"].lower() for f in found}]
        if found:
            action["params"]["artifacts"] = found[:MAX_CASE_ARTIFACTS]

    if action and action.get("type") in _CASE_ACTIONS:
        target = await _target_case(db, tenant_id, turn.session_id, case_id, message)
        if target is not None:
            action["case_id"] = target

    if action:
        # Model output is stored (and later executed) only in the proposal's shape.
        try:
            action = ActionProposal(**action).model_dump(
                exclude_none=True, exclude={"case_title", "case_missing", "edited"})
        except ValidationError:
            action = None

    display_text = strip_action_blocks(response_text)
    if action and not display_text:
        display_text = action.get("summary") or "I've prepared an action below — confirm to run it."

    # ---- proactive suggestions ----------------------------------------------
    suggestions = await _build_suggestions(db, tenant_id, case_id, message, action)

    assistant_msg = CopilotMessage(
        session_id=turn.session_id,
        role="assistant",
        content=display_text,
        action=action,
        suggestions=suggestions,
    )
    db.add(assistant_msg)
    await db.commit()
    await db.refresh(assistant_msg)

    out = CopilotMessageSchema.model_validate(assistant_msg)
    await _with_targets(db, tenant_id, [out])
    return out


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
    turn = await _open_turn(db, chat_in, current_user.id, tenant_id)
    service = AIService(tenant_id=tenant_id)
    try:
        response_text = await service.chat(turn.history, turn.context, general=turn.general, raise_errors=True)
    except AIError as e:
        await _drop_turn(db, turn)
        status, _code, detail = _failure(e)
        raise HTTPException(status_code=status, detail=detail)
    return await _close_turn(db, tenant_id, turn, service, response_text)


def _sse(event: str, data: Any) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


# Clean-up tasks for abandoned streams, held here so they aren't garbage-collected before they finish.
_ABANDONED: set = set()


async def _abandon_turn(turn: _Turn) -> None:
    async with AsyncSessionLocal() as db:
        await _drop_turn(db, turn)


async def _chat_events(turn: _Turn, tenant_id: int) -> AsyncIterator[str]:
    """The streamed reply as Server-Sent Events: `delta` {text} while the model writes, then exactly one of
    `message` (the stored assistant message, same JSON as POST /chat) or `error` {code, detail}.

    Runs after the request's DB session is gone, so it opens its own. If the client goes away (Stop button,
    closed tab) the generator is cancelled or closed: generation stops and the user turn is removed, as on
    a provider failure."""
    service = AIService(tenant_id=tenant_id)
    shown = ActionFenceFilter()  # the client never sees a raw action block
    settled = False
    try:
        raw = ""
        try:
            async with aclosing(service.chat_stream(turn.history, turn.context, general=turn.general)) as chunks:
                async for chunk in chunks:
                    raw += chunk
                    text = shown.feed(chunk)
                    if text:
                        yield _sse("delta", {"text": text})
        except AIError as e:
            async with AsyncSessionLocal() as db:
                await _drop_turn(db, turn)
            settled = True
            _status, code, detail = _failure(e)
            yield _sse("error", {"code": code, "detail": detail})
            return
        text = shown.flush()
        if text:
            yield _sse("delta", {"text": text})
        async with AsyncSessionLocal() as db:
            out = await _close_turn(db, tenant_id, turn, service, raw or "No response from AI")
        settled = True
        yield _sse("message", out.model_dump(mode="json"))
    finally:
        if not settled:
            # Cancelled mid-await: nothing more can be awaited here, so the clean-up runs as its own task.
            task = asyncio.get_running_loop().create_task(_abandon_turn(turn))
            _ABANDONED.add(task)
            task.add_done_callback(_ABANDONED.discard)


@router.post("/chat/stream")
async def chat_copilot_stream(
    *,
    db: AsyncSession = Depends(deps.get_db),
    chat_in: CopilotChatRequest,
    current_user: User = Depends(deps.get_current_active_user),
    tenant_id: int = Depends(deps.get_effective_tenant_id),
) -> StreamingResponse:
    """POST /chat with the reply streamed as Server-Sent Events (see _chat_events). Everything refused before
    the model is called (auth, session, case) is a plain HTTP error, exactly as on POST /chat."""
    turn = await _open_turn(db, chat_in, current_user.id, tenant_id)
    # X-Accel-Buffering: nginx would otherwise hold the events back until its buffer fills.
    return StreamingResponse(_chat_events(turn, tenant_id), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


async def _target_case(db: AsyncSession, tenant_id: int, session_id: int, session_case_id: Optional[int],
                       message: str) -> Optional[int]:
    """The case a proposal will act on, fixed when it is proposed so the card shows it before Confirm: the
    session's case; in the general session a case the analyst named ("case #12"), otherwise the case most
    recently created from this conversation. None means the proposal needs a case — never a guess."""
    if session_case_id is not None:
        return session_case_id
    named = _CASE_REF.search(message or "")
    case_id = int(named.group(1)) if named else await _conversation_case(db, session_id)
    if case_id is None:
        return None
    return (await db.execute(select(Case.id).where(Case.id == case_id, Case.tenant_id == tenant_id))).scalar()


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

    await db.commit()  # release the DB connection before the slow LLM call
    service = AIService(tenant_id=tenant_id)
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
            .where(CaseArtifact.case_id == case_id, Artifact.tenant_id == tenant_id, not_private())
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
        .where(Case.tenant_id == tenant_id, Artifact.value.in_(targets), Case.id != (case_id or -1), not_private())
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


async def _lock_message(db: AsyncSession, message_id: int, user_id: int, tenant_id: int) -> CopilotMessage:
    """The caller's own message, row-locked until the next commit so two requests can't both act on its
    proposal. Someone else's (or another tenant's) message is a 404."""
    msg = (await db.execute(
        select(CopilotMessage).join(CopilotSession).options(selectinload(CopilotMessage.session))
        .where(CopilotMessage.id == message_id, CopilotSession.user_id == user_id,
               CopilotSession.tenant_id == tenant_id)
        .with_for_update(of=CopilotMessage).execution_options(populate_existing=True)
    )).scalars().first()
    if msg is None:
        raise HTTPException(status_code=404, detail="Message not found")
    return msg


def _proposal(msg: CopilotMessage, index: Optional[int]) -> Dict[str, Any]:
    """The stored proposal (main action, or suggestion `index`); 404 when there is none or it is malformed."""
    items = msg.suggestions if isinstance(msg.suggestions, list) else []
    stored = msg.action if index is None else items[index] if 0 <= index < len(items) else None
    if not isinstance(stored, dict) or not isinstance(stored.get("params") or {}, dict):
        raise HTTPException(status_code=404, detail="Action not found")
    return stored


def _set_proposal(msg: CopilotMessage, index: Optional[int], proposal: Dict[str, Any]) -> None:
    # Always a new object: the JSON column only notices reassignment, not in-place edits.
    if index is None:
        msg.action = proposal
    else:
        msg.suggestions = [proposal if i == index else s for i, s in enumerate(msg.suggestions)]


# Per action type: the values the analyst may change on the card before Confirm, each with the check a
# model-proposed value gets. Not here = not editable (action type, target case, status, attached indicators).
def _text(limit: int, required: bool = True):
    def check(v: Any) -> str:
        if not isinstance(v, str) or len(v) > limit or (required and not v.strip()):
            raise ValueError(f"must be {'' if required else 'empty or '}text of at most {limit} characters")
        return v.strip()
    return check


def _one_of(enum):
    def check(v: Any) -> str:
        return enum(v.lower() if isinstance(v, str) else v).value  # ValueError when not a member
    return check


def _tag_list(v: Any) -> List[str]:
    if not isinstance(v, list) or len(v) > 50 or not all(isinstance(t, str) and len(t) <= 100 for t in v):
        raise ValueError("must be a list of at most 50 short text values")
    return [t.strip() for t in v if t.strip()]


_EDITABLE = {
    "add_timeline_note": {"content": _text(16_000)},
    "add_artifact": {"value": _text(2048), "artifact_type": _one_of(ArtifactType),
                     "description": _text(2000, required=False)},
    "create_case": {"title": _text(500), "severity": _one_of(CaseSeverity),
                    "description": _text(10_000, required=False)},
    "update_case": {"tags": _tag_list},
}


def _apply_edits(action_type: str, params: Dict[str, Any], edits: Dict[str, Any]) -> Dict[str, Any]:
    """The stored params with the analyst's edits over them. 422 for a key that isn't editable on this action
    or a value that doesn't validate — before anything is claimed, so the proposal stays confirmable."""
    allowed = _EDITABLE.get(action_type, {})
    out = dict(params)
    for key, value in edits.items():
        if key not in allowed:
            raise HTTPException(status_code=422, detail=f"'{key}' can't be edited on this action.")
        try:
            out[key] = allowed[key](value)
        except (ValueError, TypeError) as e:
            raise HTTPException(status_code=422, detail=f"{key}: {e}")
    return out


async def _settle(db: AsyncSession, req: ActionExecuteRequest, user_id: int, tenant_id: int,
                  result: Optional[ActionResult]) -> None:
    """End a run: store its result on the proposal (with the params that were actually written, when the
    analyst edited them), or (result None: it failed) hand the proposal back as it was proposed."""
    try:
        msg = await _lock_message(db, req.message_id, user_id, tenant_id)
    except HTTPException:
        return  # the conversation was cleared while the action ran: nothing left to record it on
    stored = {k: v for k, v in _proposal(msg, req.suggestion_index).items() if k not in ("state", "claimed_at")}
    if result is not None:
        stored["result"] = result.model_dump(exclude_none=True)
        if req.edits:
            stored["params"], stored["edited"] = req.params, True
    _set_proposal(msg, req.suggestion_index, stored)
    await db.commit()


@router.post("/actions/execute", response_model=ActionResult)
async def execute_action(
    *,
    db: AsyncSession = Depends(deps.get_db),
    req: ActionExecuteRequest,
    current_user: User = Depends(deps.get_current_active_user),
    tenant_id: int = Depends(deps.get_effective_tenant_id),
) -> Any:
    """Execute a copilot-proposed action. Writes require analyst-or-above and are
    audit-logged; everything is scoped to the active tenant.

    With `message_id` the proposal (type, params, target case) is taken from that (own) message rather than
    the request, and its outcome is stored there. A write runs once: a proposal that was already run,
    cancelled or dismissed is refused with 409. A read returns its stored result instead of running again.
    """
    claimed = False
    user_id = current_user.id  # read now: a rollback below expires the loaded user
    if req.message_id is not None:
        msg = await _lock_message(db, req.message_id, user_id, tenant_id)
        stored = _proposal(msg, req.suggestion_index)
        is_write = stored.get("type") in WRITE_ACTIONS
        if stored.get("result") and not is_write:
            return ActionResult(**stored["result"])
        state = live_state(stored)
        if stored.get("result") or state:
            raise HTTPException(status_code=409, detail=f"This action was already {state or 'run'}.")
        req.type, req.params = stored.get("type", req.type), stored.get("params") or {}
        if req.edits:
            req.params = _apply_edits(req.type, req.params, req.edits)
        # The target was resolved when the proposal was made and shown on the card; it is not re-guessed here.
        req.case_id = stored.get("case_id") or msg.session.case_id
        if is_write:
            # Claim it under the row lock, so a second Confirm (double click, second tab) is refused above.
            # The claim is timestamped: one left behind by a worker that died mid-run expires (see live_state).
            _set_proposal(msg, req.suggestion_index, {
                **stored, "state": "running", "claimed_at": datetime.now(timezone.utc).isoformat()})
            claimed = True
        await db.commit()
    elif req.edits:
        raise HTTPException(status_code=422, detail="Edits apply to a stored proposal (message_id).")

    try:
        result = await _run_action(db, req, current_user, tenant_id)
    except Exception:
        if claimed:
            await db.rollback()
            await _settle(db, req, user_id, tenant_id, None)
        raise
    if req.message_id is not None:
        await _settle(db, req, user_id, tenant_id, result)
    return result


@router.post("/actions/dismiss", response_model=ActionProposal)
async def dismiss_action(
    *,
    db: AsyncSession = Depends(deps.get_db),
    req: ActionDismissRequest,
    current_user: User = Depends(deps.get_current_active_user),
    tenant_id: int = Depends(deps.get_effective_tenant_id),
) -> Any:
    """Record that the user cancelled or dismissed a proposal of their own, so it can't be confirmed later
    (e.g. after reopening the panel). A proposal that already ran or was already settled is left as it is."""
    msg = await _lock_message(db, req.message_id, current_user.id, tenant_id)
    stored = _proposal(msg, req.suggestion_index)
    if not stored.get("result") and not live_state(stored):
        stored = {k: v for k, v in stored.items() if k != "claimed_at"} | {"state": req.state}
        _set_proposal(msg, req.suggestion_index, stored)
    await db.commit()
    return stored


async def _conversation_case(db: AsyncSession, session_id: int) -> Optional[int]:
    """The most recent case created by a confirmed action in this conversation."""
    rows = (await db.execute(
        select(CopilotMessage.action).where(CopilotMessage.session_id == session_id)
        .order_by(CopilotMessage.id.desc())
    )).scalars().all()
    for action in rows:
        if (isinstance(action, dict) and action.get("type") == "create_case"
                and (action.get("result") or {}).get("case_id")):
            return action["result"]["case_id"]
    return None


async def _attach_artifact(db: AsyncSession, *, tenant_id: int, user_id: int, case_id: int, value: str,
                           atype: ArtifactType, description: Optional[str] = None) -> Artifact:
    """Find-or-create the (shared, non-isolated) artifact and link it to the case once."""
    artifact = (await db.execute(
        select(Artifact).where(
            Artifact.value == value, Artifact.artifact_type == atype, Artifact.custom_type_id.is_(None),
            Artifact.tenant_id == tenant_id, Artifact.isolated == False,  # noqa: E712
        )
    )).scalars().first()
    if not artifact:
        artifact = Artifact(artifact_type=atype, value=value, description=description,
                            isolated=False, tenant_id=tenant_id, created_by=user_id)
        db.add(artifact)
        await db.flush()
    link = await db.execute(
        select(CaseArtifact).where(CaseArtifact.case_id == case_id, CaseArtifact.artifact_id == artifact.id)
    )
    if not link.scalars().first():
        db.add(CaseArtifact(case_id=case_id, artifact_id=artifact.id, added_by=user_id))
        db.add(TimelineEvent(case_id=case_id, user_id=user_id, event_type="artifact_added",
                             content=f"Added artifact: {value} ({atype.value})"))
    await create_audit_log(db=db, entity_type="artifact", entity_id=artifact.id, action="create",
                           tenant_id=tenant_id, user_id=user_id)
    return artifact


def _artifact_type(raw: Any) -> ArtifactType:
    try:
        return ArtifactType(str(raw or "other").lower())
    except ValueError:
        return ArtifactType.OTHER


async def _run_action(db: AsyncSession, req: ActionExecuteRequest, current_user: User, tenant_id: int) -> ActionResult:
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
        case, triage_id = await case_service.create_case_record(
            db, tenant_id=tenant_id, user_id=current_user.id,
            data={"title": title, "description": p.get("description"), "severity": severity,
                  "status": CaseStatus.NEW})
        # Indicators from the conversation, shown on the card before Confirm (see chat_copilot).
        added = []
        for item in (p.get("artifacts") or [])[:MAX_CASE_ARTIFACTS]:
            value = str((item or {}).get("value") or "").strip()[:2048]
            if value:
                await _attach_artifact(db, tenant_id=tenant_id, user_id=current_user.id, case_id=case.id,
                                       value=value, atype=_artifact_type(item.get("artifact_type")))
                added.append(value)
        await db.commit()
        from app.tasks.triage import run_case_triage_task  # lazy: tasks.triage imports this module
        run_case_triage_task.delay(triage_id)
        extra = f" with {len(added)} artifact(s): {', '.join(added)}" if added else ""
        return ActionResult(ok=True, message=f"Created case #{case.id}: {title}{extra}", case_id=case.id)

    if req.type == "add_artifact":
        if not req.case_id:
            raise HTTPException(status_code=400, detail="Open a case (or create one in this chat) to add an artifact.")
        await _get_case_in_tenant(db, req.case_id, tenant_id)
        value = (p.get("value") or "").strip()
        if not value:
            raise HTTPException(status_code=400, detail="An artifact value is required.")
        atype = _artifact_type(p.get("artifact_type"))
        await _attach_artifact(db, tenant_id=tenant_id, user_id=current_user.id, case_id=req.case_id,
                               value=value, atype=atype, description=p.get("description"))
        await db.commit()
        return ActionResult(ok=True, message=f"Added artifact `{value}` ({atype.value}) to case #{req.case_id}.",
                            case_id=req.case_id)

    if req.type == "add_timeline_note":
        if not req.case_id:
            raise HTTPException(status_code=400, detail="Open a case (or create one in this chat) to add a note.")
        note_case = await _get_case_in_tenant(db, req.case_id, tenant_id)
        content = (p.get("content") or "").strip()
        if not content:
            raise HTTPException(status_code=400, detail="Note content is required.")
        await case_service.add_timeline_note(db, case=note_case, content=content, user_id=current_user.id)
        await create_audit_log(db=db, entity_type="case", entity_id=req.case_id, action="comment",
                               tenant_id=tenant_id, user_id=current_user.id)
        await db.commit()
        return ActionResult(ok=True, message=f"Added a note to case #{req.case_id}.", case_id=req.case_id)

    if req.type == "update_case":
        if not req.case_id:
            raise HTTPException(status_code=400, detail="Open a case to update it.")
        case = await _get_case_in_tenant(db, req.case_id, tenant_id)
        changes: Dict[str, Any] = {}
        update_data: Dict[str, Any] = {}
        if p.get("status"):
            try:
                update_data["status"] = CaseStatus(str(p["status"]).lower())
                changes["status"] = update_data["status"].value
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid status.")
        if p.get("severity"):
            try:
                update_data["severity"] = CaseSeverity(str(p["severity"]).lower())
                changes["severity"] = update_data["severity"].value
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid severity.")
        if p.get("tags") is not None:
            new_tags = [p["tags"]] if isinstance(p["tags"], str) else p["tags"]  # never iterate a string
            if not isinstance(new_tags, list) or not all(isinstance(t, str) for t in new_tags):
                raise HTTPException(status_code=400, detail="Tags must be a list of text values.")
            # Added to the case's tags, never replacing them: a proposal naming one tag must not wipe the rest.
            tags = list(dict.fromkeys(t.strip() for t in [*(case.tags or []), *new_tags] if t.strip()))
            update_data["tags"] = tags
            changes["tags"] = tags
        if not changes:
            raise HTTPException(status_code=400, detail="Nothing to update.")
        if update_data.get("status") == CaseStatus.CLOSED:
            # Outcome and closing note come from the analyst on the card (the model can't supply them).
            if req.outcome is not None:
                if req.outcome not in CASE_OUTCOMES:
                    raise HTTPException(status_code=400, detail="Invalid outcome.")
                update_data["outcome"] = req.outcome
            if req.closing_note is not None:
                update_data["closing_note"] = req.closing_note.strip()
        # Same guards as REST/MCP: closing needs an outcome + closing note.
        await case_service.check_case_update(db, case=case, data=update_data, tenant_id=tenant_id, role=role)
        await case_service.apply_case_update(db, case=case, update_data=update_data, user_id=current_user.id)
        await db.commit()
        return ActionResult(ok=True, message=f"Updated case #{case.id} ({', '.join(changes)}).", case_id=case.id)

    if req.type == "apply_playbook":
        if not req.case_id:
            raise HTTPException(status_code=400, detail="Open a case to apply a playbook.")
        case = await _get_case_in_tenant(db, req.case_id, tenant_id)
        template_id = p.get("template_id")
        if not template_id:
            raise HTTPException(status_code=400, detail="A playbook template is required.")
        n = await task_service.apply_playbook(db, case=case, template_id=int(template_id), tenant_id=tenant_id,
                                              user_id=current_user.id)
        await db.commit()
        return ActionResult(ok=True, message=f"Applied playbook to case #{case.id} ({n} task(s) added).",
                            case_id=case.id)

    # find_related (read-only)
    related = await _find_related_cases(db, tenant_id, req.case_id, (p.get("value") or "").strip() or None)
    msg = (f"Found {len(related)} related case(s)." if related
           else "No other cases share these indicators.")
    return ActionResult(ok=True, message=msg, related=related)
