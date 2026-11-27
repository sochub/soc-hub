import logging

from app.ai.errors import AISlotBusy
from app.tasks.runner import _run_async
from app.worker import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(acks_late=True, soft_time_limit=600, time_limit=660)
def fetch_briefs_task():
    """Beat, every 30 minutes: fetch due feeds, prune old items, then queue the AI briefing."""
    from app.services.briefs import run_fetch
    _run_async(run_fetch())
    brief_items_task.delay()


# Each LLM call may take AI_TIMEOUT_SECONDS; run_briefing stops starting new ones after RUN_BUDGET_SECONDS.
@celery_app.task(acks_late=True, soft_time_limit=1500, time_limit=1800)
def brief_items_task():
    from app.services.briefs import run_briefing
    try:
        _run_async(run_briefing())
    except AISlotBusy:
        # The shared background Ollama slot is taken (see llm._shared). Unlike triage, no retry is scheduled:
        # the next beat run, 30 minutes later, picks up exactly where this one stopped.
        logger.info("briefs: background AI slot busy, continuing on the next run")
