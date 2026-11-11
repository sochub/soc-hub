"""Old M1: tenant automation runs on its own queue and pool processes are recycled on memory growth."""
from app.worker import AUTOMATION_QUEUE, celery_app


def test_automation_tasks_route_to_their_own_queue():
    router = celery_app.amqp.router
    for name in ("app.tasks.workflows.advance_run_task", "app.tasks.workflows.loop_tick_task",
                 "app.tasks.email_intake.poll_mailboxes_task"):
        assert router.route({}, name)["queue"].name == AUTOMATION_QUEUE, name
    for name in ("app.tasks.sla.check_sla_breaches_task", "app.tasks.notifications.prune_notifications_task"):
        assert router.route({}, name)["queue"].name == "celery", name
    assert celery_app.conf.worker_max_memory_per_child
