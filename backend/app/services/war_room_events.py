"""After-commit hand-off of war-room cards: stash during the request, enqueue once the commit is durable."""
import logging

from sqlalchemy import event
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)
_KEY = "war_room_cards"


def queue_card(db, tenant_id: int, event_type: str, case_id: int, changes: dict) -> None:
    db.sync_session.info.setdefault(_KEY, []).append((tenant_id, event_type, case_id, changes))


def _after_commit(session) -> None:
    if session.in_nested_transaction():
        return  # a released savepoint is not durable yet; wait for the outer commit
    cards = session.info.pop(_KEY, None)
    if not cards:
        return
    try:
        from app.tasks.slack import slack_case_event_task  # late import: worker import cycle
        for tenant_id, event_type, case_id, changes in cards:
            slack_case_event_task.delay(tenant_id, event_type, case_id, changes)
    except Exception as e:  # the commit already succeeded; the card is best-effort
        logger.warning("failed to enqueue war-room card count=%s err=%s", len(cards), type(e).__name__)


def _after_soft_rollback(session, previous_transaction) -> None:
    # Only an outermost rollback discards the stash; a failed SAVEPOINT must not drop earlier cards.
    if not previous_transaction.nested and previous_transaction.parent is None:
        session.info.pop(_KEY, None)


def register_war_room_hook() -> None:
    if not event.contains(Session, "after_commit", _after_commit):
        event.listen(Session, "after_commit", _after_commit)
        event.listen(Session, "after_soft_rollback", _after_soft_rollback)
