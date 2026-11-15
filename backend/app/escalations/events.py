"""After-commit emission of case.escalated / case.escalation_closed workflow events.

The service stashes events on the session during the request; they are emitted once the outermost commit is durable
and dropped on an outermost rollback (same pattern as app/notifications/delivery.py).
"""
import logging

from sqlalchemy import event
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)
_KEY = "esc_events"
_CH_KEY = "esc_channel_posts"


def stash(db, tenant_id: int, event_type: str, case_id: int, payload: dict) -> None:
    db.sync_session.info.setdefault(_KEY, []).append((tenant_id, event_type, case_id, payload))


def stash_channel(db, row_id: int) -> None:
    db.sync_session.info.setdefault(_CH_KEY, []).append(row_id)


def _enqueue_channel(row_id: int) -> None:
    from app.tasks.escalations import post_escalation_channel_task  # late import: worker import cycle
    post_escalation_channel_task.delay(row_id)


def _after_commit(session) -> None:
    if session.in_nested_transaction():
        return  # a released savepoint is not durable yet; wait for the outer commit
    for row_id in session.info.pop(_CH_KEY, None) or []:
        try:
            _enqueue_channel(row_id)
        except Exception as e:  # the commit already succeeded; never fail over a channel post
            logger.warning("failed to enqueue escalation channel post row=%s err=%s", row_id, type(e).__name__)
    events = session.info.pop(_KEY, None)
    if not events:
        return
    from app.workflows import events as wf_events  # late import: worker import cycle
    for tenant_id, event_type, case_id, payload in events:
        try:
            wf_events.emit_event(tenant_id, event_type, case_id=case_id, changes=payload)
        except Exception as e:  # the commit already succeeded; never fail over an event
            logger.warning("failed to emit %s case=%s err=%s", event_type, case_id, type(e).__name__)


def _after_soft_rollback(session, previous_transaction) -> None:
    # Only an outermost rollback discards the stash; a failed SAVEPOINT must not drop earlier events.
    if not previous_transaction.nested and previous_transaction.parent is None:
        session.info.pop(_KEY, None)
        session.info.pop(_CH_KEY, None)


def register_after_commit_hook() -> None:
    if not event.contains(Session, "after_commit", _after_commit):
        event.listen(Session, "after_commit", _after_commit)
        event.listen(Session, "after_soft_rollback", _after_soft_rollback)
