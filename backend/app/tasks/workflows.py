import logging

from celery.exceptions import SoftTimeLimitExceeded

from app.tasks.runner import _run_async
from app.worker import celery_app

logger = logging.getLogger(__name__)


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
    from app.workflows import runtime
    try:
        _run_async(runtime.execute_step(step_id))
    except SoftTimeLimitExceeded:
        # The soft limit landed outside the step's own handler (e.g. in the event loop). Mark the step
        # failed and advance the run; there are task_time_limit - task_soft_time_limit seconds left.
        logger.warning("workflow step %s timed out outside its handler", step_id)
        _run_async(runtime.fail_timed_out_step(step_id))


@celery_app.task(acks_late=True)
def loop_tick_task(parent_step_id):
    from app.workflows.runtime import loop_tick
    _run_async(loop_tick(parent_step_id))


@celery_app.task(acks_late=True)
def handle_slack_interaction_task(tenant_id, payload):
    from app.services.slack_actions import handle_interaction
    _run_async(handle_interaction(tenant_id, payload))


@celery_app.task(acks_late=True)
def expire_waits_task():
    from app.workflows.runtime import expire_waits
    _run_async(expire_waits())


@celery_app.task(acks_late=True)
def reap_stuck_steps_task():
    from app.workflows.runtime import reap_stuck_steps
    _run_async(reap_stuck_steps())


@celery_app.task(acks_late=True)
def withdraw_messages_task(targets):
    from app.workflows.runtime import withdraw_messages
    _run_async(withdraw_messages(targets))


@celery_app.task(acks_late=True)
def run_schedules_task():
    from datetime import datetime, timezone
    from app.workflows.runtime import run_due_schedules
    _run_async(run_due_schedules(datetime.now(timezone.utc)))
