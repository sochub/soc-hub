# Deployment

Publishing a GitHub release (tag `vX.Y` or `vX.Y.Z`) builds two multi-arch
(`linux/amd64`, `linux/arm64`) images and pushes them to Docker Hub
(`.github/workflows/images.yml`). Nothing else publishes: pushes to `main` and pull
requests (including from forks) never do.

| Image | Runs | Port |
|---|---|---|
| `sochub/sochub-backend` | API (default command), Celery worker, migrations, admin scripts | 8000 |
| `sochub/sochub-frontend` | nginx: the web UI, proxies `/api/v1` to `BACKEND_UPSTREAM` | 8080 |

Tags: `7.4` from release `v7.4`; `7.4.1` and `7.4` from release `v7.4.1`; plus `sha-<short>`, and `latest`
for the newest non-pre-release. A release whose tag is neither form fails instead of publishing only
`sha-<short>`. Pin a version in production, not `latest`; the Helm chart defaults to its `appVersion`.

Both images run as non-root users (backend uid 10001, frontend uid 101) and
carry no secrets; all configuration comes from environment variables.

## What every deployment needs

| Piece | Notes |
|---|---|
| PostgreSQL 15+ | Container, RDS, Cloud SQL, ... → `DATABASE_URL=postgresql+asyncpg://...` |
| Redis 7 | With a password → `REDIS_URL=redis://:<password>@host:6379/0` |
| Backend env | `SECRET_KEY`, `ENCRYPTION_KEY` (≥32 chars, different from `SECRET_KEY`), `DATABASE_URL`, `REDIS_URL`, `ENVIRONMENT=production`, `PUBLIC_BASE_URL` and `FRONTEND_URL` (public `https://` URLs), `BACKEND_CORS_ORIGINS`, optional `AI_*` / `STORAGE_*` / `DB_POOL_*` ([configuration.md](configuration.md)). Production refuses to start without these |
| Frontend env | `BACKEND_UPSTREAM` (default `http://backend:8000`), `MAX_UPLOAD_MB`, `HSTS_HEADER` (value only, e.g. `max-age=31536000; includeSubDomains`), `REAL_IP_FROM` (CIDRs of the proxy in front of nginx) |
| Attachments | A persistent volume at `/data/attachments`, or `STORAGE_BACKEND=s3` (required where disks are ephemeral, e.g. Fargate) |
| TLS | Terminate at a proxy or load balancer in front of the frontend's port 8080 |

The backend image runs three ways; only the command changes:

| Role | Command | Replicas |
|---|---|---|
| API | _(image default)_ `uvicorn app.main:app --host 0.0.0.0 --port 8000` | 1+ |
| Worker | `celery -A app.worker.celery_app worker -Q celery,automation --loglevel=info` | 1+ |
| Scheduler (beat) | `celery -A app.worker.celery_app beat --loglevel=info -s /tmp/celerybeat-schedule` | **exactly 1** |
| Migrations | `alembic upgrade head` | one-off, before API/worker start on each upgrade |
| Slack Socket Mode | `python -m app.slack_socket` | 1 (a 2nd for redundancy is fine); idles if no tenant uses Socket Mode |

Create the first admin once the stack is up:
`python -m app.scripts.create_super_admin --email you@example.com` (same image, interactive).

Health checks: backend `GET /health` on 8000, frontend `GET /` on 8080.

Both images run with a read-only root filesystem if `/tmp` is writable (backend: also `/home/app`;
frontend: also `/etc/nginx/conf.d`, where the config templates are rendered at start).

Release images are signed keylessly with cosign (GitHub OIDC). Verify before deploying:

```bash
cosign verify docker.io/sochub/sochub-backend:7.4 \
  --certificate-identity-regexp '^https://github.com/sochub/soc-hub/.github/workflows/images.yml@refs/tags/v' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com
```

## VPS / single server (Docker Compose)

```bash
curl -O https://raw.githubusercontent.com/sochub/soc-hub/main/docker-compose.prod.yml
curl -o .env https://raw.githubusercontent.com/sochub/soc-hub/main/.env.prod.example
# edit .env: replace every CHANGE_ME (SOCHUB_TAG too: a release such as 7.4)
docker compose -f docker-compose.prod.yml up -d
docker compose -f docker-compose.prod.yml run --rm backend python -m app.scripts.create_super_admin --email you@example.com
```

