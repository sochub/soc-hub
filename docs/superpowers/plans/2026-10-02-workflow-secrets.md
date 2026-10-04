# Workflow Secrets Store Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Per-tenant encrypted secrets that workflow HTTP steps reference as `{{ secrets.NAME }}`. Each secret is restricted to its allowed hosts. The real value is substituted only when the request is sent, is redacted from outputs and errors, and is never stored in run data.

**Architecture:**
- **Data:** a `tenant_secrets` table holding a Fernet-encrypted value and its allowed hosts.
- **`app/secrets/`:**
  - `hosts.py`: pure host-pattern validation and matching.
  - `refs.py`: `SecretRef` / `SecretsNamespace`, so templates render to an opaque placeholder `⟦secret:NAME⟧`.
  - `resolve.py`: send-time substitution, host checks and redaction.
- **Workflow engine:** the HTTP node resolves secrets after the SSRF check and redacts its output and errors. Validation rejects misuse at save time.
- **Admin API and UI:** an admin API plus an Integrations card, a picker in the workflow editor, and scan/convert helpers for credentials that are already plaintext.

**Tech Stack:** FastAPI, SQLAlchemy async, Alembic, Jinja2 sandbox (existing), httpx, React 19.

**Spec:** `docs/superpowers/specs/2026-10-02-workflow-secrets-design.md`

## Global Constraints

- Branch `feat/workflow-secrets`, stacked on `feat/incident-reports`.
- Migration `c7d8e9f0a1b2`, with `down_revision = "b6c7d8e9f0a1"`. The live dev DB is at `b6c7d8e9f0a1`.
- **Names and values:**
  - Secret names match `^[A-Z][A-Z0-9_]{1,63}$`.
  - Values are 1–8192 characters.
- **Placeholder:** exactly `⟦secret:NAME⟧` (U+27E6 / U+27E7). Find it with the regex `⟦secret:([A-Z][A-Z0-9_]{1,63})⟧`.
- **Redaction marker:** `••••`.
- **Error messages, exact text:**
  - `"Unknown secret NAME"`
  - `"Secret NAME could not be decrypted — re-enter it in Integrations"`
  - `"Secret NAME is not allowed for host HOST"`
  - `"secrets are not allowed in the URL host or credentials"`
  - `"secrets can only be inserted, not transformed"`
  - `"Secrets are only allowed in HTTP request URL, headers and body"`
  - `"Invalid secret name NAME"`
- **Roles:**
  - Secrets CRUD, `/scan` and `/convert`: `require_admin`.
  - `/names`: `require_analyst_or_above`.
  - A secret from another tenant returns 404.
- **Never log values.** The allowed log lines are:
  - `secret <op> tenant=%s name=%s outcome=%s`
  - `secret use tenant=%s names=%s host=%s outcome=%s`
- **Audit:** `entity_type="secret"`. `changes` holds only the name and the field names, never the value.
- **Tests:**
  - No network: use `httpx.MockTransport` or a monkeypatched client.
  - Delete DB rows by exact id.
  - Call `engine.dispose()` at entry and exit.
  - The `wf-test-%` tenant count must be 0 at the end.
- **Commands:**
  - Run tests: `docker compose exec -T backend python -m pytest tests/<file> -q`
  - After backend changes: `docker restart case_management-backend-1 case_management-worker-1`
- **Commits:** use `perl -e 'alarm 60; exec @ARGV' git commit ...` with your own Co-Authored-By line. If signing fails, leave the change staged and report it.

## Review Focus

1. **Lookalike hosts:** `evil-example.com`, `example.com.evil.com` and `EXAMPLE.com.` against `*.example.com` and `example.com`. Matches must be exact, case-insensitive and dot-anchored. → Task 1.
2. **Every way a value could leak:**
   - stored in `step.input` (the placeholder only);
   - a response that echoes the value raw or URL-encoded;
   - error text that contains the URL;
   - retries;
   - dry runs.

   → Tasks 3 and 4.
3. **Transforming a secret in a template:** `{{ secrets.X | upper }}`, `{{ secrets.X[0:3] }}`, `{{ secrets.X == 'a' }}`, `{{ secrets.X ~ 'y' | b64encode }}` must all fail. None may reveal anything about the value. → Task 2.
4. **Secrets spliced into dangerous places:** a URL host or userinfo, and CR/LF in a header value. → Task 3.
5. **Deleting a secret still in use:** `DELETE` returns 409 listing the workflows that use it. A forced delete then makes those workflows fail with "Unknown secret". → Tasks 4 and 6.

