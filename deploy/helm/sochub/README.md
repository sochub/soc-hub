# SOC Hub Helm chart

Kubernetes equivalent of `docker-compose.prod.yml`: api, Celery worker (embedded beat), nginx frontend,
optional Slack Socket listener, optional Ollama, and a migration Job. PostgreSQL and Redis come from
pinned Bitnami subcharts (evaluation) or external services (production).

## Quickstart

```bash
helm dependency build deploy/helm/sochub
helm install sochub deploy/helm/sochub \
  --set images.backend.repository=registry.example.com/sochub-backend \
  --set images.frontend.repository=registry.example.com/sochub-frontend
kubectl port-forward svc/sochub-frontend 8080:8080
kubectl exec -it deploy/sochub-api -- python -m app.scripts.create_super_admin --email you@example.com
```

> **Key management (read this).** With the default `secrets.generate=true`, SECRET_KEY and ENCRYPTION_KEY are
> generated on first install and kept across upgrades via a cluster `lookup`. **GitOps/ArgoCD/Flux users MUST set
> `secrets.generate=false`** and provide the keys (or `existingSecret`): those tools render without cluster access,
> so keys would rotate on every sync and all stored integration credentials become undecryptable. The generated
> Secret is kept on uninstall (`resource-policy: keep`); reinstalling over an existing database requires the
> original ENCRYPTION_KEY or all stored credentials are lost. Back the keys up.
> After `helm uninstall` the kept Secret survives but no release owns it any more, so a reinstall fails with Helm's
> "resource already exists" error until you delete the old Secret (save its keys first!) or install with
> `existingSecret` pointing at it. The `existingSecret` route is recommended because it reuses the same keys.
The bundled database passwords are placeholders: evaluation only.

## Production checklist

- Use external PostgreSQL and Redis: `postgresql.enabled=false`, `redis.enabled=false`.
- Create a Secret yourself (`existingSecret`) with strong `SECRET_KEY`, `ENCRYPTION_KEY` (>= 32 chars, different),
  `DATABASE_URL` (`postgresql+asyncpg://...`), `REDIS_URL`. Back the keys up.
- Set `config.frontendUrl` / `config.publicBaseUrl` to the public HTTPS URL; enable `ingress` with TLS.
- Storage: multi-node needs ReadWriteMany because api AND worker both write attachments (email intake
  writes from the worker). With the default ReadWriteOnce the chart adds a podAffinity so the worker
  lands on the api's node. Alternatively use S3 (`config.storageBackend=s3`, `s3.*`).
- On-prem IMAP or MISP servers on private addresses are blocked by the SSRF guard. Allow them with
  `MAILBOX_PRIVATE_HOSTS` / `MISP_PRIVATE_HOSTS` (JSON lists of exact hostnames) via `extraEnv`:
  `extraEnv: [{name: MAILBOX_PRIVATE_HOSTS, value: '["mail.corp.internal"]'}, {name: MISP_PRIVATE_HOSTS, value: '["misp.corp.internal"]'}]`
- The worker is fixed at one replica (embedded Celery beat).
- See `values-production.yaml` for a full example.

## Migrations and upgrades

The `alembic upgrade head` Job runs as a Helm hook on every `helm upgrade` (pre-upgrade), before new pods roll.
On first install it runs `pre-install` when the Secret and database are external and pre-existing
(`existingSecret` set, `postgresql.enabled=false`); otherwise it runs `post-install`, because a
pre-install hook executes before the release's own Secret, ConfigMap and bundled PostgreSQL exist. The Job
retries until the database is reachable.

Rollbacks do not downgrade the schema.
