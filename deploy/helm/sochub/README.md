# SOC Hub Helm chart

Kubernetes equivalent of `docker-compose.prod.yml`: api, Celery worker, Celery beat, nginx frontend,
optional Slack Socket listener, optional Ollama, and a migration Job. PostgreSQL and Redis come from
pinned Bitnami subcharts (evaluation) or external services (production).

## Quickstart

```bash
helm dependency build deploy/helm/sochub
helm install sochub deploy/helm/sochub   # images: docker.io/sochub/sochub-*:<appVersion>
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
The bundled PostgreSQL/Redis passwords are generated on first install and kept in the `<release>-datastores`
Secret (same `lookup` caveat for GitOps: set `postgresql.auth.password` / `redis.auth.password` there).

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
- Celery beat runs in its own Deployment fixed at one replica; `worker.replicas` scales the workers.
- Hardening: every pod runs non-root with `seccompProfile: RuntimeDefault`, a read-only root filesystem
  (emptyDir for `/tmp` etc.), no capabilities and no service-account token. `networkPolicy.enabled` (default)
  denies everything, then allows ingress controller -> frontend -> api, backend pods -> DNS, the bundled
  datastores/Ollama and `networkPolicy.egressPorts`. Restrict `networkPolicy.ingressFrom` to your ingress
  controller and set `frontend.realIpFrom` to its pod CIDR so login throttling and audit logs see client IPs.
- Production refuses an ingress without `tls` (set `ingress.tlsTerminatedUpstream=true` if a load balancer in
  front terminates TLS) and `frontend.hstsHeader` takes only the header value.
- Images default to the chart `appVersion`; set `images.*.digest` to pin what you verified.
- See `values-production.yaml` for a full example.

## Migrations and upgrades

The `alembic upgrade head` Job runs as a Helm hook on every `helm upgrade` (pre-upgrade), before new pods roll.
On first install it runs `pre-install` when the Secret and database are external and pre-existing
(`existingSecret` set, `postgresql.enabled=false`); otherwise it runs `post-install`, because a
pre-install hook executes before the release's own Secret, ConfigMap and bundled PostgreSQL exist. The Job
retries until the database is reachable.

Rollbacks do not downgrade the schema.

## Upgrading from chart 0.1.x

0.1.x shipped the bundled datastores with the placeholder password `sochub-change-me`, which the backend now
refuses in production. Before upgrading a release that used it, pick a strong password, set it on the running
PostgreSQL (`ALTER USER sochub PASSWORD '...'`) and pass it as `postgresql.auth.password` (Redis: pass the
new `redis.auth.password`; Redis keeps no password on disk). The worker no longer embeds beat: the upgrade
adds a `<release>-beat` Deployment.