---

### Task 1: Model, migration and host rules

**Files:**
- Create: `backend/app/models/tenant_secret.py`, `backend/alembic/versions/c7d8e9f0a1b2_tenant_secrets.py`, `backend/app/secrets/__init__.py` (empty), `backend/app/secrets/hosts.py`
- Modify: `backend/app/db/base.py`, to register the model
- Test: `backend/tests/test_secret_hosts.py`

**Interfaces:**
- `TenantSecret` model: columns as in the spec, plus `UniqueConstraint("tenant_id", "name", name="uq_tenant_secret_name")`.
- `hosts.valid_host_pattern(p: str, tenant_allowlist: list[str]) -> bool`
- `hosts.host_allowed(host: str, patterns: list[str]) -> bool`

- [ ] **Step 1: Failing test** in `backend/tests/test_secret_hosts.py`:

```python
import pytest

from app.secrets.hosts import host_allowed, valid_host_pattern


@pytest.mark.parametrize("p", ["api.example.com", "*.example.com", "yourco.atlassian.net", "x-y.example.io"])
def test_valid_patterns(p):
    assert valid_host_pattern(p, [])


@pytest.mark.parametrize("p", ["", "*", "*.com", "example", "http://x.com", "x.com/", "a..b.com", "*.*.x.com",
                               "10.0.0.1", "intranet", "EXAMPLE.COM"])
def test_invalid_patterns_without_allowlist(p):
    assert not valid_host_pattern(p, [])


def test_ip_and_single_label_only_if_on_tenant_allowlist():
    assert valid_host_pattern("10.0.0.1", ["10.0.0.1"]) and valid_host_pattern("intranet", ["intranet"])


@pytest.mark.parametrize("host,patterns,ok", [
    ("api.example.com", ["api.example.com"], True),
    ("API.Example.com.", ["api.example.com"], True),
    ("a.example.com", ["*.example.com"], True),
    ("a.b.example.com", ["*.example.com"], True),
    ("example.com", ["*.example.com"], False),
    ("evil-example.com", ["*.example.com"], False),
    ("example.com.evil.com", ["*.example.com", "example.com"], False),
    ("api.example.com", [], False),
    ("[::1]", ["::1"], True),
    ("api.example.com:8443", ["api.example.com"], True),
])
def test_host_allowed(host, patterns, ok):
    assert host_allowed(host, patterns) is ok
```

- [ ] **Step 2: Run.** It should FAIL.
- [ ] **Step 3: Implement** `backend/app/secrets/hosts.py`:

```python
"""Pure host-pattern rules for secret destinations."""
import ipaddress
import re

_LABEL = r"(?!-)[a-z0-9-]{1,63}(?<!-)"
_HOST = re.compile(rf"^(?=.{{1,253}}$)({_LABEL}\.)+(?=[a-z0-9-]*[a-z])[a-z0-9-]{{2,63}}$")


def _norm(host: str) -> str:
    h = (host or "").strip().lower()
    if h.startswith("["):
        h = h[1:].split("]", 1)[0]
    elif h.count(":") == 1:  # host:port (an IPv6 literal has several colons)
        h = h.split(":", 1)[0]
    return h.rstrip(".")


def _is_ip(h: str) -> bool:
    try:
        ipaddress.ip_address(h)
        return True
    except ValueError:
        return False


def valid_host_pattern(p: str, tenant_allowlist) -> bool:
    if not isinstance(p, str) or p != p.strip() or p != p.lower():
        return False
    if p.startswith("*."):
        return bool(_HOST.match(p[2:]))
    if _HOST.match(p):
        return True
    return p in (tenant_allowlist or [])


def host_allowed(host: str, patterns) -> bool:
    h = _norm(host)
    if not h:
        return False
    for p in patterns or []:
        p = (p or "").lower().rstrip(".")
        if p.startswith("*."):
            base = p[2:]
            if h.endswith("." + base) and h != base:
                return True
        elif h == p or (_is_ip(h) and _is_ip(p) and ipaddress.ip_address(h) == ipaddress.ip_address(p)):
            return True
    return False
```

