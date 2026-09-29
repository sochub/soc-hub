import asyncio
import logging

from app.worker import celery_app

logger = logging.getLogger(__name__)


def _run_async(coro):
    """Run a coroutine in a fresh loop, then dispose pooled connections bound to it
    (same reason as app/tasks/triage.py)."""
    from app.db.session import engine

    async def wrapper():
        try:
            return await coro
        finally:
            await engine.dispose()
    return asyncio.run(wrapper())


@celery_app.task(acks_late=True)
def dispatch_event_task(tenant_id, event_type, case_id, alert_id, changes, depth):
    from app.workflows.runtime import dispatch_event
    _run_async(dispatch_event(tenant_id, event_type, case_id, alert_id, changes, depth))


@celery_app.task(acks_late=True)
def advance_run_task(run_id):
    from app.workflows.runtime import advance_run
    _run_async(advance_run(run_id))


@celery_app.task(acks_late=True)
def execute_step_task(step_id):
    from app.workflows.runtime import execute_step
    _run_async(execute_step(step_id))


@celery_app.task(acks_late=True)
def loop_tick_task(parent_step_id):
    from app.workflows.runtime import loop_tick
    _run_async(loop_tick(parent_step_id))
