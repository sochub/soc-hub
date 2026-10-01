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

## AI provider

All AI features (Copilot, case analysis, triage) go through one provider layer
(LiteLLM, `backend/app/ai/`). The **deployment default** comes from the env vars
below. A tenant admin can optionally **override** it for their tenant under
**Integrations → AI provider**.

| Variable | Description | Default |
|---|---|---|
| `AI_PROVIDER` | `ollama` \| `openai` \| `openai_compatible` \| `anthropic` \| `gemini` \| `vertex` \| `bedrock`. Unset or blank means `ollama` | _(unset)_ |
| `AI_MODEL` | Model name without a provider prefix, e.g. `gpt-4o-mini`. For Ollama it falls back to `OLLAMA_MODEL` | _(unset)_ |
| `AI_API_BASE` | Endpoint for `ollama` / `openai_compatible`. For Ollama it falls back to `OLLAMA_BASE_URL` | _(unset)_ |
| `AI_API_KEY` | Key for `openai` / `anthropic` / `gemini` (optional for `openai_compatible`) | _(unset)_ |
| `AI_AWS_REGION` | Bedrock region. Falls back to `AWS_REGION`, then `AWS_DEFAULT_REGION` | _(unset)_ |
| `VERTEX_PROJECT` / `VERTEX_LOCATION` | Vertex AI project and region | _(unset)_ |
| `AI_TIMEOUT_SECONDS` | Per-call timeout (must be > 0) | `120` |
| `AI_ALLOW_TENANT_OVERRIDE` | Allow tenant admins to set their own provider | `true` |
| `OLLAMA_BASE_URL` / `OLLAMA_MODEL` | Legacy Ollama settings, still honoured | `http://localhost:11434` / `llama3` |

**Backward compatibility.** With `AI_PROVIDER` unset, nothing changes: the app
uses Ollama at `OLLAMA_BASE_URL` with `OLLAMA_MODEL`, as before.

The settings are read from the backend environment or `backend/.env` (see
`backend/.env.example`). `docker-compose.yml` does not pass `AI_*` through, so
under compose put them in `backend/.env`. That file is bind-mounted into the
backend and worker containers. Restart both containers after a change.

Cloud SDK variables are different. `AWS_REGION` / `AWS_DEFAULT_REGION`, the
`AWS_*` credential variables (`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`,
`AWS_SESSION_TOKEN`, `AWS_PROFILE`, ...) and `GOOGLE_APPLICATION_CREDENTIALS` are
read by boto3 and google-auth straight from the process environment. Pydantic
reads `backend/.env` into the settings object but does not export it to
`os.environ`, so these variables have no effect in `backend/.env`. Set them as
real environment variables: in the backend and worker `environment:` section of
`docker-compose.yml`, or on the host. (`AI_AWS_REGION` is an app setting and does
work from `backend/.env`.)

> Model choice matters for the copilot's action reliability. With Ollama,
> `llama3.1` and `qwen2.5` emit structured actions more reliably than `llama3`.

### Examples

```bash
# Ollama, local (the default). These two lines are equivalent to leaving AI_* unset.
AI_PROVIDER=ollama
AI_MODEL=llama3.1                       # AI_API_BASE falls back to OLLAMA_BASE_URL

# OpenAI
AI_PROVIDER=openai
AI_MODEL=gpt-4o-mini
AI_API_KEY=sk-...

# OpenAI-compatible server (vLLM, LM Studio, LocalAI, ...)
AI_PROVIDER=openai_compatible
AI_MODEL=meta-llama/Llama-3.1-8B-Instruct
AI_API_BASE=http://vllm:8000/v1
# AI_API_KEY=...                        # only if the server requires one

# Anthropic
AI_PROVIDER=anthropic
AI_MODEL=claude-sonnet-4-5
AI_API_KEY=sk-ant-...

# Google Gemini (AI Studio key)
AI_PROVIDER=gemini
AI_MODEL=gemini-2.0-flash
AI_API_KEY=AIza...

# Google Vertex AI with Application Default Credentials
# (GOOGLE_APPLICATION_CREDENTIALS, gcloud ADC, or the GCE/GKE service account)
AI_PROVIDER=vertex
AI_MODEL=gemini-2.0-flash
VERTEX_PROJECT=my-project
VERTEX_LOCATION=us-central1

# AWS Bedrock with an instance / task role (no keys in config)
AI_PROVIDER=bedrock
AI_MODEL=anthropic.claude-3-5-sonnet-20240620-v1:0
AI_AWS_REGION=us-east-1
```

