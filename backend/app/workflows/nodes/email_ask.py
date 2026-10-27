import asyncio
import re
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.core.config import settings
from app.models.tenant import Tenant
from app.services import email_service
from app.workflows.nodes import WAIT, NodeContext, NodeError, executor
from app.workflows.nodes.slack import button_labels
from app.workflows.errors import RetryableNodeError

_EMAIL = re.compile(r"^[^@\s<>\"']{1,64}@[A-Za-z0-9.-]{1,253}\.[A-Za-z]{2,}$")
DEFAULT_BUTTONS = ["It was me", "It wasn't me"]


@executor("email_ask_user")
async def run_email_ask_user(nctx: NodeContext, config: dict):
    """Email a person a question and wait for their answer on a one-time page (/verify/<token>). Output, once
    answered or timed out: {response, responder_email, timed_out}, like slack_ask_user."""
    email = str(config["email"]).strip()
    message = str(config["message"]).strip()[:2000]
    if not _EMAIL.match(email):
        raise NodeError("email is not a valid address — check the template")
    if not message:
        raise NodeError("message is empty — check the template")
    buttons = button_labels(config.get("buttons")) if config.get("buttons") else list(DEFAULT_BUTTONS)
    if len(buttons) > 5 or any(len(b) > 75 for b in buttons):
        raise NodeError("at most 5 buttons of up to 75 characters")
    raw_hours = config.get("timeout_hours")
    try:
        hours = 1.0 if raw_hours in (None, "") else float(raw_hours)
        valid = not isinstance(raw_hours, bool) and 0 < hours <= 168
    except (TypeError, ValueError, OverflowError):
        valid = False
    if not valid:
        raise NodeError("timeout_hours must be between 0 and 168")
    if not email_service._smtp_configured():
        raise NodeError("email is not set up on this server (SMTP_HOST / SMTP_FROM_EMAIL)")
    org = (await nctx.db.execute(select(Tenant.name).where(Tenant.id == nctx.run.tenant_id))).scalar() or "Security"
    wait_token = secrets.token_urlsafe(32)
    sent = await asyncio.to_thread(email_service.send_user_verification_email, email, org_name=org, message=message,
                                   link=f"{settings.FRONTEND_URL.rstrip('/')}/verify/{wait_token}", hours=hours)
    if not sent:
        raise RetryableNodeError(f"could not send the email to {email}")
    nctx.step.wait_token = wait_token
    nctx.step.wait_expires_at = datetime.now(timezone.utc) + timedelta(hours=hours)
    nctx.step.output = {"channel_type": "email", "target_email": email, "buttons": buttons, "message": message}
    return WAIT
