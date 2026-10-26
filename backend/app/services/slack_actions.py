"""Handles verified Slack block_actions payloads (runs in Celery)."""
import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import func, or_, select

from app.db.session import AsyncSessionLocal
from app.models.case import Case, CaseStatus
from app.models.membership import TenantMembership
from app.models.tenant import Tenant
from app.models.user import User
from app.models.workflow import WorkflowRun, WorkflowRunStep
from app.services.case_service import apply_case_update
from app.services.slack_blocks import card_text, case_card
from app.services.slack_service import SlackError, load_integration, parse_action_value, respond, slack_call
from app.utils.tenant_state import tenant_active  # noqa: F401  (re-exported: Slack entry points import it from here)
from app.workflows.events import emit_event
from app.workflows.templating import slack_escape

logger = logging.getLogger(__name__)

_CASE_UPDATES = {
    "ack": ("Acknowledged", lambda user: {"status": CaseStatus.IN_PROGRESS}),
    "assign": ("Assigned", lambda user: {"owner_id": user.id}),
    "close": ("Closed", lambda user: {"status": CaseStatus.CLOSED}),
}


async def _ephemeral(payload: dict, text: str) -> None:
    if payload.get("response_url"):
        try:
            await respond(payload["response_url"], {"response_type": "ephemeral", "replace_original": False, "text": text})
        except SlackError:
            logger.exception("could not send ephemeral Slack reply")


async def _slack_email(token: str, slack_user_id: str) -> Optional[str]:
    try:
        info = await slack_call(token, "users.info", user=slack_user_id)
    except SlackError:
        return None
    return (info.get("user") or {}).get("profile", {}).get("email")


TENANT_DEACTIVATED = "This workspace's SOC Hub tenant is deactivated."


async def _platform_user(db, tenant_id: int, email: Optional[str]) -> Optional[User]:
    if not email:
        return None
    return (await db.execute(
        select(User).outerjoin(TenantMembership, (TenantMembership.user_id == User.id) & (TenantMembership.tenant_id == tenant_id))
        .where(func.lower(User.email) == email.lower(), User.is_active == True,  # noqa: E712
               or_(User.is_super_admin == True, TenantMembership.role.in_(["admin", "analyst"])),  # noqa: E712
               select(Tenant.id).where(Tenant.id == tenant_id, Tenant.is_active.isnot(False)).exists())
    )).scalars().first()


async def _handle_case_action(tenant_id: int, payload: dict, btn_tenant_id: int, case_id: int, action: str) -> None:
    if btn_tenant_id != tenant_id:
        return  # button belongs to another tenant's message
    clicker = payload["user"]["id"]
    async with AsyncSessionLocal() as db:
        _, token = await load_integration(db, tenant_id)
        user = await _platform_user(db, tenant_id, await _slack_email(token, clicker))
        if not user:
            await _ephemeral(payload, "Your Slack account isn't linked to a sochub user with analyst access in this tenant.")
            return
        case = (await db.execute(select(Case).where(Case.id == case_id, Case.tenant_id == tenant_id))).scalars().first()
        if not case:
            await _ephemeral(payload, f"Case #{case_id} no longer exists.")
            return
        if action == "ack" and case.status in (CaseStatus.RESOLVED, CaseStatus.CLOSED):
            await _ephemeral(payload, f"Case #{case_id} is already {case.status.value}.")
            return  # a stale/replayed Acknowledge must not reopen a finished case
        if action == "warroom":
            from app.services.slack_bot import open_war_room
            channel = await open_war_room(db, tenant_id, case, user, clicker)
            await _ephemeral(payload, f"War room for case #{case_id}: <#{channel}>")
            return
        label, build = _CASE_UPDATES[action]
        changes = await apply_case_update(db, case=case, update_data=build(user), user_id=user.id)
        await db.commit()
        owner = (await db.get(User, case.owner_id)) if case.owner_id else None
        card = case_card(case, (owner.full_name or owner.email) if owner else None,
                         note=f"{label} by {slack_escape(payload['user'].get('name') or 'a responder')}")
    if not changes:
        return  # nothing changed (e.g. already in that state): don't announce it
    emit_event(tenant_id, "case.updated", case_id=case_id, changes=changes)
    if payload.get("response_url"):
        # Redraw the clicked card in place (works for channel, DM and ephemeral messages alike).
        await respond(payload["response_url"], {"replace_original": True, "text": card_text(case), "blocks": card})