- [ ] **Step 4: Model and migration.** Write them as in the spec, then run alembic upgrade → downgrade -1 → upgrade. The head must end at `c7d8e9f0a1b2`.
- [ ] **Step 5: Run the tests,** first this file, then the full suite. Then commit with the message `feat(secrets): tenant_secrets table and host rules`.

---

### Task 2: Template placeholders (SecretRef)

**Files:**
- Create: `backend/app/secrets/refs.py`
- Modify:
  - `backend/app/workflows/templating.py`: `render()` converts `SecretRef` to its placeholder.
  - `backend/app/workflows/context.py`: `build_context` adds `"secrets": SecretsNamespace()`.
- Test: `backend/tests/test_secret_refs.py`

**Interfaces:**
- `SecretRef(name)`. `str(ref)` returns `"⟦secret:NAME⟧"`. Every other dunder raises `TemplateError("secrets can only be inserted, not transformed")`.
- `SecretsNamespace()`. `getattr` returns a `SecretRef`. Item access works the same way. A name that doesn't match the pattern raises `TemplateError("Invalid secret name X")`.
- `PLACEHOLDER_RE` is exported.

- [ ] **Step 1: Failing test** in `backend/tests/test_secret_refs.py`:

```python
import pytest

from app.secrets.refs import PLACEHOLDER_RE, SecretsNamespace
from app.workflows.templating import TemplateError, eval_expr, render

CTX = {"secrets": SecretsNamespace(), "case": {"title": "t"}}


def test_inline_and_single_expression_render_placeholder():
    assert render("Bearer {{ secrets.JIRA_TOKEN }}", CTX) == "Bearer ⟦secret:JIRA_TOKEN⟧"
    assert render("{{ secrets.JIRA_TOKEN }}", CTX) == "⟦secret:JIRA_TOKEN⟧"
    assert render({"h": ["{{ secrets.A_B }}"]}, CTX) == {"h": ["⟦secret:A_B⟧"]}
    assert PLACEHOLDER_RE.findall("x ⟦secret:A_B⟧ y") == ["A_B"]


@pytest.mark.parametrize("tpl", [
    "{{ secrets.X | upper }}", "{{ secrets.X[0:3] }}", "{{ secrets.X == 'a' }}", "{{ secrets.X | length }}",
    "{{ (secrets.X ~ 'y') | b64encode }}", "{{ secrets.X + 'y' }}", "{{ 'a' in secrets.X }}",
    "{{ secrets.X.lower() }}", "{% if secrets.X %}y{% endif %}", "{{ secrets.lower_bad }}",
])
def test_transforms_rejected(tpl):
    with pytest.raises(TemplateError):
        render(tpl, CTX)


def test_expression_contexts_reject():
    with pytest.raises(TemplateError):
        eval_expr("secrets.X == 'a'", CTX)
```

Jinja's `~` operator calls `str()`, so `secrets.X ~ 'y'` produces `"⟦secret:X⟧y"`. That only exposes the placeholder text, never the value, so it is safe. The `| b64encode` case should still fail, because the filter isn't registered; if it is registered in your Jinja version, a placeholder that has been transformed simply won't be resolved at send time. Keep the test, and assert whichever behaviour actually occurs. That is: it either raises, or the output doesn't contain a valid placeholder. Plain `str()` concatenation of the placeholder is allowed. Do not try to block it.

- [ ] **Step 2: Implement** `backend/app/secrets/refs.py`:

```python
"""Template-time secret references: render to an opaque placeholder; anything else fails."""
import re

from app.workflows.templating import TemplateError

NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]{1,63}$")
PLACEHOLDER_RE = re.compile(r"⟦secret:([A-Z][A-Z0-9_]{1,63})⟧")
_MSG = "secrets can only be inserted, not transformed"


def placeholder(name: str) -> str:
    return f"⟦secret:{name}⟧"


def _deny(*_a, **_k):
    raise TemplateError(_MSG)


class SecretRef:
    __slots__ = ("_name",)

    def __init__(self, name: str):
        object.__setattr__(self, "_name", name)

    def __str__(self):
        return placeholder(self._name)

    __html__ = __str__

    def __repr__(self):
        return f"SecretRef({self._name})"

    def __getattr__(self, item):
        _deny()

    __bool__ = __len__ = __iter__ = __getitem__ = __contains__ = _deny
    __eq__ = __ne__ = __lt__ = __le__ = __gt__ = __ge__ = __hash__ = _deny
    __add__ = __radd__ = __mul__ = __rmul__ = __mod__ = __rmod__ = _deny

    def __format__(self, spec):
        if spec:
            _deny()
        return str(self)


class SecretsNamespace:
    def __getattr__(self, name):
        if not NAME_RE.match(name):
            raise TemplateError(f"Invalid secret name {name}")
        return SecretRef(name)

    __getitem__ = __getattr__
```

