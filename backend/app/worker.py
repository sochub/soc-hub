from celery import Celery
from app.core.config import settings

import app.db.base  # noqa: F401 — register all models before any task queries the DB

celery_app = Celery("worker", broker=settings.REDIS_URL)

# Time limits. Every tenant's automation, SLA sweeps, notifications and enrichment share this one worker
# (and beat runs embedded in it), so no task may hold a pool process indefinitely. At the soft limit Celery
# raises SoftTimeLimitExceeded inside the task (workflow steps catch it and fail with "step timed out");
# at the hard limit the pool process is killed and replaced. Tasks that legitimately run longer set their
# own `soft_time_limit`/`time_limit` (AI triage, threat-intel enrichment, the SLA sweep).
# Follow-up (not done here): split automation onto its own queue/worker and run beat separately, so one
# busy tenant can't delay SLA sweeps at all.
celery_app.conf.update(
    task_soft_time_limit=120,
    task_time_limit=150,
)

# The worker (docker-compose `worker` service) is started with no `-Q` flag,
# so it only ever consumes the default "celery" queue — routing tasks
# elsewhere (as a prior "main-queue" route did) means they're published but
# never picked up. No multi-queue setup exists, so everything stays default.

# The worker process only ever imports this module, so `@celery_app.task`
# never registers anything unless we import the task modules here too —
# without this, `.delay()` calls silently fail with "unregistered task".
import app.tasks.jira  # noqa: E402,F401
import app.tasks.triage  # noqa: E402,F401
import app.tasks.sla  # noqa: E402,F401
import app.tasks.workflows  # noqa: E402,F401
import app.tasks.enrichment  # noqa: E402,F401
import app.tasks.notifications  # noqa: E402,F401
import app.tasks.guest_digest  # noqa: E402,F401
import app.tasks.slack  # noqa: E402,F401
import app.tasks.war_room  # noqa: E402,F401

# Beat is embedded in the worker process via the `-B` flag on the
# docker-compose `worker` service — there's only ever one worker replica, so
# a separate beat service isn't needed. If that changes, split beat out.
celery_app.conf.beat_schedule = {
    "check-sla-breaches": {
        "task": "app.tasks.sla.check_sla_breaches_task",
        "schedule": 300.0,  # every 5 minutes
    },
    "expire-workflow-waits": {
        "task": "app.tasks.workflows.expire_waits_task",
        "schedule": 60.0,
    },
    "run-workflow-schedules": {
        "task": "app.tasks.workflows.run_schedules_task",
        "schedule": 60.0,
    },
    "prune-notifications": {
        "task": "app.tasks.notifications.prune_notifications_task",
        "schedule": 86400.0,
    },
}
