import warnings

import pytest

from app.core.config import Settings

STRONG = "x" * 48
GOOD_DB = "postgresql+asyncpg://soc:S3cure-pw@db:5432/sicms"
GOOD_REDIS = "redis://:S3cure-redis@redis:6379/0"
DEV_DB = "postgresql+asyncpg://user:password@db:5432/sicms"


def _settings(**kw):
    base = dict(SECRET_KEY=STRONG, DATABASE_URL=GOOD_DB, REDIS_URL=GOOD_REDIS, ENVIRONMENT="production")
    base.update(kw)
    return Settings(_env_file=None, **base)


def test_environment_defaults_to_production():
    assert Settings.model_fields["ENVIRONMENT"].default == "production"


def test_production_accepts_strong_credentials():
    _settings()


@pytest.mark.parametrize("url", ["redis://redis:6379/0", "redis://localhost:6379", "redis://:@redis:6379/0"])
def test_production_rejects_redis_without_password(url):
    with pytest.raises(ValueError, match="REDIS_URL"):
        _settings(REDIS_URL=url)


@pytest.mark.parametrize("pw", ["devredispass", "changeme", "redis"])
def test_production_rejects_weak_redis_password(pw):
    with pytest.raises(ValueError, match="REDIS_URL"):
        _settings(REDIS_URL=f"redis://:{pw}@redis:6379/0")


def test_production_rejects_default_db_password():
    with pytest.raises(ValueError, match="DATABASE_URL"):
        _settings(DATABASE_URL=DEV_DB)


def test_debug_does_not_relax_production_guard():
    with pytest.raises(ValueError, match="DATABASE_URL"):
        _settings(DEBUG=True, DATABASE_URL=DEV_DB)


@pytest.mark.parametrize("env", ["development", "test"])
def test_non_production_only_warns(env):
    with pytest.warns(UserWarning):
        _settings(ENVIRONMENT=env, SECRET_KEY="changeme", REDIS_URL="redis://:devredispass@redis:6379/0",
                  DATABASE_URL=DEV_DB)


def test_sql_echo_is_separate_from_debug():
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        s = _settings(ENVIRONMENT="development", DEBUG=True)
    assert s.SQL_ECHO is False


@pytest.mark.parametrize("name", ["LOGIN_MAX_PER_EMAIL_IP", "LOGIN_MAX_PER_EMAIL", "LOGIN_MAX_PER_IP",
                                  "LOGIN_WINDOW_SECONDS"])
def test_login_throttle_settings_must_be_at_least_one(name):
    with pytest.raises(ValueError, match=name):
        _settings(**{name: 0})
