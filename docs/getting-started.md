# Getting Started

## Prerequisites

- Docker + Docker Compose
- ~6 GB free disk for the local LLM model

## 1. Configure environment

```bash
cp backend/.env.example backend/.env
# Generate a strong SECRET_KEY (required outside DEBUG mode):
python -c "import secrets; print(secrets.token_urlsafe(48))"
# paste the result into backend/.env as SECRET_KEY=...
```

See [configuration.md](configuration.md) for every variable.

## 2. Start the stack

```bash
docker compose up -d --build
```

This starts six services:

| Service | Port | Purpose |
|---|---|---|
| `frontend` | 80 | React app (nginx) |
| `backend` | 8000 | FastAPI API |
| `db` | 5432 | PostgreSQL |
| `redis` | 6379 | Celery broker |
| `ollama` | 11434 | Local LLM — **auto-pulls** the model on first boot |
| `worker` | — | Celery worker |

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
