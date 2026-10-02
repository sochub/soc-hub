# Workflow Secrets Store — Design

Date: 2026-10-02 · Status: approved in brainstorming · Branch: `feat/workflow-secrets` (stacked on `feat/incident-reports` #40; retarget to `main` once #40 merges)

## Goal

Workflow HTTP request steps currently carry credentials as plain text, for example `Authorization: Bearer …` headers or `?apikey=` query values. Anyone who can view the workflow can read them, they are stored unencrypted, and they are copied into every run's `step.input`.

This feature adds a per-tenant secrets store:

- Admins save named secrets: an encrypted value, plus the hosts the secret may be sent to.
- Workflows reference a secret as `{{ secrets.NAME }}` in an HTTP step's URL, headers or body.
- The real value exists only at the moment the request is sent. It is redacted from outputs and errors and never persisted in run data.

## Decisions (from brainstorming)

| Topic | Decision |
|---|---|
| Where secrets are allowed | HTTP request step only: URL (including query), header values, and body. The resolution and redaction mechanism is shared and generic, so other external-call steps can adopt it later. |
| Exfiltration guard | Each secret has `allowed_hosts`. At send time the request host must match each secret it uses. An empty list means the secret is unusable. |
| Persistence | `step.input` and run history only ever contain an opaque placeholder `⟦secret:NAME⟧`. |
| Migration | A banner, plus per-workflow "Convert" and per-header "Move to secret" helpers. No automatic rewriting. |

## Data model — `tenant_secrets` (migration `c7d8e9f0a1b2`, `down_revision = "b6c7d8e9f0a1"`)

| Column | Type | Notes |
|---|---|---|
| `id` | int PK | |
| `tenant_id` | FK tenants CASCADE, indexed | |
| `name` | str(64), not null | Must match `^[A-Z][A-Z0-9_]{1,63}$`. Unique with `tenant_id` via `uq_tenant_secret_name`. |
| `value_enc` | Text, not null | Fernet ciphertext from `app.utils.crypto.encrypt`. |
| `allowed_hosts` | JSON list[str], not null, default `[]` | |
| `description` | str(500) null | |
| `created_by`, `updated_by` | FK users null | |
| `created_at`, `updated_at` | timestamptz | |
| `last_used_at` | timestamptz null | |

The value is limited to 1–8192 characters before encryption.

### Host rules — `app/secrets/hosts.py`

- `valid_host_pattern(p)` accepts:
  - a lowercase hostname (labels `[a-z0-9-]`, a letter-containing TLD, at most 253 characters);
  - `*.` followed by such a hostname;
  - an IP literal or a single-label name, but only if it appears verbatim in the tenant's `workflow_http_allowlist`.
- `host_allowed(host, patterns)`:
  - Lowercases the host and strips a trailing dot.
  - An exact pattern matches only on equality.
  - `*.example.com` matches `a.example.com` and `a.b.example.com`. It does **not** match `example.com`, `evil-example.com` or `example.com.evil.com`.
  - Ports are ignored.
  - IPv6 brackets are stripped.

## Placeholders and resolution — `app/secrets/resolve.py`

### Stage 1: template rendering (existing `app/workflows/templating.py`)

The rendering context `ctx` gets `secrets`, a `SecretsNamespace` object:

- Attribute access `secrets.NAME` returns a `SecretRef(name)`.
- `SecretRef.__str__` returns the placeholder `⟦secret:NAME⟧`.
- Any other operation on a `SecretRef` raises `TemplateError("secrets can only be inserted, not transformed")`. This covers filters, comparisons, indexing, `len`, arithmetic, `__add__`, `__format__` with a spec, and so on.
- Jinja's sandbox calls `str()` when it interpolates `{{ secrets.X }}` into a string, which yields the placeholder.
- A single-expression render (`"{{ secrets.X }}"` alone) would return the `SecretRef` object itself. `render()` must convert `SecretRef` results to their placeholder string.

`build_context` always includes `secrets`. The real values are never put into `ctx`.

Expressions such as trigger filters and branch conditions also see `secrets`. Using it there raises the same `TemplateError`, because comparisons are blocked.

### Stage 2: send time (in `app/workflows/nodes/http.py`)

`resolve_for_request(db, tenant_id, url, headers, body, host) -> (url, headers, body, used: dict[name, value])` does the following:

1. Finds placeholders with `⟦secret:([A-Z][A-Z0-9_]{1,63})⟧` in the URL, every header value, and the body. For a dict or list body, it walks every string leaf recursively.
2. Loads those secret rows for `tenant_id` in one query.
   - A missing name raises `NodeError("Unknown secret NAME")`.
   - A value that fails to decrypt raises `NodeError("Secret NAME could not be decrypted — re-enter it in Integrations")`.
3. For each secret, if `host_allowed(host, row.allowed_hosts)` is false, raises `NodeError("Secret NAME is not allowed for host HOST")`.
4. Substitutes the values.
   - **URL:** substitute into the query **after** URL-encoding the value. If the placeholder sits in the path, encode it as a path segment. If it sits in the authority (userinfo), raise `NodeError("secrets are not allowed in the URL host or credentials")`.
   - **Headers:** substitute raw. A value containing CR or LF raises `NodeError` (header injection guard).
   - **Body:** substitute raw into string leaves.
5. Updates `last_used_at = now()` for the used rows. The caller commits as usual.

Ordering inside `run_http`:

1. Parse the URL.
2. `assert_url_allowed` (SSRF), unchanged.
3. Secret resolution and the host check.
4. Send.

`host` is the URL's hostname. When the SSRF guard pins an IP, the check still uses the hostname.

### Redaction — `redact(obj, used)`

- Replaces every occurrence of each used value, its `urllib.parse.quote(value, safe='')` form and its `quote_plus` form with `••••`.
- Applies to:
  - the step output (response status is untouched; response headers and body are redacted);
  - every `NodeError` / `RetryableNodeError` message raised after substitution;
  - truncation markers.
- It walks dicts, lists and strings.
- Values shorter than 4 characters are still redacted. Over-redaction is acceptable.
- Exception text from httpx is redacted before it is put into an error.
- **Logging:** never log values. The only log line is `secret use tenant=%s names=%s host=%s outcome=%s`, at INFO.

### Dry runs

`simulated_output` keeps placeholders, and no resolution happens.

## Validation at workflow save — `app/workflows/validation.py`

In `validate_graph`:

- For `http_request` nodes, scan the raw strings in `url`, header values and `body` for `secrets.` references. Use the regex `\bsecrets\.([A-Za-z_][A-Za-z0-9_]*)`.
  - Unknown names (not in the tenant's `tenant_secrets`) give the error `"Unknown secret NAME"`.
  - Names that don't match the pattern give `"Invalid secret name NAME"`.
- `secrets.` in any other node type, or in any other key of an HTTP node, gives the error `"Secrets are only allowed in HTTP request URL, headers and body"`.
- `secrets.` inside a `{{ … }}` that applies a filter (`|`), an operator or an index gives `"secrets can only be inserted, not transformed"`. This is a best-effort check; the runtime enforces it fully.
- `validate_graph` needs the tenant's secret names. Add an optional parameter `secret_names: set[str] | None`. When it is None, skip the unknown-name check. This keeps existing callers and tests working.
- `api/v1/workflows.py` loads the names for the tenant and passes them on save, update and enable.

## API — `app/api/v1/secrets.py` at `/api/v1/secrets`

All endpoints are scoped to the effective tenant.

| Method and path | Role | Behaviour |
|---|---|---|
| `GET /` | admin | Returns `[{name, description, allowed_hosts, last_used_at, created_at, updated_at, created_by_email, updated_by_email, in_use_by: [{id, name}]}]`. `in_use_by` finds workflows of this tenant whose saved graph JSON (cast to text) contains `secrets.NAME`, using `ILIKE` with a word-boundary check in Python. It never includes the value. |
| `GET /names` | analyst+ | `[{name, description, allowed_hosts}]`, for the editor picker. |
| `POST /` | admin | Body `{name, value, allowed_hosts, description?}`. Validation errors: name pattern → 422; empty value or >8192 chars → 422; each host must pass `valid_host_pattern` (with the tenant allowlist) → 422; duplicate name → 409. Returns 201 with the metadata. |
| `PUT /{name}` | admin | Body `{allowed_hosts?, description?, value?}`. A blank or omitted value keeps the current one; a provided value replaces it. Unknown name → 404. |
| `DELETE /{name}` | admin | 409 `{detail, workflows: [...]}` when `in_use_by` is non-empty, unless `?force=true`. Returns 204. |
| `POST /convert/{workflow_id}` | admin | Moves literal credentials into secrets (see below). |

**Audit:** create, update and delete call `create_audit_log(entity_type="secret", entity_id=row.id, action=…, changes={"name", "fields": [...]})`. The value never appears in an audit entry.

**Logging:** `secret <op> tenant=%s name=%s outcome=%s`. The value is never logged.

### Conversion helper

`POST /secrets/convert/{workflow_id}` takes the body `{items: [{node_id, location: "header"|"query", key, secret_name}]}`. Each item names a literal credential found by `GET /secrets/scan`.

- **`GET /secrets/scan`** (admin) returns `[{workflow_id, workflow_name, node_id, location, key, host, suggested_name}]` for HTTP nodes whose:
  - header keys are `authorization`, `x-api-key`, `api-key`, `apikey`, `x-auth-token`, `x-apikey` or `private-token`, or
  - URL query keys are `apikey`, `api_key`, `key`, `token` or `access_token`,
  
  and whose value contains no `{{`. For `Authorization: Bearer xyz`, the secret is the token part (`xyz`), and the header becomes `Bearer {{ secrets.NAME }}`.
- **Convert**, per item:
  - creates the secret (`allowed_hosts = [node host]`), or reuses an existing secret with that name only if its value is identical;
  - rewrites the node config to the placeholder template;
  - saves the workflow through the normal update path, so validation runs;
  - writes audit entries.
- Old run history is not modified. The docs explain how to purge it.

## UI

- **Integrations → "Secrets" card** (admin), using the light theme and the shared Modal.
  - Table columns: name (mono), description, host chips, used-by count with a list of names, last used, and Edit/Delete actions.
  - Add/Edit dialog:
    - the name is fixed on edit;
    - the value is `type=password autoComplete=new-password`, never prefilled, with the edit hint "leave blank to keep";
    - an allowed-hosts textarea, one host per line, validated live client-side with the same rules;
    - a description field.
  - Delete uses an inline confirm. On a 409 it lists the workflows and offers "Delete anyway" (`force`).
- **Workflow editor, HTTP node inspector** (`frontend/src/features/automations/NodeInspector.tsx`).
  - Add an "Insert secret" picker beside the URL, header-value and body inputs. It inserts `{{ secrets.NAME }}` at the cursor (or appends it) and shows the secret's allowed hosts.
  - A literal-credential warning on header rows (keys from the scan list, values without `{{`) offers "Move to secret". It opens the Add dialog, prefilled with the value from the field (it is already visible to the editor) and the host, then rewrites the header and saves.
  - Run detail shows placeholders as styled `⟦secret:NAME⟧` chips. The values never exist client-side.
- **Automations list:** if `GET /secrets/scan` returns items, show a banner "N workflows contain plaintext credentials — move them to Secrets" with a review dialog that offers per-item Convert. Dismissal is stored in localStorage per tenant.

## Testing

No network: use `httpx.MockTransport` or a monkeypatched client. Use no real credentials. Delete DB rows by exact id. The `wf-test-%` leak count must be 0.

- **Templating:**
  - `{{ secrets.X }}` renders to the placeholder, both inside a string and as a single expression.
  - Filters, comparisons, indexing, `|length`, `~` concatenation into a filter, and `+` raise `TemplateError`.
  - `secrets` in a trigger filter raises an error.
- **Resolution:**
  - substitution into headers, query (encoded), path and body;
  - placeholder in userinfo → error;
  - unknown secret → error;
  - decrypt failure → clear error;
  - CR/LF in a header value → error;
  - `last_used_at` is updated.
- **Host rules:** a parametrised table, including the `evil-example.com` and `example.com.evil.com` cases, plus ports, IPv6, trailing dots and the empty list.
- **Ordering:** the SSRF guard runs before the secret check. A disallowed host means the transport is never called.
- **Persistence:** after a real run (mock transport), `step.input` contains the placeholder and the DB has no plaintext value anywhere. Check it with a `SELECT` over the run step rows that searches for the value.
- **Redaction:**
  - a mock server echoing the value in its body and headers, raw and URL-encoded, produces `••••` in the step output;
  - a mock that raises an error containing the URL produces a redacted error;
  - a retried error stays redacted.
- **Dry run:** no resolution happens, and placeholders remain.
- **Validation:**
  - an unknown secret on save → 422;
  - `secrets.` in a Slack node → 422;
  - `secrets.` in an HTTP `method` key → 422;
  - a filtered use → 422.
- **API:**
  - roles: an analyst gets 403 on admin routes and 200 on `/names`; another tenant gets 404;
  - the value is never in any response;
  - a blank value on PUT keeps the old one;
  - name and host validation → 422;
  - duplicate → 409;
  - delete in use → 409, and `force` → 204;
  - audit entries have no value;
  - logs never contain the value (caplog).
- **Scan and convert:**
  - a bearer header and an `apikey` query are detected;
  - convert creates the secret with the node host and rewrites the config;
  - the workflow is still valid after conversion.
- **Frontend:** build and lint, plus controller screenshots.

## Out of scope

- Secret versioning or rotation schedules.
- External vaults (AWS Secrets Manager, HashiCorp Vault).
- Per-workflow ACLs.
- Secrets in non-HTTP steps.
- Rewriting historical run data.
