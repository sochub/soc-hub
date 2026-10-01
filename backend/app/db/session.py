from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from app.core.config import settings

engine = create_async_engine(settings.DATABASE_URL, echo=settings.SQL_ECHO)

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


async def get_db():
    async with AsyncSessionLocal() as session:
        yield session
