from app.tasks.runner import _run_async
from app.worker import celery_app


@celery_app.task(acks_late=True, soft_time_limit=1500, time_limit=1800)
def purge_alerts_task():
    from app.services.alert_retention import run_all
    _run_async(run_all())
