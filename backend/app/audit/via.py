"""'via' attribution: while an MCP client acts for a user, activity entries and audit rows are tagged with it."""
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Dict, Iterator, Optional, Tuple

from sqlalchemy import event

ACTOR_MAX = 128  # audit_log.actor column length
VIA_CLIENT_MAX = 200  # timeline_events.via_client column length

current_via: ContextVar[Optional[dict]] = ContextVar("current_via", default=None)


@contextmanager
def acting_via(via: str, client_id: str, client_name: str) -> Iterator[None]:
    token = current_via.set({"via": via, "client_id": client_id, "client_name": client_name})
    try:
        yield
    finally:
        current_via.reset(token)


def apply_via(actor: Optional[str], changes: Optional[Dict[str, Any]]) -> Tuple[Optional[str], Optional[Dict[str, Any]]]:
    """Return (actor, changes) with the active via merged in; the caller's dict is never mutated."""
    v = current_via.get()
    if not v:
        return actor, changes
    return (actor or f"{v['via']}:{v['client_name']}"[:ACTOR_MAX],
            {**(changes or {}), "via": v["via"], "client_id": v["client_id"]})


def _tag_timeline_event(mapper, connection, target) -> None:
    v = current_via.get()
    if v and target.via is None:
        target.via, target.via_client = v["via"], v["client_name"][:VIA_CLIENT_MAX]


def _register() -> None:
    from app.models.case import TimelineEvent  # deferred: app.models.case imports this module at its end
    event.listen(TimelineEvent, "before_insert", _tag_timeline_event)


_register()
