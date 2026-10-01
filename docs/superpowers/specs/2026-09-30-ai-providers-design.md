# Pluggable AI Providers — Design

Date: 2026-09-30
Status: Approved in brainstorming (sections 1–3); section 4 pending spec review

## Goal

Every AI feature (Copilot chat, welcome briefings, case analysis, note drafting,
action extraction, AI triage) works with any of: **Ollama**, **AWS Bedrock**,
**Google Gemini (AI Studio)**, **Google Vertex AI**, **OpenAI**,
**OpenAI-compatible** endpoints (vLLM, LM Studio, LocalAI, OpenRouter, Groq…),
and **Anthropic**. Azure OpenAI is out of scope for v1.

The provider is set per **deployment** (env vars) and may be **overridden per
tenant** by a tenant admin, who also supplies that tenant's credentials.

## Decisions (from brainstorming)

| Topic | Decision |
|---|---|
| Who chooses | Deployment default + per-tenant override (`AI_ALLOW_TENANT_OVERRIDE`, default `true`) |
| Library | **LiteLLM** Python SDK (`litellm.acompletion`), exact-pinned |
| Tenant credentials | Tenant admins enter credentials for every supported provider; stored Fernet-encrypted, write-only |
| AWS | Deployment uses boto's **standard credential chain** (env → shared config/profile → web identity/IRSA → ECS task role → EC2 instance profile). Tenants choose **assume-role** (role ARN + external ID, recommended) or **access keys** |
| Failure | A failing tenant override **does not fall back** to the deployment provider (data-residency); callers use existing static fallbacks |

## Current state (to be replaced)

`backend/app/services/ai_service.py` calls Ollama directly via
`_call_ollama("api/chat", {...})` (6 call sites; options `temperature`, and
`format: "json"` at 2 sites) and `_check_ollama_available()` (`/api/tags`).
No streaming, no embeddings. Callers: `api/v1/copilot.py` (4×
`AIService()`), `tasks/triage.py` (`AIService().generate_triage`).

## Architecture

### Modules

```
backend/app/ai/
  __init__.py
  llm.py        # complete(), test_connection(), errors, LiteLLM lockdown
  config.py     # ProviderConfig dataclass; resolve_config(db, tenant_id); cache
  providers.py  # per-provider: required fields, LiteLLM model prefix, kwargs builder (pure)
  aws.py        # Bedrock credentials: chain (no kwargs) or STS assume-role w/ external ID + cache
backend/app/models/tenant_ai_config.py
backend/app/schemas/ai_config.py
backend/app/api/v1/ai_config.py          # /api/v1/ai/config
backend/alembic/versions/<rev>_tenant_ai_configs.py
frontend/src/features/integrations/AIProviderCard.tsx
```

### Public interface

```python
class AIError(Exception): ...                 # generic provider failure (message scrubbed)
class AIUnavailable(AIError): ...             # auth / network / quota / timeout / not configured

async def complete(messages: list[dict], *, tenant_id: int | None,
                   temperature: float | None = None, json_mode: bool = False,
                   db: AsyncSession | None = None) -> str
async def test_connection(cfg: ProviderConfig) -> tuple[bool, str]
async def describe(db, tenant_id) -> dict      # {provider, model, source: "tenant"|"deployment"}
```

`AIService.__init__(self, tenant_id: int | None)`; all six call sites switch
to `llm.complete(...)`, keeping prompts/temperatures/parsing; `format: "json"`
→ `json_mode=True`. `_check_ollama_available` is removed; callers that used it
to choose a static fallback now catch `AIUnavailable`.

### Provider matrix

| `provider` | LiteLLM model | Tenant fields | Deployment env |
|---|---|---|---|
| `ollama` | `ollama_chat/<model>` | `api_base` (SSRF-guarded), `model` | `OLLAMA_BASE_URL`, `AI_MODEL` (fallback `OLLAMA_MODEL`) |
| `openai` | `openai/<model>` | `api_key`, `model`, `organization?` | `AI_API_KEY`, `AI_MODEL` |
| `openai_compatible` | `openai/<model>` + `api_base` | `api_base` (SSRF-guarded), `api_key?`, `model` | `AI_API_BASE`, `AI_API_KEY`, `AI_MODEL` |
| `anthropic` | `anthropic/<model>` | `api_key`, `model` | `AI_API_KEY`, `AI_MODEL` |
| `gemini` | `gemini/<model>` | `api_key`, `model` | `AI_API_KEY`, `AI_MODEL` |
| `vertex` | `vertex_ai/<model>` | `service_account_json`, `project`, `location`, `model` | `VERTEX_PROJECT`, `VERTEX_LOCATION`, `AI_MODEL`; credentials via Google ADC |
| `bedrock` | `bedrock/<model>` | `region`, `model`, `auth_mode` (`role`\|`keys`), `role_arn`+`external_id` or `access_key_id`+`secret_access_key`(+`session_token?`) | `AWS_REGION`/`AI_AWS_REGION`, `AI_MODEL`; standard credential chain |

