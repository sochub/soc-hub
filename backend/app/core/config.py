import warnings

from typing import List

from pydantic_settings import BaseSettings
from pydantic import model_validator

# Placeholder values shipped in .env.example / docker-compose that must never
# reach a production deployment.
_WEAK_SECRET_KEYS = {
    "changeme",
    "your-secret-key-here-change-in-production",
}
_MIN_SECRET_KEY_LENGTH = 32

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
        """Refuse to boot with a weak SECRET_KEY in production.

        A predictable HS256 key lets anyone forge tokens for any user/role, so
        outside DEBUG mode this is a hard failure rather than a warning. In
        DEBUG we only warn, to keep local development frictionless.
        """
        is_weak = (
            self.SECRET_KEY in _WEAK_SECRET_KEYS
            or len(self.SECRET_KEY) < _MIN_SECRET_KEY_LENGTH
        )
        if is_weak:
            message = (
                "SECRET_KEY is weak: it is a known placeholder or shorter than "
                f"{_MIN_SECRET_KEY_LENGTH} characters. Generate a strong key, e.g. "
                "`python -c \"import secrets; print(secrets.token_urlsafe(48))\"`."
            )
            if self.DEBUG:
                warnings.warn(message, stacklevel=2)
            else:
                raise ValueError(message)
        return self

    class Config:
        env_file = ".env"

settings = Settings()