### AWS credentials (Bedrock)

The deployment default never stores AWS keys. boto3's standard credential chain
applies, in this order:

1. Environment variables (`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_SESSION_TOKEN`)
2. Shared config and credentials files / `AWS_PROFILE`
3. Web identity token (EKS IRSA, `AWS_WEB_IDENTITY_TOKEN_FILE`)
4. ECS / Fargate task role
5. EC2 instance profile (IMDS)

The deployment identity needs `bedrock:InvokeModel` on the models it uses.

### Tenant overrides

Tenant admins configure a provider under **Integrations → AI provider**
(`GET`/`PUT`/`DELETE /api/v1/ai/config`; test with `POST /api/v1/ai/config/test`).

- Credentials are encrypted at rest with `SECRET_KEY` and are write-only: the
  API only reports which secret fields are set. Leaving a secret blank on save
  keeps the stored value. Switching provider drops the stored secrets.
- Tenant Vertex needs a service-account JSON key. Tenant Bedrock uses either
  **role** mode (assume a role, below) or **keys** mode (stored access keys).
  Tenants can't use the server's own credential chain.
- If `SECRET_KEY` changes, stored credentials can't be decrypted. AI is then
  unavailable for that tenant until an admin re-enters the credentials.
- With `AI_ALLOW_TENANT_OVERRIDE=false`, the deployment provider is used for
  everyone. Saving (`PUT`) and testing an unsaved config (`POST .../test` with a
  body) return `403`. An existing tenant row is ignored but not deleted.
- Changes apply within about 60 seconds in other backend and worker processes
  (per-process cache).

**No fallback.** When a tenant config is enabled but fails (bad key, endpoint
down, AssumeRole denied, undecryptable credentials), the call fails. It never
falls back to the deployment provider, so tenant case data is never sent to a
provider the tenant didn't choose.

**Errors.** Users only see the error type, e.g. *"AI Assistant unavailable
(AuthenticationError). Ask an admin to check the AI provider settings."* The
full reason, with secrets scrubbed, is shown only to admins in **Test connection**.

#### Bedrock assume-role setup

1. In **Integrations → AI provider**, pick Bedrock and **role** mode, then save.
   The first save in role mode generates the tenant's **External ID**. The card
   shows it with the server's AWS principal. That principal is shown as an IAM
   role ARN (`arn:aws:iam::<acct>:role/<name>`). A role path is not preserved, so
   correct the ARN if your role has one. The config stays disabled until a role
   ARN is set.
2. In the tenant's AWS account, create a role with this trust policy:

   ```json
   {
     "Version": "2012-10-17",
     "Statement": [{
       "Effect": "Allow",
       "Principal": { "AWS": "<server principal ARN>" },
       "Action": "sts:AssumeRole",
       "Condition": { "StringEquals": { "sts:ExternalId": "<External ID>" } }
     }]
   }
   ```

3. Give the role permission to invoke the model:

   ```json
   {
     "Version": "2012-10-17",
     "Statement": [{
       "Effect": "Allow",
       "Action": "bedrock:InvokeModel",
       "Resource": "arn:aws:bedrock:<region>::foundation-model/*"
     }]
   }
   ```

   Inference profiles also need their `inference-profile` ARN in `Resource`.
4. The server's own identity needs `sts:AssumeRole` on that role. Paste the role
   ARN, save, and run **Test connection**.

The server assumes the role (1-hour sessions, session name
`sochub-tenant-<id>`) and caches the credentials until 5 minutes before they
expire.

#### Tenant base URLs (SSRF guard)

For tenant `ollama` and `openai_compatible` configs, `api_base` goes through the
same outbound guard as workflow HTTP nodes, both when it is saved and on every
call. A host that resolves to a private, loopback, link-local, CGNAT or reserved
address is rejected unless it is on the tenant's **HTTP allowlist**
(Integrations). Allowlisted hosts are trusted by name.

For a non-allowlisted `http://` URL, the call connects to the IP that was vetted,
so a second DNS answer can't rebind it to an internal address. `https://` keeps
its hostname, because TLS certificate verification already defeats rebinding.
The deployment default (env vars) is trusted and is not checked.

Redirects are refused. Tenant calls to these endpoints never follow a 3xx
response, because the redirect target would skip the guard. The call fails
with "AI endpoint redirect refused". Point `api_base` at the final URL.

### Privacy and logging