`migrate` runs on every `up` and exits; backend and worker wait for it.
Redis keeps queued background tasks in the `redis_data` volume (append-only file), so they survive restarts.
Backend temp files (spooled uploads) live on the `backend_tmp` volume: keep free disk for
(concurrent uploads) x `MAX_UPLOAD_MB` ([configuration.md](configuration.md#evidence-attachments)).
Upgrade by changing `SOCHUB_TAG` and running `up -d` again. Put Caddy or
another TLS proxy in front of `HTTP_PORT`.

## AWS ECS Fargate

- **One task definition** with `frontend` and `backend` containers: containers
  in a task share `localhost`, so set `BACKEND_UPSTREAM=http://localhost:8000`.
  Expose 8080 through an Application Load Balancer (HTTPS listener).
- **Worker**: a second service from the backend image with the worker command,
  desired count 1 or more.
- **Beat (scheduler)**: a third service from the backend image with the beat command,
  desired count **exactly 1** (set maximum percent to 100 so a deployment never runs two:
  scheduled jobs would run twice). Without it, periodic jobs (SLA checks, scheduled workflows,
  email intake, digests) never run.
- **Slack Socket Mode**: one more service with `python -m app.slack_socket`
  (outbound only, so no load balancer target).
- **Migrations**: a one-off task (`aws ecs run-task` with command
  `alembic upgrade head`) before rolling a new tag.
- **State**: RDS PostgreSQL and ElastiCache Redis (or Redis as its own small
  service). Fargate disks are ephemeral: use `STORAGE_BACKEND=s3`.
- **Secrets**: pass `SECRET_KEY`, `ENCRYPTION_KEY`, DB and Redis passwords (or the whole
  `DATABASE_URL` / `REDIS_URL`) from Secrets Manager or SSM Parameter Store via the task
  definition's `secrets`, to the API, worker, beat, Slack and migration tasks alike. Set
  `PUBLIC_BASE_URL` and `FRONTEND_URL` to the ALB's public `https://` URL in every one of them
  (the migration task too: it loads the same settings and refuses to start otherwise).
- **AI**: `AI_PROVIDER=bedrock` uses the task role, with no keys in config
  ([configuration.md](configuration.md#ai-provider)).

## Kubernetes

- `Deployment` + `Service` named `backend` (port 8000) and `frontend`
  (port 8080); the frontend's default `BACKEND_UPSTREAM` already matches.
  Expose `frontend` through an Ingress with TLS.
- Worker: a `Deployment` with the worker command; beat: a `Deployment` with `replicas: 1`; Slack Socket Mode: a
  `Deployment` with `python -m app.slack_socket` (no Service needed, it only connects out).
- Migrations: a `Job` (or a Helm pre-upgrade hook) running `alembic upgrade head`, with the same env as the
  API (`ENVIRONMENT`, `PUBLIC_BASE_URL`, `FRONTEND_URL`, secrets).
- Ingress: raise the controller's body size to at least `MAX_UPLOAD_MB` and its timeouts (MCP streams stay open
  up to an hour, AI calls about two minutes); ingress-nginx: `proxy-body-size`, `proxy-read-timeout: "3600"`,
  `proxy-send-timeout: "300"`.
- Attachments: a `PersistentVolumeClaim` at `/data/attachments`, or S3.
- Secrets: a `Secret` mounted with `envFrom`; images run fine with
  `runAsNonRoot: true`, `allowPrivilegeEscalation: false` and `readOnlyRootFilesystem: true` (see above).
  The Helm chart in `deploy/helm/sochub` does all of this, plus NetworkPolicies.

## Publishing a release (maintainers)

1. Create a Docker Hub access token with read/write on the `sochub` namespace.
2. In GitHub → Settings → Environments, create **`dockerhub`** and add the secrets
   `DOCKERHUB_USERNAME` and `DOCKERHUB_TOKEN` there (not as repository secrets).
   Recommended protection: deployment branches and tags limited to `v*`, and yourself
   as required reviewer.
3. Publish a release: `gh release create v7.5 --generate-notes` (or from the GitHub
   UI). Only people with write access can create releases, so contributors' pull
   requests can't trigger a publish.
