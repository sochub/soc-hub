import warnings

from typing import List, Literal, Optional
from urllib.parse import urlsplit

from pydantic_settings import BaseSettings
from pydantic import Field, field_validator, model_validator

# Placeholder values shipped in .env.example / docker-compose that must never
# reach a production deployment.
_WEAK_SECRET_KEYS = {
    "changeme",
    "your-secret-key-here-change-in-production",
}
_MIN_SECRET_KEY_LENGTH = 32
# The docker-compose local-dev Postgres password; never acceptable in production.
_WEAK_DB_PASSWORDS = {"password"}
# Public/placeholder Redis passwords (the compose dev default is "devredispass").
_WEAK_REDIS_PASSWORDS = {"devredispass", "changeme", "redis"}

class Settings(BaseSettings):
    DATABASE_URL: str = "postgresql+asyncpg://user:password@localhost:5432/sicms"
    SECRET_KEY: str = "changeme"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30

    # Security mode. The credential guard below is enforced ONLY in
    # "production" (the default, so a forgotten setting fails closed);
    # "development"/"test" downgrade it to warnings.
    ENVIRONMENT: Literal["production", "development", "test"] = "production"
    # Logging only — has no effect on security checks.
    DEBUG: bool = False
    # Log every SQL statement (SQLAlchemy echo).
    SQL_ECHO: bool = False

    # Login brute-force throttling (see app.core.login_throttle). Counts
    # attempts per fixed window. All must be >= 1 (0 refuses to boot).
    LOGIN_WINDOW_SECONDS: int = Field(900, ge=1)
    LOGIN_MAX_PER_EMAIL_IP: int = Field(5, ge=1)   # hard lock for one (email, client IP)
    LOGIN_MAX_PER_EMAIL: int = Field(30, ge=1)     # global cap per email (distributed guessing)
    LOGIN_MAX_PER_IP: int = Field(20, ge=1)        # per client IP, failures only

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

    # --- AI provider (deployment default; see docs/configuration.md) ---
    # Unset AI_PROVIDER keeps the legacy behaviour: Ollama at OLLAMA_BASE_URL/OLLAMA_MODEL.
    AI_PROVIDER: Optional[Literal["ollama", "openai", "openai_compatible", "anthropic",
                                  "gemini", "vertex", "bedrock"]] = None
    AI_MODEL: Optional[str] = None
    AI_API_BASE: Optional[str] = None
    AI_API_KEY: Optional[str] = None
    AI_AWS_REGION: Optional[str] = None
    VERTEX_PROJECT: Optional[str] = None
    VERTEX_LOCATION: Optional[str] = None
    AI_TIMEOUT_SECONDS: float = Field(120, gt=0)
    AI_ALLOW_TENANT_OVERRIDE: bool = True

    @field_validator("AI_PROVIDER", mode="before")
    @classmethod
    def _blank_provider_is_none(cls, v):
        return None if isinstance(v, str) and not v.strip() else v

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

        Enforced only when ENVIRONMENT == "production" (DEBUG is irrelevant).
        A predictable HS256 key lets anyone forge tokens for any user/role, an
        unauthenticated (or default-password) Redis lets anyone who reaches it
        inject Celery tasks, and the compose default DB password is public — so
        in production each is a hard failure. Outside production we only warn,
        to keep local development frictionless.
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
        redis_password = urlsplit(self.REDIS_URL).password
        if not redis_password:
            problems.append(
                "REDIS_URL has no password. Run Redis with --requirepass and use "
                "redis://:<password>@host:6379/0."
            )
        elif redis_password in _WEAK_REDIS_PASSWORDS:
            problems.append(
                "REDIS_URL uses a known default password. Set a strong REDIS_PASSWORD."
            )
        if urlsplit(self.DATABASE_URL).password in _WEAK_DB_PASSWORDS:
            problems.append(
                "DATABASE_URL uses the local-development default password. Set a "
                "strong POSTGRES_PASSWORD."
            )
        if problems and self.ENVIRONMENT == "production":
            raise ValueError(" ".join(problems))
        for message in problems:
            warnings.warn(message, stacklevel=2)
        return self

    class Config:
        env_file = ".env"

settings = Settings()
