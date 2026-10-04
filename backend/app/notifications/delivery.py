"""External delivery (Slack DM, then email) for mention/assigned notifications."""
import html
import logging
import re

from sqlalchemy import event, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.case import Case
from app.models.notification import EXTERNAL_TYPES, Notification
from app.models.user import User
from app.services.email_service import _send_email, _smtp_configured
from app.services.slack_service import SlackError, load_integration, slack_call
from app.workflows.templating import slack_escape

logger = logging.getLogger(__name__)
_SLACK_CODE = re.compile(r":\s*([a-z_]+)$")
_TRANSIENT_CODES = {"ratelimited", "rate_limited", "internal_error", "fatal_error", "service_unavailable", "request_timeout"}


class TransientDeliveryError(Exception):
    pass


def _enqueue(ids) -> None:
    from app.tasks.notifications import deliver_notifications_task  # late import: worker import cycle
    deliver_notifications_task.delay(list(ids))


def _after_commit(session) -> None:
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
    m = _SLACK_CODE.search(str(e))
    return m is None or m.group(1) in _TRANSIENT_CODES  # no code => transport error


def _link(case_id: int) -> str:
    base = (settings.FRONTEND_URL or "").rstrip("/")
    return f"{base}/cases/{case_id}" if base else ""


async def deliver_one(db, notification_id: int, redis) -> str:
    n = (await db.execute(select(Notification).where(Notification.id == notification_id))).scalars().first()
    if n is None or n.type not in EXTERNAL_TYPES:
        return "skipped"
    key = f"notif:ext:{n.user_id}:{n.case_id}"
    if not await redis.set(key, "1", nx=True, ex=300):
        return "rate_limited"
    user = (await db.execute(select(User).where(User.id == n.user_id))).scalars().first()
    case = (await db.execute(select(Case).where(Case.id == n.case_id))).scalars().first()
    if not user or not case or not user.is_active:
        return "skipped"
    title, link = (case.title or "")[:120], _link(case.id)
    try:
        _, token = await load_integration(db, n.tenant_id)
    except SlackError:
        token = None
    if token:
        try:
            uid = (await slack_call(token, "users.lookupByEmail", email=user.email))["user"]["id"]
            channel = (await slack_call(token, "conversations.open", users=uid))["channel"]["id"]
            text = f"{slack_escape(n.summary)}: {slack_escape(title)}" + (f"\n<{link}|Open case>" if link else "")
            await slack_call(token, "chat.postMessage", channel=channel, text=text)
            return "slack"
        except SlackError as e:
            if _is_transient(e):
                await redis.delete(key)  # let the retry through
                raise TransientDeliveryError(type(e).__name__) from None
            # users_not_found etc.: fall through to email
    if _smtp_configured():
        body = f"<p>{html.escape(n.summary)}: <strong>{html.escape(title)}</strong></p>"
        if link:
            body += f'<p><a href="{html.escape(link)}">Open case</a></p>'
        if _send_email(user.email, n.summary, body):
            return "email"
    return "none"
