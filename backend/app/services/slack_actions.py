"""Handles verified Slack block_actions payloads (runs in Celery)."""
import logging
from typing import Optional

from sqlalchemy import func, or_, select

from app.db.session import AsyncSessionLocal
from app.models.case import Case, CaseStatus
from app.models.membership import TenantMembership
from app.models.user import User
from app.services.case_service import apply_case_update
from app.services.slack_service import SlackError, load_integration, parse_action_value, respond, slack_call
from app.workflows.events import emit_event

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


async def _platform_user(db, tenant_id: int, email: Optional[str]) -> Optional[User]:
    if not email:
        return None
    return (await db.execute(
        select(User).outerjoin(TenantMembership, (TenantMembership.user_id == User.id) & (TenantMembership.tenant_id == tenant_id))
        .where(func.lower(User.email) == email.lower(), User.is_active == True,  # noqa: E712
               or_(User.is_super_admin == True, TenantMembership.role.in_(["admin", "analyst"])))  # noqa: E712
    )).scalars().first()


async def _handle_case_action(tenant_id: int, payload: dict, btn_tenant_id: int, case_id: int, action: str) -> None:
    if btn_tenant_id != tenant_id:
        return  # button belongs to another tenant's message
    clicker = payload["user"]["id"]
    async with AsyncSessionLocal() as db:
        _, token = await load_integration(db, tenant_id)
        user = await _platform_user(db, tenant_id, await _slack_email(token, clicker))
        if not user:
            await _ephemeral(payload, "Your Slack account isn't linked to a SOC Hub user with analyst access in this tenant.")
            return
        case = (await db.execute(select(Case).where(Case.id == case_id, Case.tenant_id == tenant_id))).scalars().first()
        if not case:
            await _ephemeral(payload, f"Case #{case_id} no longer exists.")
            return
        label, build = _CASE_UPDATES[action]
        changes = await apply_case_update(db, case=case, update_data=build(user), user_id=user.id)
        await db.commit()
    if changes:
        emit_event(tenant_id, "case.updated", case_id=case_id, changes=changes)
    if payload.get("response_url"):
        await respond(payload["response_url"], {"response_type": "in_channel", "replace_original": False,
                                                "text": f"{label} case #{case_id} — <@{clicker}>"})


async def handle_interaction(tenant_id: int, payload: dict) -> None:
    for action in payload.get("actions") or []:
        parsed = parse_action_value(action.get("value", ""))
        if not parsed:
            continue
        try:
            if parsed[0] == "case":
                await _handle_case_action(tenant_id, payload, *parsed[1:])
        except SlackError:
            logger.exception("Slack interaction failed for tenant %s", tenant_id)
