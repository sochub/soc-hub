import pytest

from app.core.config import Settings

STRONG = "x" * 48
GOOD_DB = "postgresql+asyncpg://soc:S3cure-pw@db:5432/sicms"
GOOD_REDIS = "redis://:S3cure-redis@redis:6379/0"


def _settings(**kw):
    base = dict(SECRET_KEY=STRONG, DATABASE_URL=GOOD_DB, REDIS_URL=GOOD_REDIS, DEBUG=False)
    base.update(kw)
    return Settings(_env_file=None, **base)


def test_production_accepts_strong_credentials():
    _settings()


@pytest.mark.parametrize("url", ["redis://redis:6379/0", "redis://localhost:6379", "redis://:@redis:6379/0"])
def test_production_rejects_redis_without_password(url):
    with pytest.raises(ValueError, match="REDIS_URL"):
        _settings(REDIS_URL=url)


def test_production_rejects_default_db_password():
    with pytest.raises(ValueError, match="DATABASE_URL"):
        _settings(DATABASE_URL="postgresql+asyncpg://user:password@db:5432/sicms")


def test_debug_only_warns():
    with pytest.warns(UserWarning):
        _settings(DEBUG=True, REDIS_URL="redis://redis:6379/0",
                  DATABASE_URL="postgresql+asyncpg://user:password@db:5432/sicms")
