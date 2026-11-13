from app.tasks.runner import _run_async
from app.worker import celery_app


@celery_app.task(acks_late=True)
def escalation_sweep_task():
    from app.escalations.service import sweep
    return _run_async(sweep())