Adjust this to how the sandbox actually behaves:
- `ImmutableSandboxedEnvironment` may block `__getattr__` on unknown objects, or call `is_safe_attribute`. If attribute access on the namespace is blocked, override `is_safe_attribute`, or make `SecretsNamespace` a mapping instead.
- `render()` in `templating.py`: after `_render_str`, if the result is a `SecretRef`, return `str(result)`.
- Make sure a `TemplateError` raised inside the sandbox is not swallowed or turned into an Undefined.

- [ ] **Step 3: Wire it in.** `build_context` adds `"secrets": SecretsNamespace()`. Run this test file plus all existing `test_wf_*` tests, then the full suite. Commit as `feat(secrets): template placeholders for secret references`.

---

### Task 3: Send-time resolution and redaction

**Files:**
- Create: `backend/app/secrets/resolve.py`
- Test: `backend/tests/test_secret_resolve.py`

**Interfaces:**
- `async def resolve_for_request(db, tenant_id: int, url: str, headers: dict[str,str], body, host: str) -> tuple[str, dict, object, dict[str, str]]`, returning the new url, headers, body and `used` (`{name: value}`). It raises `NodeError` with the exact messages listed in the constraints.
- `def redact(obj, used: dict[str, str])`: deep redaction. For every value it replaces the value itself, `quote(v, safe='')` and `quote_plus(v)` with `••••`.
- `def redact_text(s: str, used) -> str`

Rules:
- **Where placeholders are found:** in the URL, in every header value, and in the body (strings, plus every leaf string of a dict or list).
- **Loading:** a single query, `select(TenantSecret).where(tenant_id==, name.in_(names))`. Decrypt each value with `app.utils.crypto.decrypt`. A decrypt failure raises the "could not be decrypted" error.
- **Host check:** every used secret must pass `host_allowed(host, row.allowed_hosts)`.
- **URL substitution:**
  - Split the URL with `urlsplit`.
  - A placeholder in the netloc raises `NodeError("secrets are not allowed in the URL host or credentials")`.
  - In the path, substitute `quote(value, safe='')`.
  - In the query, substitute `quote_plus(value)`.
  - In the fragment, strip it.
  - Rebuild the URL with `urlunsplit`.
- **Headers:** substitute the raw value. If the value contains `\r` or `\n`, raise `NodeError("secret NAME contains a line break and cannot be used in a header")`.
- **Body:** substitute the raw value into the string leaves.
- **`last_used_at`:** set it on the used rows to `datetime.now(timezone.utc)`. The caller commits.
- **Logging:** logging of `secret use` happens in the caller (Task 4).

- [ ] **Step 1: Failing tests.**
  - Seed a temporary tenant with secrets `TOK` (value `s3cr3t value&x`, `allowed_hosts=["api.example.com"]`) and `KEY` (`k3y\r\nX-Evil: 1`).
  - Assert each of these:
    - header substitution;
    - query encoding (`s3cr3t+value%26x`);
    - path encoding;
    - body dict leaf substitution;
    - a netloc placeholder gives an error;
    - an unknown name gives an error;
    - a wrong host gives an error;
    - `KEY` in a header gives an error about the line break;
    - a corrupted `value_enc` (an UPDATE on that row id) gives the decrypt error;
    - `last_used_at` gets set.
  - Redaction:
    - `redact({"a": "x s3cr3t value&x y", "b": ["s3cr3t+value%26x"], "c": 1}, used)` replaces both string forms and leaves the int alone.
    - `redact_text` works on its own.
  - Clean up by exact id.
- [ ] **Step 2: Implement, run, commit** as `feat(secrets): send-time resolution, host checks and redaction`.

---

### Task 4: HTTP node integration, dry run, persistence

**Files:**
- Modify: `backend/app/workflows/nodes/http.py`
- Test: `backend/tests/test_secret_http_node.py`

