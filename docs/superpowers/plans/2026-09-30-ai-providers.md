# Pluggable AI Providers Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every AI feature works with Ollama, AWS Bedrock, Gemini, Vertex AI, OpenAI, OpenAI-compatible endpoints and Anthropic — configured per deployment and optionally overridden per tenant with the tenant's own credentials.

**Architecture:** A new `app/ai/` package wraps LiteLLM (`litellm.acompletion`) behind one async `complete()` function. Pure helpers build per-provider LiteLLM kwargs and resolve which config applies (tenant override → deployment env). Bedrock tenant credentials come from STS assume-role (external ID) or stored keys; the deployment uses boto's standard credential chain. `AIService` keeps its prompts/parsing and only swaps its transport.

**Tech Stack:** FastAPI, SQLAlchemy async, Alembic, LiteLLM (new), boto3 (new), google-auth (new), Fernet (`app/utils/crypto.py`), React 19 + TanStack Query.

**Spec:** `docs/superpowers/specs/2026-09-30-ai-providers-design.md` — read it before starting any task.

## Global Constraints

- **Backward compatible:** with no `AI_*` env set, the deployment provider is `ollama` using `OLLAMA_BASE_URL` + `OLLAMA_MODEL` (today's behaviour).
- **No fallback:** a failing tenant override never falls back to the deployment provider.
- LiteLLM lockdown at import: `litellm.telemetry = False`; `litellm.callbacks = []`; `litellm.success_callback = []`; `litellm.failure_callback = []`; `litellm.suppress_debug_info = True`; `litellm.drop_params = True`.
- `litellm`, `boto3`, `google-auth` pinned to exact versions in `backend/requirements.txt`.
- Secrets are write-only through the API (only `secrets_set` booleans are returned), never logged, and scrubbed from error messages.
- Tenant `api_base` (providers `ollama`, `openai_compatible`) is SSRF-checked with `assert_url_allowed` + the tenant's `workflow_http_allowlist`, at save time AND at call time.
- `/api/v1/ai/config*` is admin-only; every mutation is audit-logged with field names only (never values).
- Config cache TTL **60 s** per tenant, invalidated on PUT/DELETE. Bedrock assumed-role credentials cached until **5 minutes** before expiry; `DurationSeconds=3600`; `RoleSessionName="sochub-tenant-<tenant_id>"`.
- Test connection: one user message `"Reply with OK"`, `max_tokens=5`, timeout **30 s**. Normal calls: `AI_TIMEOUT_SECONDS` default **120**.
- Providers: exactly `ollama`, `openai`, `openai_compatible`, `anthropic`, `gemini`, `vertex`, `bedrock`. Azure is out of scope.
- Backend tests run in the container: `docker compose exec -T backend python -m pytest tests/<file> -q` (repo root). After backend changes: `docker restart case_management-backend-1 case_management-worker-1`. After requirement changes: `docker compose build backend worker && docker compose up -d --no-deps backend worker`.
- ⚠️ Live dev DB: test data deleted by exact row id only. No login/token minting — API tests use in-process `httpx.ASGITransport` + `app.dependency_overrides` (pattern: `backend/tests/test_wf_api.py`).
- Commits are SSH-signed via 1Password: `perl -e 'alarm 60; exec @ARGV' git commit ...`; on timeout leave changes staged and report "commit pending signing". Trailer: `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. **Provider switch leaks a key to the wrong vendor** — admin changes OpenAI → Anthropic leaving the key blank: the stored OpenAI key must be discarded, not sent to Anthropic. *Test: Task 6 `test_put_provider_change_discards_old_secrets`.*
2. **Model ids containing slashes or dots** (`meta-llama/Llama-3-70b`, `us.anthropic.claude-…-v1:0`) must reach LiteLLM verbatim after the provider prefix. *Test: Task 2 `test_model_id_passed_verbatim`.*
3. **Invalid Vertex service-account JSON** pasted into the card → 422 with a clear message, never a 500. *Test: Task 6 `test_put_rejects_invalid_vertex_json`.*
4. **Tenant points Ollama at an internal host** (e.g. `http://ollama:11434`) → rejected with "add it to the HTTP allowlist", and allowed once allowlisted. *Test: Task 6 `test_put_internal_api_base_requires_allowlist`.*
5. **Deployment misconfigured** (`AI_PROVIDER=openai`, no key) → app boots; AI calls return a clean "AI unavailable" message; Copilot/triage fall back as today. *Test: Task 4 `test_complete_unconfigured_deployment_is_unavailable`.*

---

## File Structure

```
backend/requirements.txt                         # + litellm, boto3, google-auth (exact pins)  (Task 1)
backend/app/core/config.py                       # + AI_* settings                              (Task 1)
backend/app/ai/__init__.py                                                                       (Task 1)
backend/app/ai/errors.py        # AIError, AIUnavailable, scrub()                               (Task 1)
backend/app/ai/providers.py     # ProviderConfig, constants, missing_fields(), build_kwargs()   (Task 2)
backend/app/ai/aws.py           # bedrock_credentials(), STS assume-role cache                  (Task 3)
backend/app/ai/llm.py           # LiteLLM lockdown, complete_with(), complete(), test_connection(), describe()  (Task 4)
backend/app/models/tenant_ai_config.py                                                           (Task 5)
backend/alembic/versions/e3f4a5b6c7d8_tenant_ai_configs.py                                       (Task 5)
backend/app/ai/config.py        # deployment_config(), row_to_config(), resolve_config(), cache  (Task 5)
backend/app/schemas/ai_config.py                                                                 (Task 6)
backend/app/api/v1/ai_config.py # /ai/config, /ai/config/test, /ai/info                         (Task 6)
backend/app/services/ai_service.py  # transport swap                                           (Task 7)
backend/app/api/v1/copilot.py, backend/app/tasks/triage.py  # pass tenant_id                   (Task 7)
frontend/src/features/integrations/AIProviderCard.tsx, Integrations.tsx                         (Task 8)
frontend/src/features/copilot/CopilotChat.tsx    # provider label                               (Task 8)
docs/configuration.md, docs/features.md                                                          (Task 9)
tests: test_ai_errors.py, test_ai_providers.py, test_ai_aws.py, test_ai_llm.py, test_ai_config.py, test_ai_api.py, test_ai_service.py
```

---

### Task 1: Dependencies, settings, errors and scrubbing

**Files:**
- Modify: `backend/requirements.txt`, `backend/app/core/config.py`
- Create: `backend/app/ai/__init__.py` (empty), `backend/app/ai/errors.py`
- Test: `backend/tests/test_ai_errors.py`

**Interfaces:**
- Produces: `AIError(Exception)`, `AIUnavailable(AIError)`, `scrub(text: str, secrets: Iterable[str] = ()) -> str`; settings `AI_PROVIDER: Optional[str]`, `AI_MODEL: Optional[str]`, `AI_API_BASE: Optional[str]`, `AI_API_KEY: Optional[str]`, `AI_AWS_REGION: Optional[str]`, `VERTEX_PROJECT: Optional[str]`, `VERTEX_LOCATION: Optional[str]`, `AI_TIMEOUT_SECONDS: float = 120`, `AI_ALLOW_TENANT_OVERRIDE: bool = True`.

- [ ] **Step 1: Add and pin dependencies**

Append `litellm`, `boto3`, `google-auth` to `backend/requirements.txt`, rebuild (`docker compose build backend worker && docker compose up -d --no-deps backend worker`), then read the installed versions with `docker compose exec -T backend pip show litellm boto3 google-auth | grep -E "^(Name|Version)"` and pin them exactly (`litellm==X.Y.Z` etc.). Rebuild again. Run `docker compose exec -T backend pip-audit` (install if missing) — expected "No known vulnerabilities found"; if litellm pulls a vulnerable transitive, pin a fixed version of it and note it in the report.

- [ ] **Step 2: Write the failing tests**

`backend/tests/test_ai_errors.py`:
```python
from app.ai.errors import AIError, AIUnavailable, scrub


def test_unavailable_is_ai_error():
    assert issubclass(AIUnavailable, AIError)


def test_scrub_known_secret_values():
    out = scrub("auth failed for key super-secret-value-123", ["super-secret-value-123"])
    assert "super-secret-value-123" not in out and "***" in out


def test_scrub_patterns():
    text = (
        "openai sk-proj-ABCDEFGHIJKLMNOP1234 anthropic sk-ant-api03-XYZXYZXYZXYZ "
        "aws AKIAABCDEFGHIJKLMNOP google AIzaSyA1234567890abcdefghijklmnop "
        "hdr Bearer abc.def.ghi "
        '"private_key": "-----BEGIN PRIVATE KEY-----\\nMIIE\\n-----END PRIVATE KEY-----\\n"'
    )
    out = scrub(text)
    for leaked in ("sk-proj-ABCDEFGHIJKLMNOP1234", "sk-ant-api03-XYZXYZXYZXYZ", "AKIAABCDEFGHIJKLMNOP",
                   "AIzaSyA1234567890abcdefghijklmnop", "abc.def.ghi", "MIIE"):
        assert leaked not in out


def test_scrub_truncates():
    assert len(scrub("x" * 5000)) <= 300


def test_scrub_ignores_blank_secrets():
    assert scrub("hello", ["", None]) == "hello"
```

- [ ] **Step 3: Run — expect FAIL** (`ModuleNotFoundError: app.ai`).
Run: `docker compose exec -T backend python -m pytest tests/test_ai_errors.py -q`

- [ ] **Step 4: Implement**

`backend/app/ai/errors.py`:
```python
"""AI-layer errors and secret scrubbing for provider error messages."""
import re
from typing import Iterable, Optional

_MAX = 300
_PATTERNS = [
    (re.compile(r"sk-[A-Za-z0-9_\-]{8,}"), "sk-***"),
    (re.compile(r"\b(AKIA|ASIA)[0-9A-Z]{16}\b"), "AKIA***"),
    (re.compile(r"AIza[0-9A-Za-z_\-]{20,}"), "AIza***"),
    (re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-~+/=]+"), "Bearer ***"),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S), "<private key>"),
    (re.compile(r'"private_key"\s*:\s*"[^"]*"'), '"private_key": "***"'),
]


class AIError(Exception):
    """A provider call failed for a reason the caller may show to the user."""


class AIUnavailable(AIError):
    """Auth, network, quota, timeout, misconfiguration — AI is unusable right now."""


def scrub(text: str, secrets: Iterable[Optional[str]] = ()) -> str:
    out = str(text)
    for s in secrets:
        if s and len(s) >= 4:
            out = out.replace(s, "***")
    for rx, repl in _PATTERNS:
        out = rx.sub(repl, out)
    return out[:_MAX]
```

In `backend/app/core/config.py`, after the `OLLAMA_*` settings, add:
```python
    # --- AI provider (deployment default; see docs/configuration.md) ---
    # Unset AI_PROVIDER keeps the legacy behaviour: Ollama at OLLAMA_BASE_URL/OLLAMA_MODEL.
    AI_PROVIDER: Optional[Literal["ollama", "openai", "openai_compatible", "anthropic",
                                  "gemini", "vertex", "bedrock"]] = None
    AI_MODEL: Optional[str] = None
    AI_API_BASE: Optional[str] = None
    AI_API_KEY: Optional[str] = None
    AI_AWS_REGION: Optional[str] = None
    VERTEX_PROJECT: Optional[str] = None
    VERTEX_LOCATION: Optional[str] = None
    AI_TIMEOUT_SECONDS: float = Field(120, gt=0)
    AI_ALLOW_TENANT_OVERRIDE: bool = True
```
(import `Optional` from typing if not already imported; `Literal`/`Field` are already used in this file).

- [ ] **Step 5: Run — expect PASS**, then the full suite once.

- [ ] **Step 6: Commit**
```bash
git add backend/requirements.txt backend/app/core/config.py backend/app/ai backend/tests/test_ai_errors.py
git commit -m "feat(ai): add litellm/boto3/google-auth, AI settings, error scrubbing"
```

---

### Task 2: Provider config and LiteLLM kwargs (pure)

**Files:**
- Create: `backend/app/ai/providers.py`
- Test: `backend/tests/test_ai_providers.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `PROVIDERS: tuple[str, ...]` (the 7 names), `SECRET_FIELDS: dict[str, frozenset[str]]`, `ALL_SECRET_FIELDS: frozenset[str]`, `SSRF_GUARDED: frozenset[str] = {"ollama","openai_compatible"}`.
  - `@dataclass class ProviderConfig: provider: str; model: str; source: str  # "tenant"|"deployment"; tenant_id: Optional[int]=None; api_base: Optional[str]=None; region: Optional[str]=None; project: Optional[str]=None; location: Optional[str]=None; auth_mode: Optional[str]=None  # bedrock: "chain"|"role"|"keys"; role_arn: Optional[str]=None; external_id: Optional[str]=None; secrets: dict = field(default_factory=dict)`
  - `class ConfigError(ValueError)`
  - `missing_fields(cfg) -> list[str]`
  - `build_kwargs(cfg, aws_creds: Optional[dict] = None) -> dict` (raises `ConfigError`)

- [ ] **Step 1: Write the failing tests**

`backend/tests/test_ai_providers.py`:
```python
import json
import pytest

from app.ai.providers import ProviderConfig, ConfigError, build_kwargs, missing_fields, PROVIDERS, SECRET_FIELDS


def cfg(provider, **kw):
    kw.setdefault("model", "m1")
    kw.setdefault("source", "tenant")
    return ProviderConfig(provider=provider, **kw)


def test_seven_providers():
    assert set(PROVIDERS) == {"ollama", "openai", "openai_compatible", "anthropic", "gemini", "vertex", "bedrock"}
    assert set(SECRET_FIELDS) == set(PROVIDERS)


def test_ollama():
    kw = build_kwargs(cfg("ollama", api_base="http://h:11434"))
    assert kw == {"model": "ollama_chat/m1", "api_base": "http://h:11434"}


def test_openai_with_org():
    kw = build_kwargs(cfg("openai", secrets={"api_key": "k", "organization": "org1"}))
    assert kw == {"model": "openai/m1", "api_key": "k", "organization": "org1"}


def test_openai_compatible_without_key_gets_placeholder():
    kw = build_kwargs(cfg("openai_compatible", api_base="https://llm.example/v1"))
    assert kw["model"] == "openai/m1" and kw["api_base"] == "https://llm.example/v1" and kw["api_key"] == "not-needed"


def test_anthropic_and_gemini():
    assert build_kwargs(cfg("anthropic", secrets={"api_key": "a"})) == {"model": "anthropic/m1", "api_key": "a"}
    assert build_kwargs(cfg("gemini", secrets={"api_key": "g"})) == {"model": "gemini/m1", "api_key": "g"}


def test_vertex_tenant_requires_and_passes_json():
    sa = json.dumps({"type": "service_account", "client_email": "x@y", "private_key": "k"})
    kw = build_kwargs(cfg("vertex", project="p", location="us-central1", secrets={"service_account_json": sa}))
    assert kw == {"model": "vertex_ai/m1", "vertex_project": "p", "vertex_location": "us-central1", "vertex_credentials": sa}
    assert "secrets.service_account_json" in missing_fields(cfg("vertex", project="p", location="l"))


def test_vertex_deployment_uses_adc():
    kw = build_kwargs(cfg("vertex", source="deployment", project="p", location="l"))
    assert "vertex_credentials" not in kw


def test_bedrock_chain_and_creds():
    assert build_kwargs(cfg("bedrock", region="us-east-1", auth_mode="chain")) == {"model": "bedrock/m1", "aws_region_name": "us-east-1"}
    kw = build_kwargs(cfg("bedrock", region="us-east-1", auth_mode="role", role_arn="arn:aws:iam::1:role/r"),
                      aws_creds={"aws_access_key_id": "A", "aws_secret_access_key": "S", "aws_session_token": "T"})
    assert kw["aws_access_key_id"] == "A" and kw["aws_session_token"] == "T"


def test_bedrock_tenant_mode_requirements():
    assert "role_arn" in missing_fields(cfg("bedrock", region="r", auth_mode="role"))
    m = missing_fields(cfg("bedrock", region="r", auth_mode="keys"))
    assert "secrets.access_key_id" in m and "secrets.secret_access_key" in m
    assert "auth_mode" in missing_fields(cfg("bedrock", region="r", auth_mode="chain"))  # tenants can't use the server chain


@pytest.mark.parametrize("provider,extra", [
    ("ollama", {}), ("openai", {}), ("anthropic", {}), ("gemini", {}), ("openai_compatible", {}),
    ("vertex", {"project": "p"}), ("bedrock", {}),
])
def test_missing_fields_raise(provider, extra):
    with pytest.raises(ConfigError):
        build_kwargs(cfg(provider, **extra))


def test_model_required():
    assert "model" in missing_fields(cfg("openai", model="", secrets={"api_key": "k"}))


def test_unknown_provider():
    with pytest.raises(ConfigError):
        build_kwargs(cfg("azure", secrets={"api_key": "k"}))


@pytest.mark.parametrize("model", ["meta-llama/Llama-3-70b", "us.anthropic.claude-3-5-sonnet-20241022-v2:0", "gpt-4o-mini"])
def test_model_id_passed_verbatim(model):
    kw = build_kwargs(cfg("openai_compatible", model=model, api_base="https://x/v1"))
    assert kw["model"] == f"openai/{model}"
    kw = build_kwargs(cfg("bedrock", model=model, region="us-east-1", auth_mode="chain", source="deployment"))
    assert kw["model"] == f"bedrock/{model}"


def test_invalid_vertex_json_is_config_error():
    with pytest.raises(ConfigError):
        build_kwargs(cfg("vertex", project="p", location="l", secrets={"service_account_json": "{not json"}))
```

- [ ] **Step 2: Run — expect FAIL.** `docker compose exec -T backend python -m pytest tests/test_ai_providers.py -q`

- [ ] **Step 3: Implement**

`backend/app/ai/providers.py`:
```python
"""Pure provider definitions: which fields each provider needs and the LiteLLM kwargs it gets."""
import json
from dataclasses import dataclass, field
from typing import Dict, List, Optional

PROVIDERS = ("ollama", "openai", "openai_compatible", "anthropic", "gemini", "vertex", "bedrock")

_PREFIX = {
    "ollama": "ollama_chat", "openai": "openai", "openai_compatible": "openai",
    "anthropic": "anthropic", "gemini": "gemini", "vertex": "vertex_ai", "bedrock": "bedrock",
}

SECRET_FIELDS: Dict[str, frozenset] = {
    "ollama": frozenset(),
    "openai": frozenset({"api_key", "organization"}),
    "openai_compatible": frozenset({"api_key"}),
    "anthropic": frozenset({"api_key"}),
    "gemini": frozenset({"api_key"}),
    "vertex": frozenset({"service_account_json"}),
    "bedrock": frozenset({"access_key_id", "secret_access_key", "session_token"}),
}
ALL_SECRET_FIELDS = frozenset().union(*SECRET_FIELDS.values())
SSRF_GUARDED = frozenset({"ollama", "openai_compatible"})


class ConfigError(ValueError):
    pass


@dataclass
class ProviderConfig:
    provider: str
    model: str
    source: str  # "tenant" | "deployment"
    tenant_id: Optional[int] = None
    api_base: Optional[str] = None
    region: Optional[str] = None
    project: Optional[str] = None
    location: Optional[str] = None
    auth_mode: Optional[str] = None  # bedrock: "chain" | "role" | "keys"
    role_arn: Optional[str] = None
    external_id: Optional[str] = None
    secrets: dict = field(default_factory=dict)


def missing_fields(cfg: ProviderConfig) -> List[str]:
    p, s = cfg.provider, cfg.secrets or {}
    missing: List[str] = []
    if not cfg.model:
        missing.append("model")
    if p in ("ollama", "openai_compatible") and not cfg.api_base:
        missing.append("api_base")
    if p in ("openai", "anthropic", "gemini") and not s.get("api_key"):
        missing.append("secrets.api_key")
    if p == "vertex":
        if not cfg.project:
            missing.append("project")
        if not cfg.location:
            missing.append("location")
        if cfg.source == "tenant" and not s.get("service_account_json"):
            missing.append("secrets.service_account_json")
    if p == "bedrock":
        if not cfg.region:
            missing.append("region")
        if cfg.source == "tenant":
            if cfg.auth_mode == "role":
                if not cfg.role_arn:
                    missing.append("role_arn")
            elif cfg.auth_mode == "keys":
                if not s.get("access_key_id"):
                    missing.append("secrets.access_key_id")
                if not s.get("secret_access_key"):
                    missing.append("secrets.secret_access_key")
            else:
                missing.append("auth_mode")
    return missing


def build_kwargs(cfg: ProviderConfig, aws_creds: Optional[dict] = None) -> dict:
    if cfg.provider not in PROVIDERS:
        raise ConfigError(f"unknown provider '{cfg.provider}'")
    missing = missing_fields(cfg)
    if missing:
        raise ConfigError("missing " + ", ".join(missing))
    p, s = cfg.provider, cfg.secrets or {}
    kw: dict = {"model": f"{_PREFIX[p]}/{cfg.model}"}
    if p in ("ollama", "openai_compatible"):
        kw["api_base"] = cfg.api_base
    if p in ("openai", "openai_compatible", "anthropic", "gemini") and s.get("api_key"):
        kw["api_key"] = s["api_key"]
    if p == "openai_compatible" and "api_key" not in kw:
        kw["api_key"] = "not-needed"  # LiteLLM's OpenAI client requires some key
    if p == "openai" and s.get("organization"):
        kw["organization"] = s["organization"]
    if p == "vertex":
        kw["vertex_project"] = cfg.project
        kw["vertex_location"] = cfg.location
        sa = s.get("service_account_json")
        if sa:
            try:
                parsed = json.loads(sa)
            except ValueError:
                raise ConfigError("service account JSON is not valid JSON")
            if not isinstance(parsed, dict) or "client_email" not in parsed:
                raise ConfigError("service account JSON must be a Google service-account key")
            kw["vertex_credentials"] = sa
    if p == "bedrock":
        kw["aws_region_name"] = cfg.region
        if aws_creds:
            kw.update({k: v for k, v in aws_creds.items() if v})
    return kw
```

- [ ] **Step 4: Run — expect PASS.**

- [ ] **Step 5: Commit**
```bash
git add backend/app/ai/providers.py backend/tests/test_ai_providers.py
git commit -m "feat(ai): provider definitions and LiteLLM kwargs builder"
```

---

### Task 3: Bedrock credentials (standard chain, assume-role, keys)

**Files:**
- Create: `backend/app/ai/aws.py`
- Test: `backend/tests/test_ai_aws.py`

**Interfaces:**
- Consumes: `ProviderConfig` (Task 2), `AIUnavailable` (Task 1).
- Produces: `async bedrock_credentials(cfg, *, now: Optional[datetime] = None, sts_factory=None) -> Optional[dict]` — `None` for the standard chain (deployment, or `auth_mode` "chain"); keys dict (`aws_access_key_id`, `aws_secret_access_key`, `aws_session_token?`) otherwise. `clear_cache(tenant_id: Optional[int] = None) -> None`. `deployment_principal_arn() -> Optional[str]` (best-effort `sts:GetCallerIdentity`, cached; `None` on error).

- [ ] **Step 1: Write the failing tests**

`backend/tests/test_ai_aws.py`:
```python
import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from app.ai import aws
from app.ai.errors import AIUnavailable
from app.ai.providers import ProviderConfig


class FakeSTS:
    def __init__(self, fail=None):
        self.calls = []
        self.fail = fail

    def assume_role(self, **kw):
        self.calls.append(kw)
        if self.fail:
            from botocore.exceptions import ClientError
            raise ClientError({"Error": {"Code": self.fail, "Message": "nope"}}, "AssumeRole")
        return {"Credentials": {"AccessKeyId": "ASIAXXX", "SecretAccessKey": "sec", "SessionToken": "tok",
                                "Expiration": datetime(2030, 1, 1, 12, 0, tzinfo=timezone.utc)}}


def role_cfg(**kw):
    base = dict(provider="bedrock", model="m", source="tenant", tenant_id=7, region="us-east-1",
                auth_mode="role", role_arn="arn:aws:iam::123:role/r", external_id="ext-1")
    base.update(kw)
    return ProviderConfig(**base)


def run(coro):
    return asyncio.run(coro)


def setup_function():
    aws.clear_cache()


def test_deployment_and_chain_use_no_keys():
    assert run(aws.bedrock_credentials(ProviderConfig("bedrock", "m", "deployment", region="r", auth_mode="chain"))) is None


def test_keys_mode():
    cfg = ProviderConfig("bedrock", "m", "tenant", tenant_id=1, region="r", auth_mode="keys",
                         secrets={"access_key_id": "AKIA1", "secret_access_key": "S1"})
    assert run(aws.bedrock_credentials(cfg)) == {"aws_access_key_id": "AKIA1", "aws_secret_access_key": "S1"}


def test_role_mode_assumes_with_external_id_and_caches():
    sts = FakeSTS()
    now = datetime(2030, 1, 1, 11, 0, tzinfo=timezone.utc)
    c1 = run(aws.bedrock_credentials(role_cfg(), now=now, sts_factory=lambda region: sts))
    c2 = run(aws.bedrock_credentials(role_cfg(), now=now + timedelta(minutes=30), sts_factory=lambda region: sts))
    assert c1 == c2 == {"aws_access_key_id": "ASIAXXX", "aws_secret_access_key": "sec", "aws_session_token": "tok"}
    assert len(sts.calls) == 1
    call = sts.calls[0]
    assert call["RoleArn"] == "arn:aws:iam::123:role/r" and call["ExternalId"] == "ext-1"
    assert call["RoleSessionName"] == "sochub-tenant-7" and call["DurationSeconds"] == 3600


def test_role_mode_refreshes_five_minutes_before_expiry():
    sts = FakeSTS()
    near = datetime(2030, 1, 1, 11, 56, tzinfo=timezone.utc)  # 4 min before expiry
    run(aws.bedrock_credentials(role_cfg(), now=near - timedelta(hours=1), sts_factory=lambda r: sts))
    run(aws.bedrock_credentials(role_cfg(), now=near, sts_factory=lambda r: sts))
    assert len(sts.calls) == 2


def test_role_change_is_a_new_cache_key():
    sts = FakeSTS()
    now = datetime(2030, 1, 1, 11, 0, tzinfo=timezone.utc)
    run(aws.bedrock_credentials(role_cfg(), now=now, sts_factory=lambda r: sts))
    run(aws.bedrock_credentials(role_cfg(role_arn="arn:aws:iam::123:role/other"), now=now, sts_factory=lambda r: sts))
    assert len(sts.calls) == 2


def test_assume_failure_is_unavailable_with_code():
    with pytest.raises(AIUnavailable, match="AccessDenied"):
        run(aws.bedrock_credentials(role_cfg(), sts_factory=lambda r: FakeSTS(fail="AccessDenied")))


def test_clear_cache_per_tenant():
    sts = FakeSTS()
    now = datetime(2030, 1, 1, 11, 0, tzinfo=timezone.utc)
    run(aws.bedrock_credentials(role_cfg(), now=now, sts_factory=lambda r: sts))
    aws.clear_cache(7)
    run(aws.bedrock_credentials(role_cfg(), now=now, sts_factory=lambda r: sts))
    assert len(sts.calls) == 2
```

- [ ] **Step 2: Run — expect FAIL.**

- [ ] **Step 3: Implement**

`backend/app/ai/aws.py`:
```python
"""Bedrock credentials.

Deployment default (and auth_mode "chain"): pass no keys — boto3's standard
credential chain applies (env → shared config/profile → web identity/IRSA →
ECS task role → EC2 instance profile).
Tenant "role": the server (using its chain credentials) calls sts:AssumeRole
into the tenant's account with an ExternalId; temporary credentials are cached
until 5 minutes before they expire. Tenant "keys": stored access keys.
"""
import asyncio
from datetime import datetime, timedelta, timezone
from typing import Callable, Dict, Optional, Tuple

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from app.ai.errors import AIUnavailable
from app.ai.providers import ProviderConfig

_REFRESH_MARGIN = timedelta(minutes=5)
_DURATION_SECONDS = 3600
_cache: Dict[Tuple[int, str, str], Tuple[dict, datetime]] = {}
_principal: Optional[str] = None


def clear_cache(tenant_id: Optional[int] = None) -> None:
    if tenant_id is None:
        _cache.clear()
        return
    for key in [k for k in _cache if k[0] == tenant_id]:
        del _cache[key]


def _default_sts(region: Optional[str]):
    return boto3.client("sts", region_name=region) if region else boto3.client("sts")


async def bedrock_credentials(cfg: ProviderConfig, *, now: Optional[datetime] = None,
                              sts_factory: Optional[Callable] = None) -> Optional[dict]:
    mode = cfg.auth_mode or "chain"
    if cfg.source == "deployment" or mode == "chain":
        return None
    if mode == "keys":
        s = cfg.secrets or {}
        creds = {"aws_access_key_id": s.get("access_key_id"), "aws_secret_access_key": s.get("secret_access_key")}
        if s.get("session_token"):
            creds["aws_session_token"] = s["session_token"]
        return creds
    if mode != "role":
        raise AIUnavailable(f"unknown Bedrock auth mode '{mode}'")

    now = now or datetime.now(timezone.utc)
    key = (cfg.tenant_id or 0, cfg.role_arn or "", cfg.external_id or "")
    hit = _cache.get(key)
    if hit and hit[1] - _REFRESH_MARGIN > now:
        return dict(hit[0])

    factory = sts_factory or _default_sts

    def _assume():
        client = factory(cfg.region)
        kwargs = {"RoleArn": cfg.role_arn, "RoleSessionName": f"sochub-tenant-{cfg.tenant_id}",
                  "DurationSeconds": _DURATION_SECONDS}
        if cfg.external_id:
            kwargs["ExternalId"] = cfg.external_id
        return client.assume_role(**kwargs)["Credentials"]

    try:
        c = await asyncio.to_thread(_assume)
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "ClientError")
        raise AIUnavailable(f"Bedrock role assumption failed: {code}")
    except BotoCoreError as e:
        raise AIUnavailable(f"Bedrock role assumption failed: {type(e).__name__}")
    creds = {"aws_access_key_id": c["AccessKeyId"], "aws_secret_access_key": c["SecretAccessKey"],
             "aws_session_token": c["SessionToken"]}
    _cache[key] = (creds, c["Expiration"])
    return dict(creds)


async def deployment_principal_arn() -> Optional[str]:
    """Best-effort ARN of the server's own AWS identity, for the trust-policy snippet."""
    global _principal
    if _principal:
        return _principal
    try:
        ident = await asyncio.to_thread(lambda: _default_sts(None).get_caller_identity())
        _principal = ident.get("Arn")
    except Exception:
        return None
    return _principal
```

- [ ] **Step 4: Run — expect PASS.**

- [ ] **Step 5: Commit**
```bash
git add backend/app/ai/aws.py backend/tests/test_ai_aws.py
git commit -m "feat(ai): Bedrock credentials via standard chain, assume-role or keys"
```

---

### Task 4: LiteLLM wrapper — `complete_with`, `test_connection`, lockdown

**Files:**
- Create: `backend/app/ai/llm.py`
- Test: `backend/tests/test_ai_llm.py`

**Interfaces:**
- Consumes: `ProviderConfig`, `build_kwargs`, `ConfigError`, `SSRF_GUARDED` (Task 2); `bedrock_credentials` (Task 3); `AIError`, `AIUnavailable`, `scrub` (Task 1); `assert_url_allowed`, `SSRFError` (`app/workflows/ssrf.py`).
- Produces: `async complete_with(cfg, messages, *, temperature=None, json_mode=False, max_tokens=None, timeout=None, allowlist: Sequence[str] = ()) -> str`; `async test_connection(cfg, *, allowlist=()) -> tuple[bool, str]`. (`complete()` that resolves config by tenant is added in Task 5.)

- [ ] **Step 1: Write the failing tests**

`backend/tests/test_ai_llm.py`:
```python
import asyncio
from types import SimpleNamespace

import litellm
import pytest

from app.ai import llm
from app.ai.errors import AIError, AIUnavailable
from app.ai.providers import ProviderConfig


def resp(text):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text))])


@pytest.fixture
def calls(monkeypatch):
    seen = []

    async def fake(**kw):
        seen.append(kw)
        return resp("hi")
    monkeypatch.setattr(llm.litellm, "acompletion", fake)
    monkeypatch.setattr(llm, "assert_url_allowed", lambda url, allow: None)
    return seen


def run(c):
    return asyncio.run(c)


OPENAI = ProviderConfig("openai", "gpt-4o-mini", "tenant", tenant_id=1, secrets={"api_key": "sk-test-1234567890"})


def test_lockdown_flags():
    assert litellm.telemetry is False
    assert litellm.drop_params is True
    assert litellm.callbacks == [] and litellm.success_callback == [] and litellm.failure_callback == []


def test_complete_returns_text_and_passes_options(calls):
    out = run(llm.complete_with(OPENAI, [{"role": "user", "content": "x"}], temperature=0, json_mode=True))
    assert out == "hi"
    kw = calls[0]
    assert kw["model"] == "openai/gpt-4o-mini" and kw["api_key"] == "sk-test-1234567890"
    assert kw["temperature"] == 0 and kw["response_format"] == {"type": "json_object"}
    assert kw["timeout"] == 120


def test_no_temperature_or_json_when_not_requested(calls):
    run(llm.complete_with(OPENAI, [{"role": "user", "content": "x"}]))
    assert "temperature" not in calls[0] and "response_format" not in calls[0]


@pytest.mark.parametrize("exc", ["AuthenticationError", "RateLimitError", "APIConnectionError", "Timeout"])
def test_provider_errors_become_unavailable_and_scrubbed(monkeypatch, exc):
    cls = getattr(litellm, exc)

    async def boom(**kw):
        raise cls(message="bad key sk-test-1234567890", llm_provider="openai", model="gpt-4o-mini")
    monkeypatch.setattr(llm.litellm, "acompletion", boom)
    with pytest.raises(AIUnavailable) as e:
        run(llm.complete_with(OPENAI, [{"role": "user", "content": "x"}]))
    assert "sk-test-1234567890" not in str(e.value)


def test_other_errors_become_ai_error(monkeypatch):
    async def boom(**kw):
        raise RuntimeError("weird")
    monkeypatch.setattr(llm.litellm, "acompletion", boom)
    with pytest.raises(AIError):
        run(llm.complete_with(OPENAI, [{"role": "user", "content": "x"}]))


def test_misconfig_is_unavailable(calls):
    with pytest.raises(AIUnavailable, match="misconfigured"):
        run(llm.complete_with(ProviderConfig("openai", "m", "tenant"), [{"role": "user", "content": "x"}]))
    assert calls == []


def test_tenant_api_base_ssrf_checked_at_call(monkeypatch, calls):
    from app.workflows.ssrf import SSRFError

    def deny(url, allow):
        raise SSRFError("10.0.0.5 is private; add it to the HTTP allowlist")
    monkeypatch.setattr(llm, "assert_url_allowed", deny)
    cfg = ProviderConfig("ollama", "llama3", "tenant", tenant_id=1, api_base="http://10.0.0.5:11434")
    with pytest.raises(AIUnavailable, match="allowlist"):
        run(llm.complete_with(cfg, [{"role": "user", "content": "x"}]))
    assert calls == []


def test_deployment_api_base_not_ssrf_checked(monkeypatch, calls):
    def deny(url, allow):
        raise AssertionError("must not be called for deployment config")
    monkeypatch.setattr(llm, "assert_url_allowed", deny)
    cfg = ProviderConfig("ollama", "llama3", "deployment", api_base="http://ollama:11434")
    assert run(llm.complete_with(cfg, [{"role": "user", "content": "x"}])) == "hi"


def test_bedrock_role_creds_passed(monkeypatch, calls):
    async def creds(cfg, **kw):
        return {"aws_access_key_id": "ASIA1", "aws_secret_access_key": "S", "aws_session_token": "T"}
    monkeypatch.setattr(llm, "bedrock_credentials", creds)
    cfg = ProviderConfig("bedrock", "anthropic.claude-v2", "tenant", tenant_id=1, region="us-east-1",
                         auth_mode="role", role_arn="arn:aws:iam::1:role/r")
    run(llm.complete_with(cfg, [{"role": "user", "content": "x"}]))
    assert calls[0]["aws_session_token"] == "T" and calls[0]["aws_region_name"] == "us-east-1"


def test_test_connection(calls):
    ok, msg = run(llm.test_connection(OPENAI))
    assert ok and "openai" in msg
    assert calls[0]["max_tokens"] == 5 and calls[0]["timeout"] == 30
    assert calls[0]["messages"] == [{"role": "user", "content": "Reply with OK"}]


def test_test_connection_failure(monkeypatch):
    async def boom(**kw):
        raise litellm.AuthenticationError(message="invalid", llm_provider="openai", model="m")
    monkeypatch.setattr(llm.litellm, "acompletion", boom)
    ok, msg = run(llm.test_connection(OPENAI))
    assert not ok and msg
```
(If a LiteLLM exception constructor in the pinned version takes different args, adapt the test's construction only — keep the class list. Check with `docker compose exec -T backend python -c "import litellm, inspect; print(inspect.signature(litellm.AuthenticationError))"`.)

- [ ] **Step 2: Run — expect FAIL.**

- [ ] **Step 3: Implement**

`backend/app/ai/llm.py`:
```python
"""Single entry point for LLM calls (LiteLLM), with provider-agnostic errors."""
import asyncio
import logging
from typing import List, Optional, Sequence, Tuple

import litellm

from app.ai.aws import bedrock_credentials
from app.ai.errors import AIError, AIUnavailable, scrub
from app.ai.providers import SSRF_GUARDED, ConfigError, ProviderConfig, build_kwargs
from app.core.config import settings
from app.workflows.ssrf import SSRFError, assert_url_allowed

logger = logging.getLogger(__name__)

# Lockdown: prompts contain case data — nothing may be sent anywhere but the chosen provider.
litellm.telemetry = False
litellm.callbacks = []
litellm.success_callback = []
litellm.failure_callback = []
litellm.suppress_debug_info = True
litellm.drop_params = True  # providers without JSON mode / temperature just ignore them

_UNAVAILABLE = tuple(
    getattr(litellm, name) for name in (
        "AuthenticationError", "PermissionDeniedError", "RateLimitError", "APIConnectionError",
        "Timeout", "ServiceUnavailableError", "NotFoundError", "BadRequestError",
        "ContextWindowExceededError", "InternalServerError",
    ) if hasattr(litellm, name)
)


async def complete_with(cfg: ProviderConfig, messages: List[dict], *, temperature: Optional[float] = None,
                        json_mode: bool = False, max_tokens: Optional[int] = None,
                        timeout: Optional[float] = None, allowlist: Sequence[str] = ()) -> str:
    secrets = list((cfg.secrets or {}).values())
    if cfg.source == "tenant" and cfg.provider in SSRF_GUARDED and cfg.api_base:
        try:
            await asyncio.to_thread(assert_url_allowed, cfg.api_base, list(allowlist))
        except SSRFError as e:
            raise AIUnavailable(f"AI endpoint not allowed: {e}")
    aws_creds = await bedrock_credentials(cfg) if cfg.provider == "bedrock" else None
    try:
        kwargs = build_kwargs(cfg, aws_creds)
    except ConfigError as e:
        raise AIUnavailable(f"AI provider misconfigured: {e}")
    if temperature is not None:
        kwargs["temperature"] = temperature
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    if max_tokens is not None:
        kwargs["max_tokens"] = max_tokens
    kwargs["timeout"] = timeout if timeout is not None else settings.AI_TIMEOUT_SECONDS
    try:
        resp = await litellm.acompletion(messages=messages, **kwargs)
    except _UNAVAILABLE as e:
        raise AIUnavailable(scrub(f"{type(e).__name__}: {e}", secrets))
    except Exception as e:
        logger.warning("AI provider call failed (%s/%s): %s", cfg.provider, cfg.model, type(e).__name__)
        raise AIError(scrub(f"{type(e).__name__}: {e}", secrets))
    try:
        return resp.choices[0].message.content or ""
    except (AttributeError, IndexError):
        raise AIError("AI provider returned an empty response")


async def test_connection(cfg: ProviderConfig, *, allowlist: Sequence[str] = ()) -> Tuple[bool, str]:
    try:
        await complete_with(cfg, [{"role": "user", "content": "Reply with OK"}],
                            max_tokens=5, timeout=30, allowlist=allowlist)
    except AIError as e:
        return False, str(e)
    return True, f"Connected — {cfg.provider} / {cfg.model} replied."
```

- [ ] **Step 4: Run — expect PASS**, then the full suite.

- [ ] **Step 5: Commit**
```bash
git add backend/app/ai/llm.py backend/tests/test_ai_llm.py
git commit -m "feat(ai): LiteLLM wrapper with lockdown, error mapping and SSRF check"
```

---

### Task 5: Tenant config storage, resolution and `complete()`

**Files:**
- Create: `backend/app/models/tenant_ai_config.py`, `backend/alembic/versions/e3f4a5b6c7d8_tenant_ai_configs.py`, `backend/app/ai/config.py`
- Modify: `backend/app/db/base.py` (register model), `backend/app/ai/llm.py` (add `complete`, `describe`)
- Test: `backend/tests/test_ai_config.py`

**Interfaces:**
- Consumes: Tasks 1–4; `encrypt`/`decrypt` (`app/utils/crypto.py`); `AsyncSessionLocal` (`app/db/session.py`); `Tenant.workflow_http_allowlist`.
- Produces:
  - model `TenantAIConfig` (columns per spec Data model).
  - `deployment_config() -> ProviderConfig`
  - `row_to_config(row: TenantAIConfig) -> ProviderConfig` (raises `AIUnavailable` on `InvalidToken`)
  - `async resolve_config(db, tenant_id: Optional[int]) -> ProviderConfig` (60 s cache)
  - `invalidate(tenant_id: int) -> None`
  - `async tenant_allowlist(db, tenant_id) -> list[str]`
  - in `llm.py`: `async complete(messages, *, tenant_id: Optional[int], temperature=None, json_mode=False) -> str`; `async describe(db, tenant_id) -> dict` → `{"provider","model","source"}`

- [ ] **Step 1: Model + migration**

`backend/app/models/tenant_ai_config.py`:
```python
from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.sql import func

from app.db.base_class import Base


class TenantAIConfig(Base):
    """A tenant's AI-provider override. Secrets live Fernet-encrypted in credentials_enc (JSON)."""
    __tablename__ = "tenant_ai_configs"

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, unique=True)
    enabled = Column(Boolean, nullable=False, default=True)
    provider = Column(String, nullable=False)
    model = Column(String, nullable=False)
    api_base = Column(String, nullable=True)
    region = Column(String, nullable=True)
    project = Column(String, nullable=True)
    location = Column(String, nullable=True)
    auth_mode = Column(String, nullable=True)
    role_arn = Column(String, nullable=True)
    external_id = Column(String, nullable=True)
    credentials_enc = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
```
Register in `backend/app/db/base.py`: `from app.models.tenant_ai_config import TenantAIConfig  # noqa: F401`.

Confirm the head first: `docker compose exec -T backend alembic heads` (expected `d2e3f4a5b6c7`; use the printed head as `down_revision` if different).

`backend/alembic/versions/e3f4a5b6c7d8_tenant_ai_configs.py`:
```python
"""tenant_ai_configs

Revision ID: e3f4a5b6c7d8
Revises: d2e3f4a5b6c7
"""
import sqlalchemy as sa
from alembic import op

revision = "e3f4a5b6c7d8"
down_revision = "d2e3f4a5b6c7"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "tenant_ai_configs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("provider", sa.String(), nullable=False),
        sa.Column("model", sa.String(), nullable=False),
        sa.Column("api_base", sa.String()),
        sa.Column("region", sa.String()),
        sa.Column("project", sa.String()),
        sa.Column("location", sa.String()),
        sa.Column("auth_mode", sa.String()),
        sa.Column("role_arn", sa.String()),
        sa.Column("external_id", sa.String()),
        sa.Column("credentials_enc", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_tenant_ai_configs_id", "tenant_ai_configs", ["id"])


def downgrade():
    op.drop_table("tenant_ai_configs")
```
Run: `docker compose exec -T backend alembic upgrade head && docker compose exec -T backend alembic downgrade -1 && docker compose exec -T backend alembic upgrade head` — all succeed.

- [ ] **Step 2: Write the failing tests**

`backend/tests/test_ai_config.py`:
```python
import asyncio
import json
import uuid

import pytest
from sqlalchemy import delete

from app.ai import config as aicfg, llm
from app.ai.errors import AIUnavailable
from app.core.config import settings
from app.db.session import AsyncSessionLocal, engine
from app.models.tenant import Tenant
from app.models.tenant_ai_config import TenantAIConfig
from app.utils.crypto import encrypt


def test_deployment_default_is_legacy_ollama(monkeypatch):
    for k in ("AI_PROVIDER", "AI_MODEL", "AI_API_BASE", "AI_API_KEY"):
        monkeypatch.setattr(settings, k, None)
    c = aicfg.deployment_config()
    assert (c.provider, c.model, c.api_base, c.source) == ("ollama", settings.OLLAMA_MODEL, settings.OLLAMA_BASE_URL, "deployment")


def test_deployment_explicit(monkeypatch):
    monkeypatch.setattr(settings, "AI_PROVIDER", "anthropic")
    monkeypatch.setattr(settings, "AI_MODEL", "claude-x")
    monkeypatch.setattr(settings, "AI_API_KEY", "k")
    c = aicfg.deployment_config()
    assert (c.provider, c.model, c.secrets) == ("anthropic", "claude-x", {"api_key": "k"})


def test_deployment_bedrock_region_and_chain(monkeypatch):
    monkeypatch.setattr(settings, "AI_PROVIDER", "bedrock")
    monkeypatch.setattr(settings, "AI_MODEL", "m")
    monkeypatch.setattr(settings, "AI_AWS_REGION", "eu-west-1")
    c = aicfg.deployment_config()
    assert c.region == "eu-west-1" and c.auth_mode == "chain"


def test_complete_unconfigured_deployment_is_unavailable(monkeypatch):
    monkeypatch.setattr(settings, "AI_PROVIDER", "openai")
    monkeypatch.setattr(settings, "AI_MODEL", "gpt-4o-mini")
    monkeypatch.setattr(settings, "AI_API_KEY", None)
    aicfg.invalidate_all()
    with pytest.raises(AIUnavailable, match="misconfigured"):
        asyncio.run(llm.complete([{"role": "user", "content": "x"}], tenant_id=None))


def test_row_to_config_decrypts_and_bad_token_is_unavailable():
    row = TenantAIConfig(tenant_id=1, provider="openai", model="m", enabled=True,
                         credentials_enc=encrypt(json.dumps({"api_key": "k1"})))
    assert aicfg.row_to_config(row).secrets == {"api_key": "k1"}
    row.credentials_enc = "garbage"
    with pytest.raises(AIUnavailable, match="re-enter"):
        aicfg.row_to_config(row)


def test_resolution_override_cache_and_no_fallback(monkeypatch):
    async def scenario():
        slug = f"wf-test-{uuid.uuid4().hex[:8]}"
        async with AsyncSessionLocal() as db:
            t = Tenant(name=slug, slug=slug)
            db.add(t)
            await db.commit()
            tid = t.id
        try:
            aicfg.invalidate_all()
            async with AsyncSessionLocal() as db:
                assert (await aicfg.resolve_config(db, tid)).source == "deployment"
                row = TenantAIConfig(tenant_id=tid, provider="anthropic", model="c", enabled=True,
                                     credentials_enc=encrypt(json.dumps({"api_key": "tk"})))
                db.add(row)
                await db.commit()
                # cached for 60 s -> still deployment until invalidated
                assert (await aicfg.resolve_config(db, tid)).source == "deployment"
                aicfg.invalidate(tid)
                cfg = await aicfg.resolve_config(db, tid)
                assert (cfg.source, cfg.provider, cfg.secrets) == ("tenant", "anthropic", {"api_key": "tk"})
                # override disabled by deployment -> deployment config
                monkeypatch.setattr(settings, "AI_ALLOW_TENANT_OVERRIDE", False)
                aicfg.invalidate(tid)
                assert (await aicfg.resolve_config(db, tid)).source == "deployment"
                monkeypatch.setattr(settings, "AI_ALLOW_TENANT_OVERRIDE", True)
                aicfg.invalidate(tid)
            # no fallback: tenant provider failure must not call the deployment provider
            seen = []

            async def fail(**kw):
                seen.append(kw["model"])
                raise __import__("litellm").AuthenticationError(message="bad", llm_provider="anthropic", model="c")
            monkeypatch.setattr(llm.litellm, "acompletion", fail)
            with pytest.raises(AIUnavailable):
                await llm.complete([{"role": "user", "content": "x"}], tenant_id=tid)
            assert seen == ["anthropic/c"]
        finally:
            async with AsyncSessionLocal() as db:
                await db.execute(delete(TenantAIConfig).where(TenantAIConfig.tenant_id == tid))
                await db.execute(delete(Tenant).where(Tenant.id == tid))
                await db.commit()
            aicfg.invalidate_all()
            await engine.dispose()
    asyncio.run(scenario())
```

- [ ] **Step 3: Run — expect FAIL.**

- [ ] **Step 4: Implement `app/ai/config.py`**

```python
"""Which AI provider config applies: tenant override or deployment default."""
import json
import os
import time
from typing import Dict, List, Optional, Tuple

from cryptography.fernet import InvalidToken
from sqlalchemy import select

from app.ai.errors import AIUnavailable
from app.ai.providers import ProviderConfig
from app.core.config import settings
from app.models.tenant import Tenant
from app.models.tenant_ai_config import TenantAIConfig
from app.utils.crypto import decrypt

_TTL_SECONDS = 60.0
_cache: Dict[Optional[int], Tuple[float, ProviderConfig]] = {}


def invalidate(tenant_id: int) -> None:
    _cache.pop(tenant_id, None)


def invalidate_all() -> None:
    _cache.clear()


def deployment_config() -> ProviderConfig:
    provider = (settings.AI_PROVIDER or "ollama").lower()
    is_ollama = provider == "ollama"
    model = settings.AI_MODEL or (settings.OLLAMA_MODEL if is_ollama else "")
    api_base = settings.AI_API_BASE or (settings.OLLAMA_BASE_URL if is_ollama else None)
    region = settings.AI_AWS_REGION or os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION")
    secrets = {"api_key": settings.AI_API_KEY} if settings.AI_API_KEY else {}
    return ProviderConfig(provider=provider, model=model, source="deployment", api_base=api_base,
                          region=region, project=settings.VERTEX_PROJECT, location=settings.VERTEX_LOCATION,
                          auth_mode="chain", secrets=secrets)


def decrypt_secrets(enc: Optional[str]) -> dict:
    if not enc:
        return {}
    try:
        return json.loads(decrypt(enc))
    except (InvalidToken, ValueError):
        raise AIUnavailable("AI credentials can't be decrypted (SECRET_KEY changed?) — re-enter them in Integrations")


def row_to_config(row: TenantAIConfig) -> ProviderConfig:
    return ProviderConfig(provider=row.provider, model=row.model, source="tenant", tenant_id=row.tenant_id,
                          api_base=row.api_base, region=row.region, project=row.project, location=row.location,
                          auth_mode=row.auth_mode, role_arn=row.role_arn, external_id=row.external_id,
                          secrets=decrypt_secrets(row.credentials_enc))


async def resolve_config(db, tenant_id: Optional[int]) -> ProviderConfig:
    hit = _cache.get(tenant_id)
    if hit and time.monotonic() - hit[0] < _TTL_SECONDS:
        return hit[1]
    cfg = deployment_config()
    if tenant_id is not None and settings.AI_ALLOW_TENANT_OVERRIDE:
        row = (await db.execute(select(TenantAIConfig).where(TenantAIConfig.tenant_id == tenant_id))).scalars().first()
        if row and row.enabled:
            cfg = row_to_config(row)
    _cache[tenant_id] = (time.monotonic(), cfg)
    return cfg


async def tenant_allowlist(db, tenant_id: Optional[int]) -> List[str]:
    if tenant_id is None:
        return []
    t = (await db.execute(select(Tenant).where(Tenant.id == tenant_id))).scalars().first()
    return list((t.workflow_http_allowlist if t else None) or [])
```

Append to `backend/app/ai/llm.py`:
```python
async def complete(messages: List[dict], *, tenant_id: Optional[int], temperature: Optional[float] = None,
                   json_mode: bool = False) -> str:
    from app.ai.config import resolve_config, tenant_allowlist
    from app.db.session import AsyncSessionLocal
    async with AsyncSessionLocal() as db:
        cfg = await resolve_config(db, tenant_id)
        allow = await tenant_allowlist(db, tenant_id) if cfg.source == "tenant" else []
    return await complete_with(cfg, messages, temperature=temperature, json_mode=json_mode, allowlist=allow)


async def describe(db, tenant_id: Optional[int]) -> dict:
    from app.ai.config import resolve_config
    try:
        cfg = await resolve_config(db, tenant_id)
    except AIUnavailable:
        return {"provider": None, "model": None, "source": "tenant"}
    return {"provider": cfg.provider, "model": cfg.model, "source": cfg.source}
```

- [ ] **Step 5: Run — expect PASS**; full suite; confirm `select count(*) from tenants where slug like 'wf-test-%'` is 0.

- [ ] **Step 6: Commit**
```bash
git add backend/app/models/tenant_ai_config.py backend/app/db/base.py backend/alembic/versions/e3f4a5b6c7d8_tenant_ai_configs.py backend/app/ai backend/tests/test_ai_config.py
git commit -m "feat(ai): tenant AI config table, resolution with cache, complete()"
```

---

### Task 6: Admin API — `/api/v1/ai/config`

**Files:**
- Create: `backend/app/schemas/ai_config.py`, `backend/app/api/v1/ai_config.py`
- Modify: `backend/app/api/api.py` (register `ai_config.router` with `prefix="/ai"`, `tags=["ai"]`)
- Test: `backend/tests/test_ai_api.py`

**Interfaces:**
- Consumes: Tasks 1–5; `deps.require_admin`, `deps.get_current_active_user`, `deps.get_effective_tenant_id`, `deps.get_db`; `create_audit_log`; `encrypt`; `assert_url_allowed`/`SSRFError`; `aws.clear_cache`, `aws.deployment_principal_arn`.
- Produces: `GET /ai/config`, `PUT /ai/config`, `POST /ai/config/test`, `DELETE /ai/config`, `GET /ai/info`.

- [ ] **Step 1: Schemas**

`backend/app/schemas/ai_config.py`:
```python
from typing import Literal, Optional

from pydantic import BaseModel, Field

Provider = Literal["ollama", "openai", "openai_compatible", "anthropic", "gemini", "vertex", "bedrock"]


class AIConfigIn(BaseModel):
    provider: Provider
    model: str = Field(min_length=1, max_length=200)
    enabled: bool = True
    api_base: Optional[str] = Field(None, max_length=500)
    region: Optional[str] = Field(None, max_length=50)
    project: Optional[str] = Field(None, max_length=200)
    location: Optional[str] = Field(None, max_length=100)
    auth_mode: Optional[Literal["role", "keys"]] = None
    role_arn: Optional[str] = Field(None, max_length=300)
    # secrets — write-only; blank/omitted keeps the stored value (unless the provider changes)
    api_key: Optional[str] = Field(None, max_length=500)
    organization: Optional[str] = Field(None, max_length=200)
    access_key_id: Optional[str] = Field(None, max_length=200)
    secret_access_key: Optional[str] = Field(None, max_length=500)
    session_token: Optional[str] = Field(None, max_length=4000)
    service_account_json: Optional[str] = Field(None, max_length=20000)
```

- [ ] **Step 2: Write the failing tests**

`backend/tests/test_ai_api.py` — follow `backend/tests/test_wf_api.py`'s in-process pattern (single `asyncio.run`, `httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")`, `app.dependency_overrides` for `deps.get_current_active_user`, `deps.require_admin`, `deps.get_effective_tenant_id`; temp tenants `wf-test-<hex>`; delete created rows by id in `finally`; `app.dependency_overrides.clear()`; `aicfg.invalidate_all()`). Monkeypatch `app.api.v1.ai_config.test_connection` to an async stub returning `(True, "ok")`. Scenario checks, each its own assertion block (name the sub-checks in comments so failures are obvious):

```python
# a) GET with no row -> source "deployment", override_allowed True, secrets_set all False
# b) PUT openai {model, api_key:"sk-abc12345678"} -> 200; GET shows source "tenant", secrets_set.api_key True,
#    and the response JSON text does NOT contain "sk-abc12345678"
# c) PUT openai {model:"gpt-4o"} (no api_key) -> keeps the stored key (secrets_set.api_key True)
# d) test_put_provider_change_discards_old_secrets: PUT anthropic {model:"claude"} with NO api_key
#    -> 200, secrets_set.api_key False (the OpenAI key was discarded, never reused for Anthropic)
# e) test_put_rejects_invalid_vertex_json: PUT vertex {model, project, location, service_account_json:"{bad"} -> 422
# f) test_put_internal_api_base_requires_allowlist: PUT ollama {model, api_base:"http://127.0.0.1:11434"} -> 422 with
#    "allowlist" in detail; then PUT /workflows/settings/http-allowlist {"hosts":["127.0.0.1"]} as admin and retry -> 200
# g) PUT bedrock {model, region, auth_mode:"role"} (no role_arn) -> 200, enabled False, external_id generated
#    (non-empty), and GET returns the same external_id on the next call; then PUT with role_arn -> enabled True
# h) POST /ai/config/test -> {"ok": true, "message": "ok"} (stub)
# i) DELETE -> 204; GET -> source "deployment"
# j) AI_ALLOW_TENANT_OVERRIDE False (monkeypatch settings) -> PUT 403; GET override_allowed False
# k) viewer (require_admin override raising HTTPException(403)) -> PUT 403, GET 403; GET /ai/info as viewer -> 200
#    with {"provider","model","source"}
# l) audit rows for entity_type "ai_config" exist and their `changes` JSON contains no secret values
```

Write each as concrete code in the test (requests via the client, `assert r.status_code == ...`, JSON field assertions); query audit rows with `select(AuditLog).where(AuditLog.entity_type == "ai_config", AuditLog.tenant_id == tid)` and assert `"sk-abc12345678" not in json.dumps(row.changes)`.

- [ ] **Step 3: Run — expect FAIL.**

- [ ] **Step 4: Implement**

`backend/app/api/v1/ai_config.py`:
```python
import asyncio
import json
import secrets as pysecrets
from typing import Any, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai import aws
from app.ai.config import decrypt_secrets, deployment_config, invalidate, row_to_config, tenant_allowlist
from app.ai.errors import AIUnavailable
from app.ai.llm import describe, test_connection
from app.ai.providers import ALL_SECRET_FIELDS, SECRET_FIELDS, SSRF_GUARDED, ConfigError, ProviderConfig, build_kwargs, missing_fields
from app.api import deps
from app.core.config import settings
from app.models.tenant_ai_config import TenantAIConfig
from app.models.user import User
from app.schemas.ai_config import AIConfigIn
from app.utils.audit import create_audit_log
from app.utils.crypto import encrypt
from app.workflows.ssrf import SSRFError, assert_url_allowed

router = APIRouter()
_PLAIN_FIELDS = ("provider", "model", "enabled", "api_base", "region", "project", "location", "auth_mode", "role_arn")


async def _row(db, tenant_id) -> Optional[TenantAIConfig]:
    return (await db.execute(select(TenantAIConfig).where(TenantAIConfig.tenant_id == tenant_id))).scalars().first()


def _out(row: Optional[TenantAIConfig], principal: Optional[str]) -> dict:
    dep = deployment_config()
    base = {"override_allowed": settings.AI_ALLOW_TENANT_OVERRIDE,
            "deployment": {"provider": dep.provider, "model": dep.model},
            "deployment_principal_arn": principal}
    if not row:
        return {**base, "source": "deployment", "provider": dep.provider, "model": dep.model, "enabled": False,
                "api_base": None, "region": None, "project": None, "location": None, "auth_mode": None,
                "role_arn": None, "external_id": None, "secrets_set": {f: False for f in sorted(ALL_SECRET_FIELDS)}}
    try:
        stored = decrypt_secrets(row.credentials_enc)
    except AIUnavailable:
        stored = {}
    return {**base, "source": "tenant" if (row.enabled and settings.AI_ALLOW_TENANT_OVERRIDE) else "deployment",
            **{f: getattr(row, f) for f in _PLAIN_FIELDS}, "external_id": row.external_id,
            "secrets_set": {f: bool(stored.get(f)) for f in sorted(ALL_SECRET_FIELDS)}}


def _merge_secrets(body: AIConfigIn, row: Optional[TenantAIConfig]) -> dict:
    stored = {}
    if row and row.provider == body.provider:
        try:
            stored = decrypt_secrets(row.credentials_enc)
        except AIUnavailable:
            stored = {}
    allowed = SECRET_FIELDS[body.provider]
    merged = {k: v for k, v in stored.items() if k in allowed}
    for f in allowed:
        v = getattr(body, f)
        if v is not None and v.strip():
            merged[f] = v.strip()
    return merged


def _candidate(body: AIConfigIn, row, merged: dict, tenant_id: int) -> ProviderConfig:
    return ProviderConfig(provider=body.provider, model=body.model.strip(), source="tenant", tenant_id=tenant_id,
                          api_base=(body.api_base or "").strip() or None, region=body.region, project=body.project,
                          location=body.location, auth_mode=body.auth_mode if body.provider == "bedrock" else None,
                          role_arn=body.role_arn, external_id=(row.external_id if row else None), secrets=merged)


async def _validate(db, cfg: ProviderConfig, tenant_id: int, *, allow_incomplete_role: bool) -> None:
    if cfg.provider in SSRF_GUARDED and cfg.api_base:
        try:
            await asyncio.to_thread(assert_url_allowed, cfg.api_base, await tenant_allowlist(db, tenant_id))
        except SSRFError as e:
            raise HTTPException(status_code=422, detail=f"{e}")
    missing = [m for m in missing_fields(cfg) if not (allow_incomplete_role and m == "role_arn")]
    if missing:
        raise HTTPException(status_code=422, detail="missing " + ", ".join(missing))
    if cfg.provider == "vertex" and cfg.secrets.get("service_account_json"):
        try:
            build_kwargs(cfg)
        except ConfigError as e:
            raise HTTPException(status_code=422, detail=str(e))


@router.get("/config")
async def get_config(db: AsyncSession = Depends(deps.get_db), current_user: User = Depends(deps.require_admin),
                     tenant_id: int = Depends(deps.get_effective_tenant_id)) -> Any:
    return _out(await _row(db, tenant_id), await aws.deployment_principal_arn())


@router.put("/config")
async def put_config(body: AIConfigIn, db: AsyncSession = Depends(deps.get_db),
                     current_user: User = Depends(deps.require_admin),
                     tenant_id: int = Depends(deps.get_effective_tenant_id)) -> Any:
    if not settings.AI_ALLOW_TENANT_OVERRIDE:
        raise HTTPException(status_code=403, detail="AI provider is managed by the deployment")
    row = await _row(db, tenant_id)
    merged = _merge_secrets(body, row)
    cfg = _candidate(body, row, merged, tenant_id)
    await _validate(db, cfg, tenant_id, allow_incomplete_role=True)
    created = row is None
    if created:
        row = TenantAIConfig(tenant_id=tenant_id, provider=cfg.provider, model=cfg.model)
        db.add(row)
    changed = [f for f in _PLAIN_FIELDS if getattr(row, f, None) != getattr(cfg if f != "enabled" else body, f, None)]
    row.provider, row.model = cfg.provider, cfg.model
    row.api_base, row.region, row.project, row.location = cfg.api_base, cfg.region, cfg.project, cfg.location
    row.auth_mode, row.role_arn = cfg.auth_mode, cfg.role_arn
    if cfg.provider == "bedrock" and cfg.auth_mode == "role" and not row.external_id:
        row.external_id = pysecrets.token_urlsafe(24)
    row.enabled = body.enabled and not (cfg.provider == "bedrock" and cfg.auth_mode == "role" and not cfg.role_arn)
    row.credentials_enc = encrypt(json.dumps(merged)) if merged else None
    secrets_updated = sorted(f for f in SECRET_FIELDS[cfg.provider] if (getattr(body, f) or "").strip())
    await db.flush()
    await create_audit_log(db=db, entity_type="ai_config", entity_id=row.id, action="create" if created else "update",
                           tenant_id=tenant_id, user_id=current_user.id,
                           changes={"fields": changed, "secrets_updated": secrets_updated})
    await db.commit()
    await db.refresh(row)
    invalidate(tenant_id)
    aws.clear_cache(tenant_id)
    return _out(row, await aws.deployment_principal_arn())


@router.post("/config/test")
async def test_config(body: Optional[AIConfigIn] = Body(None), db: AsyncSession = Depends(deps.get_db),
                      current_user: User = Depends(deps.require_admin),
                      tenant_id: int = Depends(deps.get_effective_tenant_id)) -> Any:
    row = await _row(db, tenant_id)
    if body is not None:
        cfg = _candidate(body, row, _merge_secrets(body, row), tenant_id)
        await _validate(db, cfg, tenant_id, allow_incomplete_role=False)
    elif row and row.enabled and settings.AI_ALLOW_TENANT_OVERRIDE:
        try:
            cfg = row_to_config(row)
        except AIUnavailable as e:
            return {"ok": False, "message": str(e)}
    else:
        cfg = deployment_config()
    allow = await tenant_allowlist(db, tenant_id) if cfg.source == "tenant" else []
    ok, message = await test_connection(cfg, allowlist=allow)
    return {"ok": ok, "message": message}


@router.delete("/config", status_code=204)
async def delete_config(db: AsyncSession = Depends(deps.get_db), current_user: User = Depends(deps.require_admin),
                        tenant_id: int = Depends(deps.get_effective_tenant_id)) -> Response:
    row = await _row(db, tenant_id)
    if row:
        await create_audit_log(db=db, entity_type="ai_config", entity_id=row.id, action="delete",
                               tenant_id=tenant_id, user_id=current_user.id)
        await db.delete(row)
        await db.commit()
    invalidate(tenant_id)
    aws.clear_cache(tenant_id)
    return Response(status_code=204)


@router.get("/info")
async def info(db: AsyncSession = Depends(deps.get_db), current_user: User = Depends(deps.get_current_active_user),
               tenant_id: int = Depends(deps.get_effective_tenant_id)) -> Any:
    return await describe(db, tenant_id)
```
Register in `backend/app/api/api.py`: add `ai_config` to the `from app.api.v1 import ...` line and `api_router.include_router(ai_config.router, prefix="/ai", tags=["ai"])`.

- [ ] **Step 5: Run — expect PASS**; full suite; temp-tenant count 0.

- [ ] **Step 6: Commit**
```bash
git add backend/app/schemas/ai_config.py backend/app/api/v1/ai_config.py backend/app/api/api.py backend/tests/test_ai_api.py
git commit -m "feat(ai): admin API for tenant AI provider config"
```

---

### Task 7: Switch `AIService` and its callers to the new layer

**Files:**
- Modify: `backend/app/services/ai_service.py`, `backend/app/api/v1/copilot.py`, `backend/app/tasks/triage.py`
- Test: `backend/tests/test_ai_service.py`

**Interfaces:**
- Consumes: `llm.complete` (Task 5), `AIError`, `AIUnavailable`.
- Produces: `AIService(tenant_id: Optional[int] = None)`; method signatures unchanged.

- [ ] **Step 1: Write the failing tests**

`backend/tests/test_ai_service.py`:
```python
import asyncio
import json

import pytest

from app.ai.errors import AIUnavailable
from app.services import ai_service
from app.services.ai_service import AIService


@pytest.fixture
def fake(monkeypatch):
    calls = []

    async def complete(messages, *, tenant_id, temperature=None, json_mode=False):
        calls.append({"messages": messages, "tenant_id": tenant_id, "temperature": temperature, "json_mode": json_mode})
        return fake.reply
    fake.reply = "answer"
    monkeypatch.setattr(ai_service.llm, "complete", complete)
    return calls


def run(c):
    return asyncio.run(c)


def test_chat_uses_tenant_and_temperature(fake):
    out = run(AIService(tenant_id=5).chat([{"role": "user", "content": "hi"}]))
    assert out == "answer"
    assert fake[0]["tenant_id"] == 5 and fake[0]["temperature"] == 0.3
    assert fake[0]["messages"][0]["role"] == "system"


def test_triage_uses_json_mode(fake):
    fake.reply = json.dumps({"severity": "high", "tags": ["x"], "next_steps": "do"})
    res = run(AIService(tenant_id=5).generate_triage({"title": "t"}))
    assert fake[0]["json_mode"] is True and fake[0]["temperature"] == 0
    assert res is not None


def test_force_action_uses_json_mode(fake):
    fake.reply = json.dumps({"type": None})
    assert run(AIService(tenant_id=1).force_action_extraction("hello")) is None
    assert fake[0]["json_mode"] is True


@pytest.fixture
def down(monkeypatch):
    async def complete(messages, **kw):
        raise AIUnavailable("AuthenticationError: invalid key")
    monkeypatch.setattr(ai_service.llm, "complete", complete)


def test_fallbacks_when_unavailable(down):
    svc = AIService(tenant_id=1)
    assert "Case" in run(svc.generate_welcome_briefing({"title": "T", "severity": "high"}))  # static briefing
    assert run(svc.generate_general_welcome({})) is not None
    assert "unavailable" in run(svc.chat([{"role": "user", "content": "hi"}])).lower()
    assert "unavailable" in run(svc.analyze_case({"title": "T"})).lower()
    assert run(svc.generate_note_content([], "add note")) is None
    assert run(svc.force_action_extraction("x")) is None
    assert run(svc.generate_triage({"title": "t"})) is None


def test_no_ollama_specific_code_left():
    src = open(ai_service.__file__).read()
    assert "_call_ollama" not in src and "_check_ollama_available" not in src and "api/chat" not in src
```

- [ ] **Step 2: Run — expect FAIL.**

- [ ] **Step 3: Implement**

In `backend/app/services/ai_service.py`:
- Imports: remove `httpx` if unused afterwards; add `from app.ai import llm` and `from app.ai.errors import AIError`.
- Replace the constructor and transport:
```python
class AIService:
    def __init__(self, tenant_id: Optional[int] = None):
        self.tenant_id = tenant_id

    async def _complete(self, messages: List[Dict[str, str]], *, temperature: Optional[float] = None,
                        json_mode: bool = False) -> str:
        return await llm.complete(messages, tenant_id=self.tenant_id, temperature=temperature, json_mode=json_mode)
```
  Delete `_call_ollama` and `_check_ollama_available`.
- In every method, remove the `if not await self._check_ollama_available(): ...` early return and replace the `self._call_ollama("api/chat", {...})` call with `await self._complete([...messages...], temperature=..., json_mode=...)`, mapping:
  - `generate_welcome_briefing`: no temperature → `text = await self._complete(msgs)`; return `text or self._static_welcome_briefing(context)`; `except AIError` → static briefing.
  - `analyze_case`: `except AIError as e: return f"AI Analysis unavailable: {e}"`.
  - `generate_general_welcome`: like the welcome briefing with `_static_general_welcome`.
  - `chat`: `temperature=0.3`; `except AIError as e: return f"AI Assistant unavailable: {e}"`.
  - `generate_note_content`: `temperature=0`; keep the post-processing; `except AIError` → `None`.
  - `force_action_extraction` and `generate_triage`: `temperature=0, json_mode=True`; `data = json.loads(text or "{}")`; keep existing parsing; `except (AIError, ValueError)` → `None`.
  - Remove the `httpx.TimeoutException` branches (timeouts now arrive as `AIUnavailable`).
  - Update docstrings/messages that say "Ollama" to say "the AI provider".

In `backend/app/api/v1/copilot.py`: `AIService()` → `AIService(tenant_id)` at all four sites (`_create_session_with_briefing` and `_create_general_session_with_briefing` already receive `tenant_id`; the chat and analyze endpoints have `tenant_id` from `Depends(deps.get_effective_tenant_id)`).

In `backend/app/tasks/triage.py`: `AIService().generate_triage(...)` → `AIService(triage.tenant_id).generate_triage(...)`. The existing `None → triage.status = "failed"` path stays; set `triage.error_message = "AI provider unavailable or returned an unusable response"` if the current message mentions Ollama.

- [ ] **Step 4: Run** `tests/test_ai_service.py` → PASS; then the full suite → PASS (existing copilot heuristics and triage tests unchanged).

- [ ] **Step 5: Smoke** — restart backend + worker; `docker compose exec -T backend python -c "import app.main, app.worker"` imports cleanly; worker log shows no import errors.

- [ ] **Step 6: Commit**
```bash
git add backend/app/services/ai_service.py backend/app/api/v1/copilot.py backend/app/tasks/triage.py backend/tests/test_ai_service.py
git commit -m "refactor(ai): AIService uses the provider-agnostic LLM layer per tenant"
```

---

### Task 8: Frontend — AI provider card and Copilot label

**Files:**
- Create: `frontend/src/features/integrations/AIProviderCard.tsx`
- Modify: `frontend/src/features/integrations/Integrations.tsx` (render `<AIProviderCard />` in the admin branch, above `<SlackCard />`), `frontend/src/features/copilot/CopilotChat.tsx` (provider label in the header)

**Interfaces:**
- Consumes: `GET/PUT/DELETE /ai/config`, `POST /ai/config/test`, `GET /ai/info` (Task 6).

- [ ] **Step 1: Implement the card**

`frontend/src/features/integrations/AIProviderCard.tsx`:
```tsx
import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Bot, Copy } from 'lucide-react';
import { isAxiosError } from 'axios';
import { api } from '../../api/client';

type Provider = 'ollama' | 'openai' | 'openai_compatible' | 'anthropic' | 'gemini' | 'vertex' | 'bedrock';
type SecretField = 'api_key' | 'organization' | 'access_key_id' | 'secret_access_key' | 'session_token' | 'service_account_json';

interface AIConfig {
    source: 'tenant' | 'deployment';
    override_allowed: boolean;
    deployment: { provider: string; model: string };
    deployment_principal_arn: string | null;
    provider: Provider; model: string; enabled: boolean;
    api_base: string | null; region: string | null; project: string | null; location: string | null;
    auth_mode: 'role' | 'keys' | null; role_arn: string | null; external_id: string | null;
    secrets_set: Record<SecretField, boolean>;
}

const LABEL: Record<Provider, string> = {
    ollama: 'Ollama (local)', openai: 'OpenAI', openai_compatible: 'OpenAI-compatible (vLLM, LM Studio, OpenRouter…)',
    anthropic: 'Anthropic', gemini: 'Google Gemini (AI Studio)', vertex: 'Google Vertex AI', bedrock: 'AWS Bedrock',
};
const FIELDS: Record<Provider, { plain: string[]; secrets: SecretField[] }> = {
    ollama: { plain: ['api_base'], secrets: [] },
    openai: { plain: [], secrets: ['api_key', 'organization'] },
    openai_compatible: { plain: ['api_base'], secrets: ['api_key'] },
    anthropic: { plain: [], secrets: ['api_key'] },
    gemini: { plain: [], secrets: ['api_key'] },
    vertex: { plain: ['project', 'location'], secrets: ['service_account_json'] },
    bedrock: { plain: ['region'], secrets: [] },
};
const FIELD_LABEL: Record<string, string> = {
    api_base: 'Base URL', project: 'GCP project', location: 'Location (e.g. us-central1)', region: 'AWS region',
    api_key: 'API key', organization: 'Organization (optional)', access_key_id: 'Access key ID',
    secret_access_key: 'Secret access key', session_token: 'Session token (optional)',
    service_account_json: 'Service account JSON',
};

const errText = (e: unknown, fb: string) =>
    isAxiosError(e) && typeof e.response?.data?.detail === 'string' ? e.response.data.detail : fb;

export default function AIProviderCard() {
    const qc = useQueryClient();
    const { data } = useQuery({ queryKey: ['ai-config'], queryFn: async () => (await api.get('/ai/config')).data as AIConfig });
    const [draft, setDraft] = useState<Record<string, string | boolean | null> | null>(null);
    const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
    const form: Record<string, string | boolean | null> = draft ?? (data ? {
        provider: data.source === 'tenant' ? data.provider : data.deployment.provider,
        model: data.source === 'tenant' ? data.model : '', enabled: true,
        api_base: data.api_base, region: data.region, project: data.project, location: data.location,
        auth_mode: data.auth_mode ?? 'role', role_arn: data.role_arn,
    } : {});
    const set = (k: string, v: string | boolean | null) => { setDraft({ ...form, [k]: v }); setMsg(null); };
    const provider = (form.provider as Provider) || 'ollama';
    const spec = FIELDS[provider];
    const bedrockSecrets: SecretField[] = form.auth_mode === 'keys' ? ['access_key_id', 'secret_access_key', 'session_token'] : [];
    const secretFields = provider === 'bedrock' ? bedrockSecrets : spec.secrets;

    const body = () => {
        const b: Record<string, unknown> = { provider, model: form.model, enabled: form.enabled ?? true };
        for (const f of spec.plain) b[f] = form[f] || null;
        if (provider === 'bedrock') { b.auth_mode = form.auth_mode; b.role_arn = form.role_arn || null; }
        for (const f of secretFields) if (form[`secret_${f}`]) b[f] = form[`secret_${f}`];
        return b;
    };
    const save = useMutation({
        mutationFn: async () => (await api.put('/ai/config', body())).data as AIConfig,
        onSuccess: (r) => { qc.setQueryData(['ai-config'], r); setDraft(null); qc.invalidateQueries({ queryKey: ['ai-info'] });
            setMsg({ ok: true, text: r.enabled ? 'Saved' : 'Saved (disabled until the role ARN is set)' }); },
        onError: (e) => setMsg({ ok: false, text: errText(e, 'Save failed') }),
    });
    const test = useMutation({
        mutationFn: async () => (await api.post('/ai/config/test', draft ? body() : undefined)).data as { ok: boolean; message: string },
        onSuccess: (r) => setMsg({ ok: r.ok, text: r.message }),
        onError: (e) => setMsg({ ok: false, text: errText(e, 'Test failed') }),
    });
    const reset = useMutation({
        mutationFn: async () => api.delete('/ai/config'),
        onSuccess: () => { setDraft(null); qc.invalidateQueries({ queryKey: ['ai-config'] }); qc.invalidateQueries({ queryKey: ['ai-info'] });
            setMsg({ ok: true, text: 'Reverted to the deployment default' }); },
        onError: (e) => setMsg({ ok: false, text: errText(e, 'Reset failed') }),
    });

    if (!data) return null;
    const input = 'w-full border border-zinc-300 px-2 py-1.5 text-sm';
    const readOnly = !data.override_allowed;
    const trust = data.external_id ? JSON.stringify({
        Version: '2012-10-17', Statement: [{ Effect: 'Allow', Principal: { AWS: data.deployment_principal_arn ?? '<SOC Hub server role ARN>' },
            Action: 'sts:AssumeRole', Condition: { StringEquals: { 'sts:ExternalId': data.external_id } } }],
    }, null, 2) : null;

    return (
        <div className="bg-white border border-zinc-200 p-5 space-y-3">
            <div className="flex items-center gap-2">
                <Bot size={16} className="text-accent-600" />
                <h2 className="font-semibold text-zinc-800">AI provider</h2>
                <span className="ml-auto label-mono">{data.source === 'tenant' ? `this tenant · ${data.provider}` : `deployment default · ${data.deployment.provider}`}</span>
            </div>
            {readOnly ? (
                <p className="text-sm text-zinc-600">Managed by the deployment: <span className="font-mono">{data.deployment.provider} / {data.deployment.model}</span></p>
            ) : (
                <>
                    <p className="text-xs text-zinc-500">Case data sent to the AI goes to this provider. Leave unset to use the deployment default ({data.deployment.provider} / {data.deployment.model}).</p>
                    <label className="block"><span className="label-mono">provider</span>
                        <select className={input} value={provider} onChange={(e) => setDraft({ provider: e.target.value, model: '', enabled: true, auth_mode: 'role' })}>
                            {(Object.keys(LABEL) as Provider[]).map((p) => <option key={p} value={p}>{LABEL[p]}</option>)}
                        </select></label>
                    <label className="block"><span className="label-mono">model</span>
                        <input className={`${input} font-mono`} value={(form.model as string) ?? ''} onChange={(e) => set('model', e.target.value)}
                            placeholder={provider === 'bedrock' ? 'anthropic.claude-3-5-sonnet-20241022-v2:0' : 'model id'} /></label>
                    {spec.plain.map((f) => (
                        <label key={f} className="block"><span className="label-mono">{FIELD_LABEL[f]}</span>
                            <input className={`${input} font-mono`} value={(form[f] as string) ?? ''} onChange={(e) => set(f, e.target.value)} /></label>
                    ))}
                    {provider === 'bedrock' && (
                        <fieldset className="space-y-2">
                            <legend className="label-mono">AWS credentials</legend>
                            <label className="flex items-center gap-2 text-sm"><input type="radio" checked={form.auth_mode !== 'keys'} onChange={() => set('auth_mode', 'role')} />Assume a role in your AWS account (recommended)</label>
                            <label className="flex items-center gap-2 text-sm"><input type="radio" checked={form.auth_mode === 'keys'} onChange={() => set('auth_mode', 'keys')} />Access keys</label>
                            {form.auth_mode !== 'keys' && (
                                <>
                                    <label className="block"><span className="label-mono">role ARN</span>
                                        <input className={`${input} font-mono`} value={(form.role_arn as string) ?? ''} onChange={(e) => set('role_arn', e.target.value)} placeholder="arn:aws:iam::123456789012:role/sochub-bedrock" /></label>
                                    {trust ? (
                                        <div>
                                            <div className="flex items-center justify-between"><span className="label-mono">trust policy for that role</span>
                                                <button type="button" className="text-xs text-accent-600 flex items-center gap-1" onClick={() => navigator.clipboard.writeText(trust)}><Copy size={12} />Copy</button></div>
                                            <pre className="text-[11px] font-mono bg-zinc-50 border border-zinc-200 p-2 overflow-x-auto">{trust}</pre>
                                        </div>
                                    ) : <p className="text-xs text-zinc-500">Save once with "Assume a role" selected to generate your External ID and trust policy.</p>}
                                </>
                            )}
                        </fieldset>
                    )}
                    {secretFields.map((f) => (
                        <label key={f} className="block"><span className="label-mono">{FIELD_LABEL[f]}</span>
                            {f === 'service_account_json'
                                ? <textarea rows={4} className={`${input} font-mono text-xs`} value={(form[`secret_${f}`] as string) ?? ''} onChange={(e) => set(`secret_${f}`, e.target.value)} placeholder={data.secrets_set[f] ? '•••• (unchanged)' : '{ "type": "service_account", … }'} />
                                : <input type="password" autoComplete="off" className={`${input} font-mono`} value={(form[`secret_${f}`] as string) ?? ''} onChange={(e) => set(`secret_${f}`, e.target.value)} placeholder={data.secrets_set[f] ? '•••• (unchanged)' : ''} />}
                        </label>
                    ))}
                    <div className="flex items-center gap-2 flex-wrap">
                        <button onClick={() => save.mutate()} disabled={save.isPending || !form.model} className="h-8 px-3 bg-accent-600 text-white text-sm disabled:opacity-50">Save</button>
                        <button onClick={() => test.mutate()} disabled={test.isPending} className="h-8 px-3 border border-zinc-300 text-sm disabled:opacity-50">{test.isPending ? 'Testing…' : 'Test'}</button>
                        {data.source === 'tenant' && <button onClick={() => reset.mutate()} className="h-8 px-3 border border-zinc-300 text-sm">Reset to deployment default</button>}
                        {msg && <span role="status" className={msg.ok ? 'text-xs text-emerald-700' : 'text-xs text-red-700'}>{msg.text}</span>}
                    </div>
                </>
            )}
        </div>
    );
}
```
(If lint flags the `form` derivation or `set` closure, keep behaviour and adjust only typing/eslint-compliant structure.)

- [ ] **Step 2: Wire it in** — `Integrations.tsx`: `import AIProviderCard from './AIProviderCard';` and render `<AIProviderCard />` inside the admin branch above `<SlackCard />`.

- [ ] **Step 3: Copilot label** — in `CopilotChat.tsx` add:
```tsx
const { data: aiInfo } = useQuery({ queryKey: ['ai-info'], queryFn: async () => (await api.get('/ai/info')).data as { provider: string | null; model: string | null }, staleTime: 60_000 });
```
and next to the "Copilot" header text render:
```tsx
{aiInfo?.provider && <span className="ml-2 font-mono text-[10px] opacity-70" title="Where case data is sent">AI: {aiInfo.provider} · {aiInfo.model}</span>}
```
(import `useQuery` / `api` if the file doesn't already).

- [ ] **Step 4: Verify** — `cd frontend && npm run build` clean; `npm run lint` shows no new errors in these files; `docker compose up -d --no-deps --build frontend`; `curl -s -o /dev/null -w '%{http_code}' http://localhost/integrations` → 200. Browser click-through is deferred to the human (no browser available).

- [ ] **Step 5: Commit**
```bash
git add frontend/src/features/integrations frontend/src/features/copilot/CopilotChat.tsx
git commit -m "feat(ai): AI provider settings card and Copilot provider label"
```

---

### Task 9: Docs and end-to-end smoke

**Files:**
- Modify: `docs/configuration.md`, `docs/features.md`, `.env.example`

- [ ] **Step 1: Docs** — `docs/configuration.md` gets an "AI provider" section: the env table (`AI_PROVIDER`, `AI_MODEL`, `AI_API_BASE`, `AI_API_KEY`, `AI_AWS_REGION`, `VERTEX_PROJECT`, `VERTEX_LOCATION`, `AI_TIMEOUT_SECONDS`, `AI_ALLOW_TENANT_OVERRIDE`) with defaults and the backward-compatibility note; per-provider examples (Ollama local, OpenAI, OpenAI-compatible/vLLM, Anthropic, Gemini, Vertex with ADC, Bedrock with an instance role); the AWS standard credential chain order; the tenant assume-role setup (external ID, trust policy, required `bedrock:InvokeModel` permission on the tenant role); the no-fallback rule; the SSRF/allowlist rule for tenant base URLs; and that LiteLLM telemetry is disabled. `docs/features.md`: a short "AI providers" entry. `.env.example`: commented AI_* examples.

- [ ] **Step 2: End-to-end smoke (no real provider credentials)**
  - In the backend container, run a script that sets a temp tenant override to `openai_compatible` pointing at a tiny local stub (start `python -m http.server`-style stub *inside the container* that answers `POST /v1/chat/completions` with an OpenAI-shaped JSON `{"choices":[{"message":{"role":"assistant","content":"OK"}}]}` on `127.0.0.1:<port>`), add `127.0.0.1` to that temp tenant's allowlist, call `llm.complete(...)` → `"OK"`; then call `AIService(tid).chat(...)` → the stub's text. Delete the temp tenant/config by id afterwards; confirm the `wf-test-%` count is 0.
  - With the deployment default unchanged (Ollama container intentionally stopped), `AIService(None).analyze_case({"title":"t"})` returns the "AI Analysis unavailable: …" message (no crash).
  - Record outputs in the report.

- [ ] **Step 3: Commit**
```bash
git add docs/configuration.md docs/features.md .env.example
git commit -m "docs(ai): configuring AI providers, Bedrock roles and tenant overrides"
```
