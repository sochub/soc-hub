from app.tasks.runner import _run_async
from app.worker import celery_app


# A cycle can fetch 50 emails with attachments, more than the default 120 s soft limit allows.
@celery_app.task(acks_late=True, soft_time_limit=540, time_limit=600)
def poll_mailboxes_task():
    from app.services.email_intake import poll_due_mailboxes
    _run_async(poll_due_mailboxes())


@celery_app.task(acks_late=True)
def cleanup_email_artifacts_task():
    from app.services.email_intake import cleanup_email_artifacts
    _run_async(cleanup_email_artifacts())
