"""Old M1: tenant automation runs on its own queue and pool processes are recycled on memory growth."""
from app.worker import AUTOMATION_QUEUE, celery_app


def test_automation_tasks_route_to_their_own_queue():
    router = celery_app.amqp.router
    for name in ("app.tasks.workflows.advance_run_task", "app.tasks.workflows.loop_tick_task",
                 "app.tasks.email_intake.poll_mailboxes_task"):
        assert router.route({}, name)["queue"].name == AUTOMATION_QUEUE, name
    for name in ("app.tasks.sla.check_sla_breaches_task", "app.tasks.notifications.prune_notifications_task",
                 # global beat sweeps: a tenant backlog on the automation queue must not delay them
                 "app.tasks.workflows.run_schedules_task", "app.tasks.workflows.expire_waits_task",
                 "app.tasks.workflows.reap_stuck_steps_task"):
        assert router.route({}, name)["queue"].name == "celery", name
    assert celery_app.conf.worker_max_memory_per_child


def test_long_beat_entries_are_wall_clock_crontabs():
    """A beat restart (schedule file on tmpfs) resets interval countdowns; daily/hourly entries must be crontabs."""
    from celery.schedules import crontab
    for key, entry in celery_app.conf.beat_schedule.items():
        sched = entry["schedule"]
        if not isinstance(sched, crontab):
            assert float(sched) <= 300, key
    sched = celery_app.conf.beat_schedule
    assert sched["run-workflow-schedules"]["schedule"] == crontab(minute="*")
    assert sched["prune-notifications"]["schedule"] == crontab(hour=3, minute=15)
    assert sched["send-client-digests"]["schedule"] == crontab(minute=0)