async def _handle_suggestion_action(tenant_id: int, payload: dict, btn_tenant_id: int, suggestion_id: int,
                                    verb: str) -> None:
    """Add/Set/Dismiss on a SOC-bot suggestion. The reply is re-rendered by the after-commit war_room.rerender card."""
    if btn_tenant_id != tenant_id:
        return  # button belongs to another tenant's message
    from app.services.war_room_suggestions import resolve_suggestion_ex
    clicker = payload["user"]["id"]
    async with AsyncSessionLocal() as db:
        _, token = await load_integration(db, tenant_id)
        user = await _platform_user(db, tenant_id, await _slack_email(token, clicker))
        if not user:
            await _ephemeral(payload, "Your Slack account isn't linked to a sochub user with analyst access in this tenant.")
            return
        try:
            _, applied = await resolve_suggestion_ex(db, tenant_id=tenant_id, suggestion_id=suggestion_id,
                                     accept=verb == "accept", user=user, slack_user_id=clicker)
        except LookupError:
            await _ephemeral(payload, "This suggestion no longer exists.")
            return
        await db.commit()
    if not applied:
        await _ephemeral(payload, "This suggestion was already resolved.")


def authorize_answer(step_status: str, target_slack_id: Optional[str], clicker_id: str) -> Optional[str]:
    if step_status != "waiting":
        return "This request was already answered or has expired."
    if not target_slack_id or target_slack_id != clicker_id:
        return "This request isn't for you."
    return None


async def handle_answer(tenant_id: int, payload: dict, wait_token: str, index: int) -> None:
    from app.tasks.workflows import advance_run_task

    clicker = payload["user"]["id"]
    async with AsyncSessionLocal() as db:
        # Row lock + status re-check under the lock: double clicks/races resume the run at most once.
        step = (await db.execute(
            select(WorkflowRunStep).join(WorkflowRun, WorkflowRun.id == WorkflowRunStep.run_id)
            .where(WorkflowRunStep.wait_token == wait_token, WorkflowRun.tenant_id == tenant_id)
            .with_for_update(of=WorkflowRunStep)
        )).scalars().first()
        if not step:
            await _ephemeral(payload, "This request was already answered or has expired.")
            return
        step_id, run_id = step.id, step.run_id
        waiting = dict(step.output or {})
        reason = authorize_answer(step.status, waiting.get("target_slack_id"), clicker)
        if not reason and step.wait_expires_at and step.wait_expires_at <= datetime.now(timezone.utc):
            reason = "This request was already answered or has expired."  # past deadline, sweeper not yet run
        buttons = waiting.get("buttons") or []
        if reason or not 0 <= index < len(buttons):
            await _ephemeral(payload, reason or "Unknown option.")
            return
        try:
            _, token = await load_integration(db, tenant_id)
        except SlackError:
            logger.exception("Slack unavailable while accepting answer for step %s", step_id)
            await _ephemeral(payload, "Slack isn't available for this workspace right now; your answer was not recorded.")
            return
        label = buttons[index]
        step.output = {**waiting, "response": label, "responder_slack_id": clicker,
                       "responder_email": await _slack_email(token, clicker), "timed_out": False}
        step.status, step.finished_at = "succeeded", datetime.now(timezone.utc)
        await db.commit()

    try:
        await slack_call(token, "chat.update", channel=waiting["channel"], ts=waiting["message_ts"],
                         text=f"You answered: {slack_escape(label)}",
                         blocks=[{"type": "section", "text": {"type": "mrkdwn", "text": f":white_check_mark: You answered: *{slack_escape(label)}*"}}])
    except Exception:  # cosmetic; the answer is committed, so the run must still resume
        logger.warning("could not update answered Slack message for step %s", step_id, exc_info=True)
    advance_run_task.delay(run_id)


async def handle_interaction(tenant_id: int, payload: dict) -> None:
    async with AsyncSessionLocal() as db:  # queued before a deactivation: still never acts
        if not await tenant_active(db, tenant_id):
            return
    for action in payload.get("actions") or []:
        parsed = parse_action_value(action.get("value", ""))
        if not parsed:
            continue
        try:
            if parsed[0] == "case":
                await _handle_case_action(tenant_id, payload, *parsed[1:])
            elif parsed[0] == "wrs":
                await _handle_suggestion_action(tenant_id, payload, *parsed[1:])
            elif parsed[0] == "wf":
                await handle_answer(tenant_id, payload, parsed[1], parsed[2])
        except SlackError:
            logger.exception("Slack interaction failed for tenant %s", tenant_id)
