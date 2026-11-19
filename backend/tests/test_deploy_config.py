"""Deployment/config knobs: access-log token redaction, S3 SSE selection, DB pool settings."""
import logging

import pytest

from app.core.log_redaction import TokenPathFilter, redact_path


@pytest.mark.parametrize("path,expected", [
    ("/api/v1/invitations/validate/abcDEF_123-xyz", "/api/v1/invitations/validate/[redacted]"),
    ("/api/v1/user-verification/tok3n?x=1", "/api/v1/user-verification/[redacted]?x=1"),
    ("/api/v1/invitations/", "/api/v1/invitations/"),
    ("/api/v1/cases/12", "/api/v1/cases/12"),
])
def test_redact_path(path, expected):
    assert redact_path(path) == expected


def test_access_log_filter_rewrites_uvicorn_args():
    rec = logging.LogRecord("uvicorn.access", logging.INFO, __file__, 1, '%s - "%s %s HTTP/%s" %d',
                            ("1.2.3.4:5", "GET", "/api/v1/user-verification/secret", "1.1", 200), None)
    assert TokenPathFilter().filter(rec) is True
    assert "secret" not in rec.getMessage()
    assert "/api/v1/user-verification/[redacted]" in rec.getMessage()


def test_access_log_filter_installed_by_app():
    import app.main  # noqa: F401
    assert any(isinstance(f, TokenPathFilter) for f in logging.getLogger("uvicorn.access").filters)


def test_s3_sse_defaults(monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "S3_SSE", None)
    monkeypatch.setattr(settings, "S3_ENDPOINT_URL", None)
    assert settings.s3_sse == "AES256"
    monkeypatch.setattr(settings, "S3_ENDPOINT_URL", "http://minio:9000")
    assert settings.s3_sse is None
    monkeypatch.setattr(settings, "S3_SSE", "AES256")
    assert settings.s3_sse == "AES256"
    monkeypatch.setattr(settings, "S3_SSE", "none")
    monkeypatch.setattr(settings, "S3_ENDPOINT_URL", None)
    assert settings.s3_sse is None


def test_s3_sse_blank_and_invalid():
    from app.core.config import Settings
    assert Settings(ENVIRONMENT="test", S3_SSE="").S3_SSE is None
    with pytest.raises(ValueError):
        Settings(ENVIRONMENT="test", S3_SSE="bogus")


def test_s3_storage_without_sse_sends_no_extra_args(monkeypatch):
    from app.storage.s3 import S3Storage
    monkeypatch.setattr("app.storage.s3.boto3.session.Session",
                        lambda: type("S", (), {"client": lambda *a, **k: object()})())
    assert S3Storage("b", sse=None).extra_args is None
    assert S3Storage("b", sse="aws:kms").extra_args == {"ServerSideEncryption": "aws:kms"}


def test_engine_pool_settings():
    from app.core.config import settings
    from app.db.session import engine
    assert engine.pool._pre_ping is True
    assert engine.pool.size() == settings.DB_POOL_SIZE