**Behaviour, in order, inside `run_http`:**
1. The existing SSRF check (`assert_url_allowed`), unchanged.
2. Compute `host = httpx.URL(url).host`.
3. `url, headers, body, used = await resolve_for_request(nctx.db, run.tenant_id, url, headers, body, host)`. This only happens when the URL, a header or the body contains a placeholder; otherwise `used = {}`.
4. Pin the IP and send, as today. The pinned `target` must be built from the URL **after substitution**.
5. Pass the output dict (status, headers, body/json, `truncated`) through `redact(output, used)` before returning it.
6. Every `NodeError`/`RetryableNodeError` raised after substitution carries `redact_text(msg, used)`. That includes the timeout and transport-error messages, which contain the URL.
7. When `used` is non-empty, log one line at INFO: `secret use tenant=%s names=%s host=%s outcome=%s`, with `outcome` set to `ok`, `error` or `blocked`.

Dry-run (`simulated_output`) path: `runtime.py` never calls `run_http` for simulated nodes, so make no change there. Add a test asserting that a dry run keeps the placeholder and doesn't touch `last_used_at`.

- [ ] **Step 1: Failing tests.** Use the in-process pattern from `backend/tests/test_wf_http_node.py`.
  - Seed a tenant whose allowlist and secret allow `api.example.com`. Monkeypatch `assert_url_allowed` to return `None`, and the httpx transport to a `MockTransport`. Then assert:
    - (a) Bearer header substitution reaches the mock. Inspect `request.headers`.
    - (b) The mock echoes the token in its JSON body, in a response header and URL-encoded in a field. The node output shows `••••` for all three, and the raw token appears nowhere in `json.dumps(output)`.
    - (c) A host that isn't allowed raises `NodeError`, and the mock is never called.
    - (d) A mock that raises `httpx.ConnectError` with the URL in its message gives a redacted `RetryableNodeError` text.
  - Full runtime test: create a workflow (HTTP node with `{{ secrets.TOK }}`) and a run, and execute the step through `runtime` the same way existing `test_wf_*` runtime tests do, with the transport mocked. Then:
    - `step.input` contains `⟦secret:TOK⟧`;
    - `SELECT input::text, output::text, error FROM workflow_run_steps WHERE run_id=:id` contains no token text.
  - Clean up by exact ids.
- [ ] **Step 2: Implement, run** this file plus all `test_wf_*` tests, then the full suite. Commit as `feat(secrets): resolve and redact secrets in the HTTP request node`.

---

### Task 5: Save-time validation

**Files:**
- Modify:
  - `backend/app/workflows/validation.py`: add the optional `secret_names: set[str] | None = None` to `validate_graph` and `workflow_errors`.
  - `backend/app/api/v1/workflows.py`: load the tenant's secret names and pass them at every `workflow_errors(...)` call site (lines ~54, 113, 145, 150, 202).
- Test: `backend/tests/test_secret_validation.py`

**Rules:**
- Find references with the regex `\bsecrets\.([A-Za-z_][A-Za-z0-9_]*)` over the raw string values of each node's config, walked recursively.
- In an `http_request` node:
  - the keys `url`, `headers` (values only) and `body` are allowed;
  - a reference in any other key gives the "only allowed" error;
  - an invalid name gives `"Invalid secret name X"`;
  - a name not in `secret_names` gives `"Unknown secret X"` (this check only runs when `secret_names` is not None).
- In any other node type, any `secrets.` reference gives the "only allowed" error.
- Transform check: for each `{{ … }}` span containing `secrets.`, report the "inserted, not transformed" error if that span also contains `|`, `[`, `==`, `!=`, `+`, ` in `, or a `(` after the secret reference.
- Errors use the existing `{"node_id": ..., "message": ...}` shape.

- [ ] **Step 1: Failing tests.**
  - Pure `validate_graph` cases, using graph fixtures copied from the existing validation tests: a Slack node, the HTTP `method` key, an unknown name, a filter, a valid use, and `secret_names=None` skipping the unknown check.
  - One API test: `PUT` of a workflow using an unknown secret returns 422 (pattern from `test_wf_api.py`).
- [ ] **Step 2: Implement, run, commit** as `feat(secrets): validate secret references when saving workflows`.

---

### Task 6: Secrets API

