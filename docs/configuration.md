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
| `ENVIRONMENT` | Passed to backend + worker (see below). Unset → `production` | `production` (`.env.example`: `development`) |

> **Production:** use `ENVIRONMENT=production` and strong, URL-safe
> `POSTGRES_PASSWORD` and `REDIS_PASSWORD`. In production the backend refuses to
> boot with a weak `SECRET_KEY`, a `REDIS_URL` without a password or with a known
> default one (`devredispass`, `changeme`, `redis`), or a `DATABASE_URL` using the
> password `password`. `development`/`test` only log warnings for these.

## Core

| Variable | Description | Default |
|---|---|---|
| `DATABASE_URL` | PostgreSQL DSN (asyncpg) | `postgresql+asyncpg://user:password@db:5432/sicms` |
| `SECRET_KEY` | JWT signing key. **≥32 chars, not a placeholder** — in production the app refuses to boot with a weak key. Generate: `python -c "import secrets; print(secrets.token_urlsafe(48))"` | _placeholder_ |
| `REDIS_URL` | Redis connection string. In production **must include a strong (non-default) password** | `redis://:<REDIS_PASSWORD>@redis:6379/0` (compose) |
| `ENVIRONMENT` | Security mode: `production` \| `development` \| `test`. The credential checks above are hard failures **only** in `production`; otherwise they warn | `production` |
| `DEBUG` | Logging only — no effect on security checks | `false` |
| `SQL_ECHO` | Log every SQL statement (SQLAlchemy echo) | `false` |
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
- **Login throttling** — see below.
- **Emails** — stored trimmed and lower-cased; `users` has a UNIQUE index on
  `lower(email)`, so login, invitations and SSO are case-insensitive.

## Login throttling

Every login attempt makes a **conditional, atomic reservation** in Redis (one Lua
script) **before** the password is checked: if every counter is below its limit,
all three are incremented; if any is already at its limit, **nothing** is
incremented and the request gets `429` with `Retry-After` (seconds left in that
counter's window). Rejected attempts therefore consume no budget, and parallel
bursts can't race past the limits. Counters are fixed windows of
`LOGIN_WINDOW_SECONDS`.

| Variable | Counts attempts per… | Default |
|---|---|---|
| `LOGIN_MAX_PER_EMAIL_IP` | (email, client IP) — the hard lock. Keyed on the IP so one attacker IP can't lock the victim out; only the global cap below can, and that needs guesses from many IPs (≥ 6 with the defaults) | `5` |
| `LOGIN_MAX_PER_EMAIL` | email, any IP — looser cap on distributed guessing. Only allowed attempts count, so a single IP can add at most `LOGIN_MAX_PER_EMAIL_IP` per window; reaching the cap takes many IPs | `30` |
| `LOGIN_MAX_PER_IP` | client IP, any email — failures only (a successful login refunds its slot) | `20` |
| `LOGIN_WINDOW_SECONDS` | window length | `900` |

All four must be **≥ 1**; a `0` (or negative) value refuses to boot with a
validation error naming the setting.

**Redis requirements.** The throttle runs Lua scripts that use `EXPIRE … NX`,
so it needs **Redis ≥ 7.0** (compose ships `redis:7`). It assumes a **single
Redis node**: the three counter keys aren't hash-tagged, so Redis Cluster is not
supported. A blocking counter that somehow lost its TTL is given one on the next
attempt, so it can't stay locked forever.

A successful login clears that email's two counters and atomically refunds its
per-IP slot (never below 0), so NATed users aren't throttled by their own successful logins.
If Redis is unreachable the check fails **open** (a warning is logged). Unknown emails cost the same
Argon2 work as wrong passwords.

**Client IP.** The backend uses nginx's `X-Real-IP` (nginx sets it, and
overwrites `X-Forwarded-For`, from `$remote_addr`); client-supplied forwarding
headers are ignored. This is safe because the backend port is bound to
`127.0.0.1`. If nginx itself sits behind a load balancer or another proxy,
`$remote_addr` is that proxy, so all users share one IP bucket — configure
nginx's realip module to recover the client address from the trusted hop:

```nginx
set_real_ip_from 10.0.0.0/8;      # your LB / proxy addresses only
real_ip_header   X-Forwarded-For;
real_ip_recursive on;
```

> **Docker Desktop (macOS/Windows):** published ports are NATed, so nginx sees
> every client as the Docker gateway (e.g. `172.18.0.1`) and all local clients
> share one per-IP bucket. On a Linux host the real client IP is preserved.

> The Postgres credentials (`user`/`password`) and `REDIS_PASSWORD=devredispass`
> are **local-development defaults**; production (`ENVIRONMENT=production`)
> refuses them. Change them — and `SECRET_KEY` — before deploying.
