# Alerts triage UI + multi-webhook ingestion

**Date:** 2026-06-18
**Status:** Approved

## Goal

Surface the existing (UI-less) alert ingestion pipeline as a triage queue, and
let each tenant create **multiple named webhooks** so incoming alerts are
attributable to their source tool (Splunk, CrowdStrike, etc.).

## Background

The backend already has alert ingestion with no frontend:
- `POST /alerts/webhook` — ingests an `Alert`, authed by a single per-tenant
  `X-API-Key` (`Tenant.webhook_api_key`), via `deps.get_tenant_from_webhook_key`.
- `GET /alerts/` — lists the tenant's alerts.
- `POST /alerts/{alert_id}/promote/{case_id}` — links an alert to an existing case.

There is no UI for any of it, and only one key per tenant (so source attribution
relies on caller-supplied `payload.source`, which is untrusted).

## Data model change

Replace the single `Tenant.webhook_api_key` with a `webhooks` table. Each row is
one named ingestion source.

| column      | type                          |
|-------------|-------------------------------|
| `id`        | PK                            |
| `tenant_id` | FK → tenants.id (indexed)     |
| `name`      | string, not null (source label) |
| `api_key`   | unique string (`whk_…`), indexed |
| `created_at`| timestamp                     |

**Migration (Alembic):**
1. Create `webhooks` table.
2. Data-migrate: for each tenant with a non-null `webhook_api_key`, insert a
   `webhooks` row `(tenant_id, name="Default", api_key=<existing key>)`.
3. Drop `tenants.webhook_api_key`.

Downgrade reverses: re-add column, copy back one key per tenant, drop table.

## Backend

### Ingestion (`alerts.py`)
- New dependency `deps.get_webhook_from_key`: resolves `X-API-Key` → active
  `Webhook` whose tenant `is_active`; 401 otherwise. Returns the `Webhook`.
- `POST /alerts/webhook` uses it: `tenant_id = webhook.tenant_id`, and the
  alert's `source` is **stamped from `webhook.name`** (no longer trusted from the
  payload). `AlertCreate` drops the required `source` field for this path.
- Remove the old `get_tenant_from_webhook_key` (single usage).

### Dismiss (`alerts.py`)
- `POST /alerts/{alert_id}/dismiss` — tenant-scoped; sets `status="dismissed"`.
  404 if not found in tenant.

### Webhook management (`integrations.py`, `require_admin`, active-tenant scoped)
- `GET /integrations/webhooks` — list the active tenant's webhooks.
- `POST /integrations/webhooks {name}` — create; returns the row incl. `api_key`.
- `DELETE /integrations/webhooks/{id}` — revoke (hard delete; key stops working).
- Key generation reuses `generate_webhook_key` (un-privatize it in `tenants.py`,
  or move to a shared spot — no new module if avoidable).

### Tenants cleanup (`tenants.py`, `schemas/tenant.py`)
- Remove `webhook_api_key` from tenant create and from the tenant schema.
- Remove the `POST /tenants/{id}/rotate-webhook-key` endpoint (now obsolete;
  rotation = delete + create a webhook).

### Promote / create-case
- Promote-to-existing: unchanged.
- "Create new case from alert": **client-side** — `POST /cases/` (pre-filled from
  the alert) then `POST /alerts/{id}/promote/{newCaseId}`. No backend change.

## Frontend

### New Alerts page
- Sidebar item + `/alerts` route, mirroring Cases/IOCs.
- `features/alerts/AlertsList.tsx`: triage table — **Source • Title • Status •
  Received**, filterable by status (pending/promoted/dismissed) and by source.
- Row actions:
  - **Promote** → modal to pick an existing case (dropdown of `GET /cases/`).
  - **Create case** → pre-fills + creates a new case from the alert, then links.
  - **Dismiss** → `POST /alerts/{id}/dismiss`.
  - **Expand** → raw `payload` JSON, read-only.
- React Query for fetch + mutations, invalidate `['alerts']` on action. Matches
  `CasesList.tsx` styling (glass-panel table, severity/status chip patterns).

### Integrations page (replaces "Coming soon")
- Webhooks manager: list webhooks (name, key with reveal+copy, created), a
  **Create webhook** form (name → shows key + sample `curl`), and **Revoke**.
- Admin-only; non-admins see an "ask an admin" note.

### Types
- Add `Alert` and `Webhook` to `types/index.ts` (or local interfaces, matching the
  existing per-file `interface` convention in `CasesList.tsx`).

## Tests

- `POST /alerts/webhook` with a valid webhook key → alert created in that tenant
  with `source == webhook.name`; revoked/invalid key → 401.
- `POST /alerts/{id}/dismiss` → status becomes `dismissed`; cross-tenant → 404.

## Scope / decisions

- Webhook keys stored and displayed in plaintext — consistent with the existing
  implementation (internal SOC tool); not introducing new exposure.
- No `is_active` flag on webhooks — revoke is a hard delete. Alerts keep the
  source as a string, so deletion never orphans data.
- The single-key rotate endpoint is removed, not kept for back-compat.

## Out of scope

- Editing/back-filling `source` on already-ingested alerts.
- Per-webhook rate limiting / signing secrets.
- Bulk alert actions.
