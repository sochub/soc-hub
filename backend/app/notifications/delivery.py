"""External delivery (Slack DM as a case card, then email) for mention/assigned/SLA-breach notifications."""
import html
import logging

from sqlalchemy import event, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.case import Case
from app.models.escalation import CaseEscalation
from app.models.notification import EXTERNAL_TYPES, Notification
from app.models.membership import TenantMembership
from app.models.user import User
from app.services.email_service import _send_email, _smtp_configured
from app.services.slack_blocks import case_card
from app.services.slack_service import SlackError, load_integration, slack_call
from app.utils.tenant_state import tenant_deactivated
from app.workflows.templating import slack_escape

logger = logging.getLogger(__name__)
_TRANSIENT_CODES = {"ratelimited", "rate_limited", "internal_error", "fatal_error", "service_unavailable", "request_timeout"}


class TransientDeliveryError(Exception):
    pass


def _enqueue(ids) -> None:
    from app.tasks.notifications import deliver_notifications_task  # late import: worker import cycle
    deliver_notifications_task.delay(list(ids))


def _after_commit(session) -> None:
    if session.in_nested_transaction():
        return  # a released savepoint is not durable yet; wait for the outer commit
    ids = session.info.pop("notif_external", None)
    if ids:
        try:
            _enqueue(ids)
        except Exception as e:  # the commit already succeeded; in-app notifications stand
            logger.warning("failed to enqueue notification delivery count=%s err=%s", len(ids), type(e).__name__)


def _after_soft_rollback(session, previous_transaction) -> None:
    # Only an outermost rollback discards the stash; a failed SAVEPOINT must not drop ids
    # stashed by earlier successful hooks in the same transaction.
    if not previous_transaction.nested and previous_transaction.parent is None:
        session.info.pop("notif_external", None)


def register_after_commit_hook() -> None:
    if not event.contains(Session, "after_commit", _after_commit):
        event.listen(Session, "after_commit", _after_commit)
        event.listen(Session, "after_soft_rollback", _after_soft_rollback)


def _is_transient(e: SlackError) -> bool:
    return e.code is None or e.code in _TRANSIENT_CODES  # no code => transport error


def _link(case_id: int) -> str:
    base = (settings.FRONTEND_URL or "").rstrip("/")
    return f"{base}/cases/{case_id}" if base else ""


def _row_tier(row) -> dict:
    """The tier an escalation row targets, resolved from its own snapshot (the live tier may be gone)."""
    snap = row.policy_snapshot or {}
    pos = (snap.get("to") or {}).get("position")
    return next((t for t in snap.get("tiers", []) if t.get("position") == pos), None) or {}


async def deliver_one(db, notification_id: int, redis) -> str:
    n = (await db.execute(select(Notification).where(Notification.id == notification_id))).scalars().first()
    if n is None or n.type not in EXTERNAL_TYPES:
        return "skipped"
    if await tenant_deactivated(db, n.tenant_id):  # no email/Slack for a deactivated tenant
        return "skipped"
    if n.escalation_id is not None:  # additive per-tier channels; never rate limited
        row = (await db.execute(select(CaseEscalation).where(
            CaseEscalation.id == n.escalation_id, CaseEscalation.tenant_id == n.tenant_id))).scalars().first()
        if row is None:
            return "skipped"
        tier = _row_tier(row)
        # a tier Slack channel replaces the per-member DMs; email stays per member
        dm = bool(tier.get("notify_slack")) and not tier.get("slack_channel")
        email = bool(tier.get("notify_email"))
        if not dm and not email:
            return "skipped"
        return await _deliver(db, n, slack=dm, email=email, additive=True)
    key = f"notif:ext:{n.user_id}:{n.case_id}"
    if not await redis.set(key, "1", nx=True, ex=300):
        return "rate_limited"
    try:
        return await _deliver(db, n)
    except TransientDeliveryError:
        await redis.delete(key)  # let the retry through
        raise
    except Exception:
        await redis.delete(key)  # a redelivery must not be silently rate-limited
        raise


async def _deliver(db, n, slack: bool = True, email: bool = True, additive: bool = False) -> str:
    """Default: Slack DM, falling back to email. additive (escalations): every enabled channel is sent."""
    user = (await db.execute(select(User).where(User.id == n.user_id))).scalars().first()
    case = (await db.execute(select(Case).where(Case.id == n.case_id, Case.tenant_id == n.tenant_id))).scalars().first()
    if not user or not case or not user.is_active:
        return "skipped"
    member = (await db.execute(select(TenantMembership.id).where(
        TenantMembership.user_id == n.user_id, TenantMembership.tenant_id == n.tenant_id))).first()
    if member is None:
        return "skipped"
    title, link = (case.title or "")[:120], _link(case.id)
    sent = []
    token = None
    if slack:
        try:
            _, token = await load_integration(db, n.tenant_id)
        except SlackError:
            token = None
    if token:
        try:
            uid = (await slack_call(token, "users.lookupByEmail", email=user.email))["user"]["id"]
            channel = (await slack_call(token, "conversations.open", users=uid))["channel"]["id"]
            text = f"{slack_escape(n.summary)}: {slack_escape(title)}" + (f"\n<{link}|Open case>" if link else "")
            owner = (await db.get(User, case.owner_id)) if case.owner_id else None
            blocks = case_card(case, (owner.full_name or owner.email) if owner else None, note=n.summary)
            await slack_call(token, "chat.postMessage", channel=channel, text=text, blocks=blocks)
            if not additive:
                return "slack"
            sent.append("slack")
        except SlackError as e:
            if _is_transient(e):
                raise TransientDeliveryError(e.code or type(e).__name__) from None
            # users_not_found etc.: fall through to email
    if email and _smtp_configured():
        body = f"<p>{html.escape(n.summary)}: <strong>{html.escape(title)}</strong></p>"
        if link:
            body += f'<p><a href="{html.escape(link)}">Open case</a></p>'
        if _send_email(user.email, n.summary, body, log_recipient=False):
            sent.append("email")
    return "+".join(sent) or "none"


async def post_escalation_channel(db, row_id: int) -> str:
    """Post one case card to the tier's Slack channel for a still-pending escalation row. Never raises."""
    try:
        row = (await db.execute(select(CaseEscalation).where(CaseEscalation.id == row_id))).scalars().first()
        if row is None or row.status != "pending" or await tenant_deactivated(db, row.tenant_id):
            return "skipped"
        tier = _row_tier(row)
        channel = tier.get("slack_channel")
        case = (await db.execute(select(Case).where(Case.id == row.case_id, Case.tenant_id == row.tenant_id))).scalars().first()
        if not channel or case is None:
            return "skipped"
        try:
            _, token = await load_integration(db, row.tenant_id)
        except SlackError:
            return "skipped"
        title, link = (case.title or "")[:120], _link(case.id)
        head = f"Case escalated to {tier.get('name') or 'next tier'}"
        text = f"{slack_escape(head)}: {slack_escape(title)}" + (f"\n<{link}|Open case>" if link else "")
        blocks = case_card(case, None, note=f"{head}: {row.summary}"[:500])
        await slack_call(token, "chat.postMessage", channel=channel, text=text, blocks=blocks)
        return "posted"
    except Exception as e:  # channel posts are best effort
        logger.warning("escalation channel post failed row=%s err=%s", row_id, type(e).__name__)
        return "failed"