**Files:**
- Create: `backend/app/schemas/secret.py`, `backend/app/api/v1/secrets.py`
- Modify: `backend/app/api/api.py`, to register `prefix="/secrets"`
- Test: `backend/tests/test_secrets_api.py`

**Behaviour:** exactly as in the spec's API table, apart from `/scan` and `/convert`, which come in Task 7.

**`in_use_by`:**
- Query the tenant's workflows with `Workflow.graph.cast(Text).ilike(f"%secrets.{name}%")`.
- Then confirm each hit in Python with `re.search(rf"\bsecrets\.{name}\b", json.dumps(wf.graph))`.

**Validation:**
- `name` matches `NAME_RE` (from `refs.py`).
- `value` length is 1–8192, after `.strip()` for the emptiness check only; store the value unstripped.
- `allowed_hosts` is a list of at most 50 entries, each passing `valid_host_pattern(p, tenant.workflow_http_allowlist)`.
- `description` is at most 500 characters.

**Tests:** labelled lettered checks.
- (a) An admin creates a secret and gets 201. The response has no `value` field. The DB `value_enc` is not equal to the plaintext.
- (b) A duplicate name returns 409.
- (c) Bad name, empty value, too-long value and a bad host each return 422. An IP host returns 422 unless it is on the tenant allowlist.
- (d) An analyst gets 403 on POST, PUT and DELETE, and 200 on `/names`. A viewer gets 403 on `/names`.
- (e) `GET /` lists `in_use_by` after a workflow that references the secret is created.
- (f) A PUT with a blank `value` keeps the old one: decrypt and compare. A PUT with a new value replaces it.
- (g) DELETE of a secret in use returns 409 with the workflow names. `?force=true` returns 204.
- (h) A secret from another tenant returns 404 on PUT and DELETE.
- (i) Audit rows have `changes` with no value text.
- (j) caplog has no value text, and exactly one `secret <op>` line per operation.
- (k) The plaintext value never appears in any JSON response, checked over every response from the test.

- [ ] Implement, run, commit as `feat(secrets): secrets admin API`.

---

### Task 7: Scan and convert helpers

**Files:**
- Modify: `backend/app/api/v1/secrets.py`, adding `GET /scan` and `POST /convert/{workflow_id}`
- Create: `backend/app/secrets/scan.py`, containing pure `scan_graph(graph) -> list[dict]` and `apply_conversion(graph, items, names) -> graph`
- Test: `backend/tests/test_secret_scan.py`

**`scan_graph`** finds credentials in `http_request` nodes and returns `{node_id, location, key, host, suggested_name, literal}`. `literal` is used internally only; the API returns everything except `literal`.
- **Header keys**, matched case-insensitively: `authorization`, `x-api-key`, `api-key`, `apikey`, `x-auth-token`, `x-apikey`, `private-token`. A match counts only if its value contains no `{{`.
  - For an `Authorization` value of the form `Bearer xxx` or `Token xxx`, the literal is the part after the scheme word.
- **URL query keys:** `apikey`, `api_key`, `key`, `token`, `access_token`. A match counts only if its value contains no `{{`.
- **`suggested_name`:**
  - Built from the host and key, e.g. `API_EXAMPLE_COM_TOKEN`.
  - Upper-case, with every non-alphanumeric character replaced by `_`, and truncated to 64 characters.
  - If the result doesn't start with a letter, prefix it with `S_`.

**`apply_conversion`** rewrites the matched spot to the `{{ secrets.NAME }}` template, keeping any scheme prefix (`Bearer {{ secrets.NAME }}`) and leaving the rest of the query intact.

**`POST /convert/{workflow_id}`**, body `{items: [{node_id, location, key, secret_name}]}`:
1. Re-scan the workflow server-side. Any item that doesn't match a current finding gets a 422.
2. For each item:
   - If the secret already exists and its value equals the literal, reuse it.
   - If it exists with a different value, return 409.
   - Otherwise create it with `allowed_hosts=[host]`.
3. Apply the conversion and save through the same update logic as `PUT /workflows/{id}`. Factor out a helper so that validation runs.
4. Audit each secret that was created, plus the workflow update.

**`GET /scan`:** across all of the tenant's workflows, returns `[{workflow_id, workflow_name, node_id, location, key, host, suggested_name}]`. It must never return the literal value.

