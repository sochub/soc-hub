import warnings

from typing import List
from urllib.parse import urlsplit

from pydantic_settings import BaseSettings
from pydantic import model_validator

# Placeholder values shipped in .env.example / docker-compose that must never
# reach a production deployment.
_WEAK_SECRET_KEYS = {
    "changeme",
    "your-secret-key-here-change-in-production",
}
_MIN_SECRET_KEY_LENGTH = 32
# The docker-compose local-dev Postgres password; never acceptable in production.
_WEAK_DB_PASSWORDS = {"password"}

class Settings(BaseSettings):
    DATABASE_URL: str = "postgresql+asyncpg://user:password@localhost:5432/sicms"
    SECRET_KEY: str = "changeme"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    DEBUG: bool = False

    REDIS_URL: str = "redis://localhost:6379"

    # CORS: comma-separated list of allowed origins. Empty = no cross-origin
    # access (frontend is served same-origin behind nginx). Stored as a raw
    # string and parsed via `cors_origins` to avoid pydantic-settings' JSON
    # decoding of complex-typed env vars.
    BACKEND_CORS_ORIGINS: str = ""

    # Note: webhook auth is per-webhook (Webhook.api_key, one or more per
    # tenant), not a single global key — see app.api.deps.get_webhook_from_key.

    # Jira Integration
    JIRA_URL: str | None = None
    JIRA_USER: str | None = None
    JIRA_API_TOKEN: str | None = None

    # Ollama Configuration
    OLLAMA_BASE_URL: str = "http://localhost:11434"
    OLLAMA_MODEL: str = "llama3"

    # SMTP (optional — invitations work without it)
    SMTP_HOST: str | None = None
    SMTP_PORT: int = 587
    SMTP_USER: str | None = None
    SMTP_PASSWORD: str | None = None
    SMTP_FROM_EMAIL: str | None = None

    # Invitation settings
    INVITATION_EXPIRE_HOURS: int = 48  # 2 days

    # Frontend URL for invitation links
    FRONTEND_URL: str = "http://localhost:80"

    # Externally visible origin (behind nginx) used to build SAML SP URLs
    # (entity id, ACS, metadata) and post-SSO redirects.
    PUBLIC_BASE_URL: str = "http://localhost"

    @property
    def cors_origins(self) -> List[str]:
        """Parsed list of allowed CORS origins."""
        return [o.strip() for o in self.BACKEND_CORS_ORIGINS.split(",") if o.strip()]

    @model_validator(mode="after")
    def validate_secret_key(self) -> "Settings":
        """Refuse to boot with weak secrets in production.

        "Production" means DEBUG is off. A predictable HS256 key lets anyone
        forge tokens for any user/role, an unauthenticated Redis lets anyone
        who reaches it inject Celery tasks, and the compose default DB password
        is public — so outside DEBUG mode each is a hard failure. In DEBUG we
        only warn, to keep local development frictionless.
        """
        problems = []
        if (
            self.SECRET_KEY in _WEAK_SECRET_KEYS
            or len(self.SECRET_KEY) < _MIN_SECRET_KEY_LENGTH
        ):
            problems.append(
                "SECRET_KEY is weak: it is a known placeholder or shorter than "
                f"{_MIN_SECRET_KEY_LENGTH} characters. Generate a strong key, e.g. "
                "`python -c \"import secrets; print(secrets.token_urlsafe(48))\"`."
            )
        if not urlsplit(self.REDIS_URL).password:
            problems.append(
                "REDIS_URL has no password. Run Redis with --requirepass and use "
                "redis://:<password>@host:6379/0."
            )
        if urlsplit(self.DATABASE_URL).password in _WEAK_DB_PASSWORDS:
            problems.append(
                "DATABASE_URL uses the local-development default password. Set a "
                "strong POSTGRES_PASSWORD."
            )
        for message in problems:
            if self.DEBUG:
                warnings.warn(message, stacklevel=2)
        if problems and not self.DEBUG:
            raise ValueError(" ".join(problems))
        return self

    class Config:
        env_file = ".env"

settings = Settings()
