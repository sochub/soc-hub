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

`change` rows do carry before/after **field values** (for example a case description or an IOC value): that is what the case **Audit** tab shows. Only keys matching the confidentiality regex below are masked. Workflow-run rows are the exception: they store only the workflow name, trigger event, `case_id`/`alert_id` and the trigger's key names; the full trigger payload stays in `workflow_runs.trigger_payload`.

## Security events

`login.success`, `login.failed`, `login.locked`, `login.mfa_required`, `mfa.challenge_passed`, `mfa.challenge_failed`, `mfa.enabled`, `mfa.disabled`, `mfa.reset`, `password.changed`, `tenant.switch`, `logout`, `sso.success`, `sso.failed`, `sso.replay`, `guest.login.link`, `guest.login.password`, `guest.login.mfa`, `guest.login.sso`, `guest.login.register`, `http.denied`.

Guest sign-ins are `security` events with `actor = guest:<email>`. Link and SSO sign-ins are attributed to the grant's tenant; password, MFA and register sign-ins are platform-level (a password session spans tenants). A failed attempt has `outcome = denied`, no actor, and `changes = {"email_domain": ...}` only (never the full email). Guest requests on a case carry `actor = guest:<email>` and the grant's tenant.

- Events are queued on `ctx.events` and written by `AuditMiddleware` after the handler finishes, so `status_code` and `duration_ms` are filled in.
- Failed logins store only `{"email_domain": ...}`. Tenant attribution runs one uniform email-keyed query and counts only if the user has exactly one membership. The malformed-username branch stores the domain only if it matches `^[a-z0-9.-]{1,253}$`.
- SSO failures store a fixed reason code: `not_enabled`, `saml_invalid`, `no_email`, `not_allowed`.
- The provisional tenant from the token applies only when the user is a member.
- Tenant resolution (`context.effective_tenant`): a tenant resolved by `get_effective_tenant_id` wins; otherwise a `require_super_admin` route is platform-level (tenant NULL), even when the super admin is a member of the active tenant; otherwise the provisional tenant. Events that pass `tenant_id` explicitly (login and similar) keep it.
- A queued event on a request that ends in a 5xx is written with `outcome = "error"`.
- MFA sign-ins record `login.success` alongside `mfa.challenge_passed`, and setting up MFA during sign-in records it alongside `mfa.enabled`.
- `ip` is the `X-Real-IP`/peer address only if it parses as an IP address, else the socket peer, else NULL.
- Anonymous 401s are recorded per request (one `http.denied` row each). In production put an nginx `limit_req` zone in front of the API so a flood of unauthenticated requests can't flood the table.
- In Slack HTTP mode, signed Slack requests (`POST /api/v1/slack/interactions`, `/commands`, `/events`) are recorded as platform-level `write` rows (tenant NULL). In Socket Mode (the default) they never reach the HTTP API, so they produce no such rows; the case changes they cause are still in the case audit trail.

## Sensitive reads

`case.view`, `attachment.download`, `report.view`, `report.export`, `secret.list`, `secret.names`, `secret.scan`, `audit.view`, `audit.export`.

`audit.view` is recorded only for the first page of a plain list: "Load more" (`before=`) and Related (`request_id=`) lookups are not.

## Confidentiality rules

`redact.scrub()` is the one place that decides what is stored in `changes`. Any dict key matching this regex (case-insensitive) has its value replaced with `***`, recursively:

```
password|passwd|secret|token|api[_-]?key|key_enc|value_enc|mfa_secret|otp|totp|authorization|cookie|private|credential
```

Sole exemption: a key ending in `_updated`, `_fields` or `_names` whose value is a list of identifier strings fully matching `[a-z0-9_]{1,64}` (field-name lists such as `secrets_updated: ["api_key"]`, never values). Anything else under a sensitive key is masked. Canary tests assert no secret value reaches a row.

## Who can see what

- **Tenant admins**: their active tenant's rows.
- **Super admins**: also **Platform** scope (`tenant_id` NULL): tenant create/update/delete, and the super admin's own `audit.view` / `audit.export`. `?tenant_id=` naming a nonexistent tenant returns 404.
- **Analysts and viewers**: no Audit log tab; `/settings/audit` is blocked by `RequireRole`.
- **Case Audit tab** (`GET /audit-logs/{entity_type}/{entity_id}`): `change` rows only.

## CSV export

`GET /audit-logs/export` honours the list filters. Cells starting with `=`, `+`, `-`, `@`, tab or CR are prefixed with `'` to neutralise spreadsheet formulas. JSON output is raw.

The export releases the request's DB session before streaming and fetches each 1000-row batch in its own short-lived session, so no pooled connection is held while a slow client reads.

## Extending

- **New write route**: audited automatically, nothing to do.
- **New sensitive read**: `@router.get(..., dependencies=[audit_read("thing.view")])`.
- **Exclude a write route**: add `"METHOD /route/template": "reason"` to `AUDIT_EXCLUDED_ROUTES` in `middleware.py`. `tests/test_audit_guarantees.py` fails if an entry names a route that doesn't exist.
- **New security event**: `await audit_event("security", "thing.happened", request=request, ...)`.
- Route templates come from `middleware._route_template(scope)`, which relies on a private FastAPI attribute (FastAPI 0.143.0). The guarantees test cross-checks it against OpenAPI, so an upgrade that breaks it fails loudly.
- Tests: a conftest autouse fixture deletes every audit row a test inserts.

## Deploying the migration

Migration `e7a1b2c3d4f5` alters `audit_logs` (exclusive lock) and backfills `user_email` from `users`, so on a large table run it in a maintenance window. Its downgrade **deletes** platform-level and entity-less rows (they can't satisfy the old NOT NULL columns).

## Failure mode

Fail-open. If an insert fails the request still succeeds and the error is logged as `audit insert failed request_id=...`.

## Foreign keys

- `user_id` is `ON DELETE SET NULL`; the `user_email` snapshot on the row is kept.
- `tenant_id` is `ON DELETE CASCADE`: deleting a tenant deletes its audit rows.

## Out of scope

Retention/pruning, hash chaining or tamper evidence, alerting on events.
