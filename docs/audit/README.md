# Platform audit log

A tenant-scoped, append-only record of who did what. Viewed in **Settings → Audit log**; exportable as CSV.

Code: `backend/app/audit/` (`middleware.py`, `events.py`, `redact.py`, `context.py`), API in `backend/app/api/v1/audit_logs.py`.

## Categories

| Category | What | Written by |
|---|---|---|
| `change` | Field-level diffs on an entity (e.g. case status old -> new). Also the case **Audit** tab. | `create_audit_log()` |
| `write` | Every `POST/PUT/PATCH/DELETE`, action `METHOD /route/{template}`, with status code and duration. | `AuditMiddleware` |
| `security` | Auth and credential events (below), plus `http.denied` for any 401/403. | `audit_event()` |
| `read` | Opt-in sensitive reads (below). | `audit_read()` dependency |

A write usually produces a `write` row and (when fields change) linked `change` rows, shown under **Related**. Rows hold the matched route template only, never request bodies or query strings.

## Security events

`login.success`, `login.failed`, `login.locked`, `login.mfa_required`, `mfa.challenge_passed`, `mfa.challenge_failed`, `mfa.enabled`, `mfa.disabled`, `mfa.reset`, `password.changed`, `tenant.switch`, `logout`, `sso.success`, `sso.failed`, `http.denied`.

- Events are queued on `ctx.events` and written by `AuditMiddleware` after the handler finishes, so `status_code` and `duration_ms` are filled in.
- Failed logins store only `{"email_domain": ...}`. Tenant attribution runs one uniform email-keyed query and counts only if the user has exactly one membership. The malformed-username branch stores the domain only if it matches `^[a-z0-9.-]{1,253}$`.
- SSO failures store a fixed reason code: `not_enabled`, `saml_invalid`, `no_email`, `not_allowed`.
- The provisional tenant from the token applies only when the user is a member.
- Guest sign-in events and guest actor context are a follow-up once the case-guests PR (#59) merges.

## Sensitive reads

`case.view`, `attachment.download`, `report.view`, `report.export`, `secret.list`, `secret.names`, `secret.scan`, `audit.view`, `audit.export`.

## Confidentiality rules

`redact.scrub()` is the one place that decides what is stored in `changes`. Any dict key matching this regex (case-insensitive) has its value replaced with `***`, recursively:

```
password|passwd|secret|token|api[_-]?key|key_enc|value_enc|mfa_secret|otp|totp|authorization|cookie|private|credential
```

Sole exemption: a key ending in `_updated`, `_fields` or `_names` whose value is a list of identifier strings matching `^[a-z0-9_]{1,64}$` (field-name lists such as `secrets_updated: ["api_key"]`, never values). Anything else under a sensitive key is masked. Canary tests assert no secret value reaches a row.

## Who can see what

- **Tenant admins**: their active tenant's rows.
- **Super admins**: also **Platform** scope (`tenant_id` NULL): tenant create/update/delete, and the super admin's own `audit.view` / `audit.export`. `?tenant_id=` naming a nonexistent tenant returns 404.
- **Analysts and viewers**: no Audit log tab; `/settings/audit` is blocked by `RequireRole`.
- **Case Audit tab** (`GET /audit-logs/{entity_type}/{entity_id}`): `change` rows only.

## CSV export

`GET /audit-logs/export` honours the list filters. Cells starting with `=`, `+`, `-`, `@`, tab or CR are prefixed with `'` to neutralise spreadsheet formulas. JSON output is raw.

## Extending

- **New write route**: audited automatically, nothing to do.
- **New sensitive read**: `@router.get(..., dependencies=[audit_read("thing.view")])`.
- **Exclude a write route**: add `"METHOD /route/template": "reason"` to `AUDIT_EXCLUDED_ROUTES` in `middleware.py`. `tests/test_audit_guarantees.py` fails if an entry names a route that doesn't exist.
- **New security event**: `await audit_event("security", "thing.happened", request=request, ...)`.
- Route templates come from `middleware._route_template(scope)`, which relies on a private FastAPI attribute (FastAPI 0.142.2). The guarantees test cross-checks it against OpenAPI, so an upgrade that breaks it fails loudly.
- Tests: a conftest autouse fixture deletes every audit row a test inserts.

## Failure mode

Fail-open. If an insert fails the request still succeeds and the error is logged as `audit insert failed request_id=...`.

## Foreign keys

- `user_id` is `ON DELETE SET NULL`; the `user_email` snapshot on the row is kept.
- `tenant_id` is `ON DELETE CASCADE`: deleting a tenant deletes its audit rows.

## Out of scope

Retention/pruning, hash chaining or tamper evidence, alerting on events, guest actor context (follow-up to #59).