- LiteLLM is locked down. Telemetry is off, all callbacks are cleared, and
  message logging is off. `LITELLM_LOCAL_MODEL_COST_MAP=True` makes it use its
  bundled model cost map instead of fetching one from GitHub. Prompts go only
  to the configured provider.
- Each call logs one `ai_call` line with provider, model, source, tenant,
  duration, outcome and, on failure, the exception type and HTTP status.
  Prompts, responses, error text and secrets are never logged.

## Threat-intel enrichment

SOC Hub can look up IOCs and artifacts in public threat-intelligence services and
show the verdicts on the IOC/artifact. It is configured per tenant by a tenant
admin in **Integrations -> Threat intel** (API: `GET/PUT/DELETE /api/v1/enrichment/config`,
`POST /api/v1/enrichment/config/test`).

**Sources and keys**

| Source | Covers | Key |
|---|---|---|
| VirusTotal | IP, domain, URL, file hash | VirusTotal API key |
| URLhaus | IP, domain, URL, file hash | free abuse.ch Auth-Key (auth.abuse.ch) |
| ThreatFox | IP, domain, URL, file hash | the same abuse.ch Auth-Key |
| RDAP | IP, domain | none |
| crt.sh | domain | none |

A source without its key is simply not run. Keys are stored encrypted, are
write-only (responses only say whether a key is set) and are never logged. Each
source can be switched off individually. Only lookups are made: nothing is ever
uploaded (no files, no submissions).

**Settings** (defaults in brackets)

- `auto_max_tlp` [`green`]: automatic lookups run for indicators up to this TLP
  (`none`, `white`, `green`, `amber`, `red`; `none` disables automatic lookups).
- `artifact_tlp` [`amber`]: artifacts have no TLP of their own and are treated as
  this TLP. With the defaults artifacts are therefore not looked up automatically.
- `cache_ttl_hours` [24, 1-720]: a fresh `ok`/`not_found` result is reused instead
  of calling the source again. Manual runs bypass the cache.
- `vt_per_minute` [4] and `vt_per_day` [500]: VirusTotal limits. The defaults are
  the VirusTotal free tier (4 requests/minute, 500/day); raise them only with a paid key.
- `internal_domains` [none]: domains (and their subdomains) that are never sent out.
- `sources`: per-source on/off switches.

Saving the Threat intel card saves the **full configuration**. Through the API, a
`PUT` merges the fields sent: any field left out keeps its stored value (or its
default when nothing is stored yet). Keys are kept unless you send a new one or a
`clear_*` flag.

**Rate limits.** Counted per tenant in Redis and shared by all workers, except
crt.sh, which is limited globally. VirusTotal uses the two limits above; URLhaus and
ThreatFox 60/minute; RDAP 30/minute; crt.sh 1 per 5 seconds. Over the limit, a
lookup is retried later with a delay (shown as pending); if the wait would be an hour
or more (VirusTotal daily quota) or retries are exhausted, the result is `rate_limited`.

**TLP rules**

- Automatic lookups happen when an IOC or artifact is created, and when its value,
  type or (for IOCs) TLP changes. This applies in every process (API, worker and
  scripts), but only if the TLP is at or below `auto_max_tlp`.
- A manual **Enrich** run (`POST /api/v1/enrichment/{ioc|artifact}/{id}/run`,
  analyst or above) works on any TLP, but anything other than white or green needs
  explicit confirmation (`{"confirm": true}`; the API answers `409` otherwise).

**What is never sent out.** These values are marked `skipped` and no source is called:

- private, reserved, loopback, link-local and multicast IPs, including shorthand
  or hex forms inside URLs (`127.1`, `0x7f.1`, `2130706433`);
- single-label hosts such as `localhost` or `intranet`;
- anything under `internal_domains`, whether it is a domain or the host of a URL;
- an IP address stored as a "domain" (not enrichable).

**RDAP redirects.** RDAP goes through the rdap.org redirector. Up to 3 redirects are
followed; each must be `https` and pass the same internal-address check, and TLS
verification protects the hostname.

**Kill switch.** `ENRICHMENT_ENABLED=false` (environment, default `true`) stops new
enqueues (automatic and manual), tasks already queued and pending retries: the worker
task does nothing, and a manual run answers `503`. Restart the backend and the worker
after changing it.

**Logging.** One `ti_lookup tenant=.. source=.. type=.. outcome=.. duration_ms=..`
line per source per lookup, and one `ti_config` line per config save, test or delete.
Indicator values, keys and response bodies are never logged.

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
