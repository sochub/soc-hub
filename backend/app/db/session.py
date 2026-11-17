from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from app.core.config import settings

# pool_pre_ping: replace connections the server or a proxy dropped (DB restart, idle timeout) instead of
# failing the next request. Celery tasks dispose this engine after each run (app/tasks/runner.py).
engine = create_async_engine(
    settings.DATABASE_URL,
    echo=settings.SQL_ECHO,
    pool_size=settings.DB_POOL_SIZE,
    max_overflow=settings.DB_MAX_OVERFLOW,
    pool_pre_ping=True,
)

AsyncSessionLocal = sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)

# Every process (API, Celery worker, scripts) builds sessions from here, so the IOC/artifact
# auto-enrichment listeners register here too. hooks.py imports its models lazily: no cycle.
import app.enrichment.hooks as _enrichment_hooks  # noqa: E402,F401

# After-commit external delivery of notifications (stash is written by app/notifications/service.py).
from app.notifications.delivery import register_after_commit_hook  # noqa: E402

register_after_commit_hook()

# After-commit case.escalated / case.escalation_closed workflow events (stash: app/escalations/service.py).
from app.escalations.events import register_after_commit_hook as register_escalation_hook  # noqa: E402

register_escalation_hook()

from app.services.war_room_events import register_war_room_hook  # noqa: E402

register_war_room_hook()

from app.guests.notify import register_guest_digest_hook  # noqa: E402

register_guest_digest_hook()


async def get_db():
    async with AsyncSessionLocal() as session:
        yield session
