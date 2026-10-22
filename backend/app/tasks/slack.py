"""Slack bot background work: responder alerts / live cards and App Home."""
from app.tasks.workflows import _run_async
from app.worker import celery_app


@celery_app.task(acks_late=True)
def slack_case_event_task(tenant_id, event_type, case_id, changes):
    from app.services.slack_bot import on_case_event
    _run_async(on_case_event(tenant_id, event_type, case_id, changes or {}))


@celery_app.task(acks_late=True)
def slack_home_task(tenant_id, slack_user_id):
    from app.services.slack_bot import publish_home
    _run_async(publish_home(tenant_id, slack_user_id))
