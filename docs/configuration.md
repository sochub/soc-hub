# Configuration

Backend settings come from environment variables (see `backend/.env.example`).
Copy it to `backend/.env` and adjust.

## docker-compose (`.env` next to `docker-compose.yml`)

See `.env.example`. These are interpolated into `docker-compose.yml`; the
backend and worker get `DATABASE_URL` / `REDIS_URL` built from them.

| Variable | Description | Default |
|---|---|---|
| `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` | Postgres credentials (applied only when the volume is first initialised) | `user` / `password` / `sicms` |
| `REDIS_PASSWORD` | Redis `requirepass`; embedded in `REDIS_URL` | `devredispass` |
| `DEBUG` | Passed to backend + worker (see below) | `false` |

> **Production:** set `DEBUG=false` and strong, URL-safe `POSTGRES_PASSWORD` and
> `REDIS_PASSWORD`. Outside DEBUG the backend refuses to boot with a weak
> `SECRET_KEY`, a `REDIS_URL` without a password, or a `DATABASE_URL` using the
> password `password`.

## Core

| Variable | Description | Default |
|---|---|---|
| `DATABASE_URL` | PostgreSQL DSN (asyncpg) | `postgresql+asyncpg://user:password@db:5432/sicms` |
| `SECRET_KEY` | JWT signing key. **≥32 chars, not a placeholder** — the app refuses to boot with a weak key unless `DEBUG=true`. Generate: `python -c "import secrets; print(secrets.token_urlsafe(48))"` | _placeholder_ |
| `REDIS_URL` | Redis connection string. **Must include a password** unless `DEBUG=true` | `redis://:<REDIS_PASSWORD>@redis:6379/0` (compose) |
| `DEBUG` | SQL logging; also downgrades the `SECRET_KEY`, Redis-password and DB-password checks to warnings | `false` |
| `BACKEND_CORS_ORIGINS` | Comma-separated allowed origins. Empty = same-origin only | _(empty)_ |
| `PUBLIC_BASE_URL` | Externally visible origin (behind nginx). Used to build SAML SP URLs and post-SSO redirects | `http://localhost` |

## AI (Ollama)

| Variable | Description | Default |
|---|---|---|
| `OLLAMA_BASE_URL` | Ollama API endpoint | `http://localhost:11434` |
| `OLLAMA_MODEL` | Model name. The `ollama` container auto-pulls this on first boot | `llama3` |

> Model choice matters for the copilot's action reliability. `llama3.1` and
> `qwen2.5` have native tool-calling and emit structured actions more reliably than
> `llama3`. Set `OLLAMA_MODEL` and the container pulls it automatically.

## Email (optional — invitations work without it)

`SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM_EMAIL`. When
unset, invitation links are returned in the API/UI instead of emailed.

## Jira (optional)

`JIRA_URL`, `JIRA_USER`, `JIRA_API_TOKEN`.

## Security model

- **Passwords** — Argon2 hashed; policy enforced everywhere a password is set:
  ≥12 chars with upper, lower, and a digit; common passwords rejected.
- **Alert webhook** — `POST /api/v1/alerts/webhook` is authenticated **per tenant**.
  Each tenant has its own `webhook_api_key` (super-admin can view/rotate); the key
  alone determines the destination tenant, sent as the `X-API-Key` header.
- **Security headers** — CSP, `X-Frame-Options`, HSTS, etc. on every response.
- **Audit log** — every mutation is recorded with tenant, actor, and change set.
- **Per-tenant SSO** — see [sso/saml-design.md](sso/saml-design.md).

- **Network exposure** — only nginx (port 80) listens on all interfaces; db, redis,
  ollama and the backend are bound to `127.0.0.1`.

> The Postgres credentials (`user`/`password`) and `REDIS_PASSWORD=devredispass`
> are **local-development defaults**. Change them — and `SECRET_KEY` — before deploying.
