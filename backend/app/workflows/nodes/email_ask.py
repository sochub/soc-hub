import asyncio
import logging
import re
import secrets
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.core.config import settings
from app.models.membership import TenantMembership
from app.models.tenant import Tenant
from app.models.tenant_enrichment_config import TenantEnrichmentConfig
from app.models.user import User
from app.services import email_service
from app.workflows.nodes import WAIT, NodeContext, NodeError, executor
from app.workflows.nodes.slack import button_labels
from app.workflows.errors import RetryableNodeError

_EMAIL = re.compile(r"^[^@\s<>\"']{1,64}@[A-Za-z0-9.-]{1,253}\.[A-Za-z]{2,}$")
DEFAULT_BUTTONS = ["It was me", "It wasn't me"]
logger = logging.getLogger(__name__)


async def tenant_domains(db, tenant_id: int) -> set:
    """The organisation's own mail domains: those of its members' emails, plus the tenant's internal domains
    (threat-intel settings). A "Security check" email only goes to these, so a workflow can't turn the platform's
    SMTP into a phishing relay to arbitrary addresses.
    ponytail: a member on a shared mailbox provider (gmail.com) opens that whole provider; ceiling: a dedicated
    per-tenant recipient-domain setting."""
    emails = (await db.execute(select(User.email).join(TenantMembership, TenantMembership.user_id == User.id).where(
        TenantMembership.tenant_id == tenant_id, User.is_active.isnot(False)))).scalars().all()
    out = {e.rsplit("@", 1)[1].lower() for e in emails if e and "@" in e}
    internal = (await db.execute(select(TenantEnrichmentConfig.internal_domains).where(
        TenantEnrichmentConfig.tenant_id == tenant_id))).scalar()
    out.update(str(d).lower().strip(".") for d in (internal or []) if d)
    return out


def _domain_allowed(email: str, domains: set) -> bool:
    d = email.rsplit("@", 1)[1].lower().rstrip(".")
    return any(d == a or d.endswith("." + a) for a in domains)


async def _reserve_send(tenant_id: int) -> bool:
    """Fixed hourly window per tenant (EMAIL_ASK_HOURLY_CAP). Fails open like the other limiters: Redis down also
    means the Celery broker is down, so nothing would run anyway."""
    import redis.asyncio as aioredis
    key = f"email_ask:{tenant_id}:{int(time.time() // 3600)}"
    r = aioredis.from_url(settings.REDIS_URL, socket_timeout=2, socket_connect_timeout=2)
    try:
        n = await r.incr(key)
        await r.expire(key, 3600, nx=True)
    except Exception as exc:
        logger.warning("email_ask limiter unavailable: %s", type(exc).__name__)
        return True
    finally:
        await r.aclose()
    return n <= settings.EMAIL_ASK_HOURLY_CAP


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
    if not _domain_allowed(email, await tenant_domains(nctx.db, nctx.run.tenant_id)):
        raise NodeError("email must be at one of this organization's domains (its members' email domains, or the "
                        "internal domains in the threat-intel settings)")
    if not await _reserve_send(nctx.run.tenant_id):
        raise NodeError(f"this organization sent {settings.EMAIL_ASK_HOURLY_CAP} verification emails in the last "
                        "hour; try again later")
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
