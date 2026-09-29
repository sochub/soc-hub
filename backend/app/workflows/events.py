"""Workflow event emission + trigger matching."""
import logging
from typing import Optional

from app.workflows.templating import eval_expr, TemplateError

logger = logging.getLogger(__name__)

MAX_DEPTH = 3


def should_emit(depth: int) -> bool:
    return depth <= MAX_DEPTH


def trigger_matches(trigger_filter: Optional[str], ctx: dict) -> bool:
    if not trigger_filter or not trigger_filter.strip():
        return True
    try:
        return bool(eval_expr(trigger_filter, ctx))
    except TemplateError as e:
        logger.warning("workflow trigger_filter error (treated as no match): %s", e)
        return False


def emit_event(tenant_id: int, event_type: str, *, case_id: Optional[int] = None, alert_id: Optional[int] = None,
               changes: Optional[dict] = None, depth: int = 0) -> None:
    """Call only AFTER the originating change is committed."""
    if not should_emit(depth):
        logger.warning("dropping %s event for tenant %s at depth %s (loop guard)", event_type, tenant_id, depth)
        return
    from app.tasks.workflows import dispatch_event_task  # late import: avoids worker import cycle
    dispatch_event_task.delay(tenant_id, event_type, case_id, alert_id, changes or {}, depth)