Deployment env: `AI_PROVIDER` (default unset ⇒ `ollama` with today's
`OLLAMA_BASE_URL`/`OLLAMA_MODEL` — **backward compatible**), `AI_MODEL`,
`AI_API_BASE`, `AI_API_KEY`, `AI_TIMEOUT_SECONDS` (default 120, matching today),
`AI_ALLOW_TENANT_OVERRIDE` (default `true`).

### AWS Bedrock credentials (`aws.py`)

- **Deployment / `chain`**: pass no AWS kwargs to LiteLLM → boto3 default chain.
- **Tenant `role`**: our server (using its chain credentials) calls
  `sts:AssumeRole(RoleArn, ExternalId, RoleSessionName="sochub-tenant-<id>",
  DurationSeconds=3600)` via `boto3` in `asyncio.to_thread`; temporary
  credentials cached in-process per `(tenant_id, role_arn)` until 5 minutes
  before `Expiration`; passed to LiteLLM as `aws_access_key_id`,
  `aws_secret_access_key`, `aws_session_token`, `aws_region_name`.
- **Tenant `keys`**: pass the decrypted keys directly.
- External ID: generated per tenant on first view (`secrets.token_urlsafe(24)`),
  stored (not secret), shown in the card with a copyable trust-policy snippet:
  `{"Effect":"Allow","Principal":{"AWS":"<deployment role ARN>"},"Action":"sts:AssumeRole","Condition":{"StringEquals":{"sts:ExternalId":"<id>"}}}`.
  The deployment principal ARN is shown from `sts:GetCallerIdentity` (best-effort).

### Data model — `tenant_ai_configs`

| column | type | notes |
|---|---|---|
| id | int pk | |
| tenant_id | int fk tenants (cascade), unique | |
| enabled | bool, default true | override active |
| provider | str | one of the 7 |
| model | str | |
| api_base | str null | ollama / openai_compatible |
| region | str null | bedrock |
| project, location | str null | vertex |
| auth_mode | str null | bedrock: `role` \| `keys` |
| role_arn | str null | bedrock role mode |
| external_id | str null | bedrock role mode (generated) |
| credentials_enc | text null | Fernet-encrypted JSON: `api_key`, `organization`, `access_key_id`, `secret_access_key`, `session_token`, `service_account_json` |
| created_at, updated_at | timestamps | |

### Config resolution & cache

`resolve_config(db, tenant_id)`:
1. If `AI_ALLOW_TENANT_OVERRIDE` and a row exists with `enabled` → tenant
   config (secrets decrypted; Fernet `InvalidToken` → `AIUnavailable("AI
   credentials can't be decrypted — re-enter them")`).
2. Else deployment config from settings.
Result cached per tenant for 60 s (process-local dict with timestamps); the
cache entry is invalidated on PUT/DELETE in the same process (other processes
converge within 60 s).

### API — `/api/v1/ai/config` (admin of active tenant; super-admin follows existing rules)

- `GET` → `{source: "tenant"|"deployment", override_allowed, provider, model,
  api_base, region, project, location, auth_mode, role_arn, external_id,
  secrets_set: {api_key: bool, ...}, deployment: {provider, model}}`. Never
  returns secret values.
- `PUT` (body: provider, model, provider-specific fields, secrets) → validates
  required fields per provider; blank/omitted secret keeps the stored one;
  `api_base` SSRF-checked (`assert_url_allowed` with the tenant HTTP allowlist);
  returns GET shape. 403 when overrides are disabled.
- `POST /test` → runs `test_connection` on the submitted (unsaved) or saved
  config: a 1-message prompt (`"Reply with OK"`, `max_tokens` small, timeout
  30 s); returns `{ok, message}` with the provider error message scrubbed of
  anything resembling credentials.
