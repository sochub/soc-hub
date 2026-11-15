from app.tasks.runner import _run_async
from app.worker import celery_app


@celery_app.task(acks_late=True)
def escalation_sweep_task():
    from app.escalations.service import sweep
    return _run_async(sweep())


async def _post_channel(row_id: int):
    from app.db.session import AsyncSessionLocal
    from app.notifications.delivery import post_escalation_channel
    async with AsyncSessionLocal() as db:
        return await post_escalation_channel(db, row_id)


@celery_app.task(acks_late=True)
def post_escalation_channel_task(row_id: int):
    return _run_async(_post_channel(row_id))
