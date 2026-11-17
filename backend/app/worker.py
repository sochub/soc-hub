from celery import Celery
from celery.schedules import crontab
from celery.signals import worker_init
from app.core.config import settings

import app.db.base  # noqa: F401 — register all models before any task queries the DB

celery_app = Celery("worker", broker=settings.REDIS_URL)


# The API process imports this module too (tasks import celery_app), so mark background mode only when a
# worker actually starts. worker_init fires before the prefork pool forks, so every child inherits it.
@worker_init.connect
def _mark_background(**_):
    from app.ai import llm
    llm.BACKGROUND = True  # LLM calls here take the shared background slot (see llm._shared)

# Time limits. Every tenant's automation, SLA sweeps, notifications and enrichment share this one worker
# (and beat runs embedded in it), so no task may hold a pool process indefinitely. At the soft limit Celery
# raises SoftTimeLimitExceeded inside the task (workflow steps catch it and fail with "step timed out");
# at the hard limit the pool process is killed and replaced. Tasks that legitimately run longer set their
# own `soft_time_limit`/`time_limit` (AI triage, threat-intel enrichment, the SLA sweep).
# A pool process that grows past worker_max_memory_per_child (KiB; checked after each task) is replaced, so a
# tenant's oversized template render or payload can't keep a worker bloated.
celery_app.conf.update(
    task_soft_time_limit=120,
    task_time_limit=150,
    worker_max_memory_per_child=512_000,
)

# Tenant-driven automation (workflow runs, IMAP polling) goes to its own "automation" queue, so one busy
# tenant can't delay SLA sweeps, notifications or enrichment on the default "celery" queue. Deployments must
# run a worker for each queue (or one worker with `-Q celery,automation`): a queue nobody consumes is
# published but never picked up. Beat runs as its own process (`celery -A app.worker beat`), not `-B`.
AUTOMATION_QUEUE = "automation"
# Exact task names win over the globs below: the global beat sweeps (cron schedules, ask-user expiry, the
# stuck-step reaper) stay on the default queue so a backlog of tenant runs can't delay them; they only
# create/mark rows and enqueue the real work on the automation queue.
celery_app.conf.task_routes = {
    "app.tasks.workflows.run_schedules_task": {"queue": "celery"},
    "app.tasks.workflows.expire_waits_task": {"queue": "celery"},
    "app.tasks.workflows.reap_stuck_steps_task": {"queue": "celery"},
    "app.tasks.workflows.*": {"queue": AUTOMATION_QUEUE},
    "app.tasks.email_intake.*": {"queue": AUTOMATION_QUEUE},
}

# The worker process only ever imports this module, so `@celery_app.task`
# never registers anything unless we import the task modules here too —
# without this, `.delay()` calls silently fail with "unregistered task".
import app.tasks.ticketing  # noqa: E402,F401
import app.tasks.portfolio  # noqa: E402,F401
import app.tasks.triage  # noqa: E402,F401
import app.tasks.sla  # noqa: E402,F401
import app.tasks.workflows  # noqa: E402,F401
import app.tasks.enrichment  # noqa: E402,F401
import app.tasks.notifications  # noqa: E402,F401
import app.tasks.guest_digest  # noqa: E402,F401
import app.tasks.slack  # noqa: E402,F401
import app.tasks.war_room  # noqa: E402,F401
import app.tasks.email_intake  # noqa: E402,F401
import app.tasks.escalations  # noqa: E402,F401

# Beat schedule: run exactly one beat process per deployment (see the queue note above).
# Beat's schedule file can live on tmpfs (read-only containers), so a restart forgets when an interval entry
# last ran and starts its countdown again: a 24 h interval would never fire if beat restarts daily. Anything
# longer than a few minutes is therefore a wall-clock crontab (UTC), which fires regardless of restarts.
celery_app.conf.beat_schedule = {
    "check-sla-breaches": {
        "task": "app.tasks.sla.check_sla_breaches_task",
        "schedule": 300.0,  # every 5 minutes
    },
    "expire-workflow-waits": {
        "task": "app.tasks.workflows.expire_waits_task",
        "schedule": 60.0,
    },
    "reap-stuck-workflow-steps": {
        "task": "app.tasks.workflows.reap_stuck_steps_task",
        "schedule": 300.0,
    },
    "escalation-sweep": {
        "task": "app.tasks.escalations.escalation_sweep_task",
        "schedule": 60.0,
    },
    "run-workflow-schedules": {
        "task": "app.tasks.workflows.run_schedules_task",
        "schedule": crontab(minute="*"),  # on the minute; the sweep also catches up missed minutes
    },
    "send-client-digests": {
        "task": "app.tasks.portfolio.send_client_digests_task",
        "schedule": crontab(minute=0),  # hourly
    },
    "sync-tickets": {
        "task": "app.tasks.ticketing.sync_tickets_task",
        "schedule": 300.0,
    },
    "poll-mailboxes": {
        "task": "app.tasks.email_intake.poll_mailboxes_task",
        "schedule": 60.0,
    },
    "cleanup-email-artifacts": {
        "task": "app.tasks.email_intake.cleanup_email_artifacts_task",
        "schedule": crontab(hour=3, minute=0),  # daily, 03:00 UTC
    },
    "prune-notifications": {
        "task": "app.tasks.notifications.prune_notifications_task",
        "schedule": crontab(hour=3, minute=15),  # daily, 03:15 UTC
    },
}