- `DELETE` → removes the override (reverts to deployment default).
- All mutations audit-logged (`entity_type="ai_config"`; changes list field
  names only, never values).
- `GET /api/v1/ai/info` (any member) → `{provider, model, source}` for the
  Copilot label.

### Frontend

- `AIProviderCard` in Settings → Integrations (admin): provider select →
  dynamic fields from a per-provider field map; password inputs for secrets
  (placeholder "•••• (unchanged)" when set); Vertex JSON textarea; Bedrock
  auth-mode radio (Role recommended / Access keys) with external ID + trust
  policy snippet (copy button); Test (shows ok/error), Save, "Reset to
  deployment default". When `override_allowed=false`, card is read-only
  "Managed by deployment: <provider>/<model>".
- Copilot panel header shows "AI: <provider> · <model>" (from `/ai/info`).

## Security & privacy

- **SSRF**: tenant `api_base` (ollama, openai_compatible) is validated with
  the existing `assert_url_allowed` + tenant allowlist at save and at call
  time (DNS may change). Deployment config is trusted.
- **LiteLLM lockdown** at import of `app/ai/llm.py`: `litellm.telemetry =
  False`; `litellm.callbacks = []`; `litellm.success_callback = []`;
  `litellm.failure_callback = []`; `litellm.suppress_debug_info = True`;
  `litellm.drop_params = True`; no proxy/server components imported.
  `litellm`, `boto3`, `google-auth` pinned exactly in requirements.
- **No fallback** from a failing tenant override to the deployment provider.
- **Secrets** write-only via API; never logged; error messages scrubbed
  (regex for `sk-…`, `AKIA…`, `AIza…`, bearer tokens, private-key blocks).
- **Prompt content** is never logged by our code.

## Error handling

- LiteLLM `AuthenticationError`, `RateLimitError`, `APIConnectionError`,
  `Timeout`, `ServiceUnavailableError`, `NotFoundError` (bad model) →
  `AIUnavailable(<scrubbed short reason>)`; any other exception → `AIError`.
- Existing callers already degrade to static output on Ollama failure; they
  now catch `AIUnavailable`/`AIError` instead of `httpx` errors, preserving
  behaviour: Copilot returns its existing "AI unavailable" message, triage
  marks the triage row `failed` with the reason, briefings fall back to the
  static briefing.
- `json_mode=True` with providers lacking JSON mode: `drop_params` removes it;
  existing JSON extraction/parsing handles fenced/chatty output.
- STS failures (`AccessDenied`, bad external ID) → `AIUnavailable("Bedrock
  role assumption failed: <code>")`.

## Testing

Pure/unit (no network):
- `providers.build_kwargs(cfg)` for every provider: correct LiteLLM model
  prefix and kwargs; missing required field → error; secrets only in kwargs.
- `resolve_config`: override enabled/disabled, `AI_ALLOW_TENANT_OVERRIDE`
  false, InvalidToken → `AIUnavailable`, cache hit/expiry/invalidation.
- Backward compatibility: with no `AI_*` env set, deployment config is
  `ollama` + `OLLAMA_BASE_URL` + `OLLAMA_MODEL`.
- `aws`: chain mode passes no keys; role mode calls STS with RoleArn +
  ExternalId (stubbed boto3 client), caches until expiry−5 min, refreshes
  after; keys mode passes keys.
- `llm.complete`: monkeypatched `litellm.acompletion` — returns text;
  `json_mode` → `response_format={"type":"json_object"}`; exception mapping;
  no fallback to deployment when tenant override fails.
- Error scrubbing regexes.
- SSRF: private `api_base` rejected at PUT and at call time.
API (in-process ASGI + dependency overrides, temp tenant cleaned up by id):
- GET/PUT/DELETE/test, secrets never returned, blank secret keeps old,
  403 when overrides disabled, viewer 403, audit rows contain no values.
Regression:
- Existing copilot/triage tests pass; `AIService` call sites use `complete`
  (monkeypatched) with the same prompts.
Manual acceptance (human): one real call per provider the operator has
credentials for (at minimum Ollama locally), and a Bedrock assume-role setup.

## Out of scope (v1)

Azure OpenAI; streaming responses; embeddings; per-feature model selection
(e.g. different model for triage vs chat); cost/usage tracking; LiteLLM proxy.
