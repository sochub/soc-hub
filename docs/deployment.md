# Deployment

Every merge to `main` and every `v*.*.*` tag publishes two multi-arch
(`linux/amd64`, `linux/arm64`) images to Docker Hub
(`.github/workflows/images.yml`):

| Image | Runs | Port |
|---|---|---|
| `sochub/sochub-backend` | API (default command), Celery worker, migrations, admin scripts | 8000 |
| `sochub/sochub-frontend` | nginx: the web UI, proxies `/api/v1` to `BACKEND_UPSTREAM` | 8080 |

Tags: `latest` (main), `1.4.0` / `1.4` (release tags), `sha-<short>` (every build).
Pin a release or `sha-` tag in production, not `latest`.

Both images run as non-root users (backend uid 10001, frontend uid 101) and
carry no secrets; all configuration comes from environment variables.

## What every deployment needs

| Piece | Notes |
|---|---|
| PostgreSQL 15+ | Container, RDS, Cloud SQL, ... → `DATABASE_URL=postgresql+asyncpg://...` |
| Redis 7 | With a password → `REDIS_URL=redis://:<password>@host:6379/0` |
| Backend env | `SECRET_KEY`, `DATABASE_URL`, `REDIS_URL`, `ENVIRONMENT=production`, `PUBLIC_BASE_URL`, `BACKEND_CORS_ORIGINS`, optional `AI_*` / `STORAGE_*` ([configuration.md](configuration.md)) |
| Frontend env | `BACKEND_UPSTREAM` (default `http://backend:8000`), `MAX_UPLOAD_MB` |
| Attachments | A persistent volume at `/data/attachments`, or `STORAGE_BACKEND=s3` (required where disks are ephemeral, e.g. Fargate) |
| TLS | Terminate at a proxy or load balancer in front of the frontend's port 8080 |

The backend image runs three ways; only the command changes:

| Role | Command | Replicas |
|---|---|---|
| API | _(image default)_ `uvicorn app.main:app --host 0.0.0.0 --port 8000` | 1+ |
| Worker + scheduler | `celery -A app.worker.celery_app worker -B --loglevel=info -s /tmp/celerybeat-schedule` | **exactly 1** (`-B` embeds the scheduler) |
| Migrations | `alembic upgrade head` | one-off, before API/worker start on each upgrade |

Create the first admin once the stack is up:
`python -m app.scripts.create_super_admin --email you@example.com` (same image, interactive).

Health checks: backend `GET /health` on 8000, frontend `GET /` on 8080.

## VPS / single server (Docker Compose)

```bash
curl -O https://raw.githubusercontent.com/sochub/soc-hub/main/docker-compose.prod.yml
curl -o .env https://raw.githubusercontent.com/sochub/soc-hub/main/.env.prod.example
# edit .env: replace every CHANGE_ME
docker compose -f docker-compose.prod.yml up -d
docker compose -f docker-compose.prod.yml run --rm backend python -m app.scripts.create_super_admin --email you@example.com
```

`migrate` runs on every `up` and exits; backend and worker wait for it.
Upgrade by changing `SOCHUB_TAG` and running `up -d` again. Put Caddy or
another TLS proxy in front of `HTTP_PORT`.

## AWS ECS Fargate

- **One task definition** with `frontend` and `backend` containers: containers
  in a task share `localhost`, so set `BACKEND_UPSTREAM=http://localhost:8000`.
  Expose 8080 through an Application Load Balancer (HTTPS listener).
- **Worker**: a second service from the backend image with the worker command,
  desired count 1.
- **Migrations**: a one-off task (`aws ecs run-task` with command
  `alembic upgrade head`) before rolling a new tag.
- **State**: RDS PostgreSQL and ElastiCache Redis (or Redis as its own small
  service). Fargate disks are ephemeral: use `STORAGE_BACKEND=s3`.
- **Secrets**: pass `SECRET_KEY`, DB and Redis passwords from Secrets Manager
  or SSM Parameter Store via the task definition's `secrets`.
- **AI**: `AI_PROVIDER=bedrock` uses the task role, with no keys in config
  ([configuration.md](configuration.md#ai-provider)).

## Kubernetes

- `Deployment` + `Service` named `backend` (port 8000) and `frontend`
  (port 8080); the frontend's default `BACKEND_UPSTREAM` already matches.
  Expose `frontend` through an Ingress with TLS.
- Worker: a `Deployment` with `replicas: 1` and the worker command.
- Migrations: a `Job` (or a Helm pre-upgrade hook) running `alembic upgrade head`.
- Attachments: a `PersistentVolumeClaim` at `/data/attachments`, or S3.
- Secrets: a `Secret` mounted with `envFrom`; images run fine with
  `runAsNonRoot: true` and `allowPrivilegeEscalation: false`.

## Publishing setup (maintainers)

The workflow needs two repository secrets: `DOCKERHUB_USERNAME` and
`DOCKERHUB_TOKEN` (a Docker Hub access token with read/write on the `sochub`
namespace). Run it manually from Actions → Images → Run workflow.
