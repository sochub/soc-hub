# Getting Started

## Prerequisites

- Docker + Docker Compose
- ~6 GB free disk for the local LLM model

## 1. Configure environment

```bash
# docker-compose variables (Postgres/Redis credentials, ENVIRONMENT)
cp .env.example .env
# backend settings
cp backend/.env.example backend/.env
# Generate a strong SECRET_KEY (required when ENVIRONMENT=production):
python -c "import secrets; print(secrets.token_urlsafe(48))"
# paste the result into backend/.env as SECRET_KEY=...
```

The root `.env.example` ships local-development values: `ENVIRONMENT=development`,
the default Postgres password and `REDIS_PASSWORD=devredispass`. In
`development` the backend only **warns** about these weak credentials. For any
shared or production deployment set `ENVIRONMENT=production` (also the default
when unset) with a strong `SECRET_KEY`, `POSTGRES_PASSWORD` and `REDIS_PASSWORD`;
in production the backend refuses to boot with any of the dev defaults.

See [configuration.md](configuration.md) for every variable.

## 2. Start the stack

```bash
docker compose up -d --build
```

This starts six services:

| Service | Port | Purpose |
|---|---|---|
| `frontend` | 80 (all interfaces) | React app (nginx) — the only public entry point |
| `backend` | 127.0.0.1:8000 | FastAPI API |
| `db` | 127.0.0.1:5432 | PostgreSQL |
| `redis` | 127.0.0.1:6379 | Celery broker + login rate limiting (password-protected) |
| `ollama` | 127.0.0.1:11434 | Local LLM — **auto-pulls** the model on first boot |
| `worker` | — | Celery worker |

Everything except nginx is published on loopback only, so local tools (psql,
redis-cli, an IDE) work but other machines on the network cannot reach the
database, Redis, the LLM or the API directly.

The Ollama container pulls `OLLAMA_MODEL` (default `llama3`, ~4.7 GB) automatically
on first start and caches it in the `ollama_data` volume. Watch progress with
`docker compose logs -f ollama`; until it finishes, AI features return an
"unavailable" message but the rest of the app works.

## 3. Run migrations

```bash
docker compose exec backend alembic upgrade head
```

## 4. Create the first super-admin

There is no default user. The script prompts securely for a password (omit
`--password` to be prompted — recommended):

```bash
docker compose exec backend python -m app.scripts.create_super_admin \
  --email admin@example.com --name "Super Admin"
```

Passwords must be ≥12 chars with upper, lower, and a digit. Re-running with an
existing email upgrades that user to super-admin instead of duplicating.

## 5. Sign in

Open **http://localhost** and log in. A super-admin can create tenants
(**Tenants**), invite users (**Users**), and switch the active tenant from the
sidebar picker.

## 6. (Optional) Seed demo data

Populate a tenant with ~40 backdated incidents (plus artifacts, IOCs, timeline
events) so the dashboard and graph have something to show:

```bash
# seed (tenant id 1 = the default tenant a fresh super-admin lands in)
docker compose exec backend python -m app.scripts.seed_incidents --tenant-id 1 --count 40 --days 30

# load the global playbook marketplace catalog
docker compose exec backend python -m app.scripts.seed_playbooks

# remove seeded incidents later (only touches rows tagged "seed")
docker compose exec backend python -m app.scripts.seed_incidents --tenant-id 1 --purge
```

## Operational notes

- **Apply backend code changes:** the API runs with `--reload`, but on macOS the
  bind-mount may not deliver file events — `docker compose restart backend` after
  backend edits to be safe.
- **Frontend changes:** the frontend is a built nginx image —
  `docker compose up -d --build frontend` to redeploy.
- **Fresh database:** migrations are designed to apply cleanly in one
  `alembic upgrade head` (per-migration transactions handle Postgres enum ordering).
