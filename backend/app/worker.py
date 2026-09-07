from celery import Celery
from app.core.config import settings

import app.db.base  # noqa: F401 — register all models before any task queries the DB

celery_app = Celery("worker", broker=settings.REDIS_URL)

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

# Beat is embedded in the worker process via the `-B` flag on the
# docker-compose `worker` service — there's only ever one worker replica, so
# a separate beat service isn't needed. If that changes, split beat out.
celery_app.conf.beat_schedule = {
    "check-sla-breaches": {
        "task": "app.tasks.sla.check_sla_breaches_task",
        "schedule": 300.0,  # every 5 minutes
    },
}