**Tests:**
- Pure `scan_graph`: a bearer header, an `x-api-key`, an `apikey` query, ignoring templated values, and the suggested-name format.
- `apply_conversion` output.
- API:
  - `/scan` lists the expected items and the literal text is absent from the response.
  - Convert creates the secret, the workflow graph now holds a template, and the workflow is still valid.
  - An existing secret with a different value gives 409.
  - An analyst gets 403.

- [ ] Implement, run, commit as `feat(secrets): scan and convert plaintext workflow credentials`.

---

### Task 8: Frontend

**Files:**
- Create: `frontend/src/features/integrations/SecretsCard.tsx`
- Modify:
  - `frontend/src/features/integrations/Integrations.tsx`: add `<SecretsCard key={active_tenant_id}/>` in the admin branch, after ThreatIntelCard.
  - `frontend/src/features/automations/NodeInspector.tsx`: for the http_request node, add a secret picker next to the URL, header-value and body inputs, plus a "Move to secret" warning on header rows.
  - `frontend/src/features/automations/AutomationsList.tsx`: add the scan banner and review dialog.
  - `frontend/src/features/automations/RunDetail.tsx`: render placeholders as chips.
  - `frontend/src/types/index.ts`

**Behaviour:** as in the spec's UI section. Read `backend/app/api/v1/secrets.py` and `schemas/secret.py` for the exact data shapes.
- **Components and theme:** use the shared `components/layout/Modal.tsx` and the light theme. Follow the patterns in `ThreatIntelCard.tsx`: write-only secret fields with a "set" hint, `formFrom`, and error-detail parsing.
- **Client-side host validation:** mirror `hosts.valid_host_pattern`, without the allowlist exception. IPs and single labels show the hint "must be on the tenant HTTP allowlist" and are left for the server to decide.
- **Inserting a secret:** add `{{ secrets.NAME }}` at the input's caret, or append it when the caret is unknown.
- **"Move to secret":**
  1. Open the Add dialog with the name, the host taken from the node URL, and the value pre-filled from the current literal.
  2. On a successful create, rewrite the header value to the template, keeping any `Bearer ` prefix.
  3. Mark the workflow dirty. The user saves as usual.
- **Run detail:** render each `⟦secret:NAME⟧` as a small mono chip with a lock icon and `NAME`. Values never exist client-side.
- **Scan banner:** show it only to admins when `GET /secrets/scan` returns items. Dismissal is stored in localStorage under the key `secrets-scan-dismissed:<tenantId>`. The review dialog lists the items with editable suggested names and a "Convert" button per workflow that calls `/secrets/convert/{id}`.
- **Hard rules:** no `dangerouslySetInnerHTML`, no `window.confirm`, `alert` or `window.open`, and no secret value in `localStorage` or console output.

- [ ] Implement.
- [ ] Verify:
  - `npm run build` passes.
  - eslint shows no new errors in the touched files.
  - Rebuild the container.
  - `/` returns 200.
- [ ] Commit as `feat(secrets): secrets card, editor picker, plaintext-credential migration`.

---

### Task 9: Docs and end-to-end check

- [ ] **Docs:** add a "Workflow secrets" section to `docs/configuration.md` covering:
  - the model;
  - allowed hosts and wildcard rules;
  - the placeholder and redaction;
  - where secrets can be used;
  - roles;
  - scan and convert;
  - limitations (no transforms, HTTP only, historical runs are not rewritten);
  - how to purge old run steps that contain plaintext.

  For the purge, give a SQL example that touches only the `workflow_run_steps` rows of a given workflow, with an explicit `WHERE` clause and a backup warning.

  Also add a short entry to `docs/features.md`.
- [ ] **In-container end-to-end check:**
  1. Create a temporary tenant, a secret, and a workflow with an HTTP node using `{{ secrets.X }}` pointed at a local mock server. The server can be a short `python -m http.server`-style echo stub bound inside the container on 127.0.0.1. Add 127.0.0.1 to the temporary tenant's allowlist and to the secret's allowed hosts.
  2. Execute the run via the runtime.
  3. Show that the stub received the real value, while `step.input`/`output` hold only the placeholder or `••••`.
  4. Clean up by id.
  5. Confirm the leak count is 0.
  6. Record everything in the report.
- [ ] Commit as `docs(secrets): workflow secrets configuration and feature notes`.
