# SLA & Escalation Engine — Design

**Status:** Approved, implementing (2 of 3 planned major updates — see
[AI Triage & Enrichment](2026-09-06-ai-triage-enrichment-design.md), done;
Incident Reporting & Export not yet designed).

## Purpose

Track per-severity response/resolution SLA targets on cases, surface
on-track/at-risk/breached status in the UI, and email the case owner +
tenant admins once when a case newly breaches — without requiring anyone to
be looking at the UI for the breach to be caught.

## Data model

New `sla_policies` table — one row per (tenant, severity) **override**; a
missing row falls back to a hardcoded default, so existing tenants need no
backfill:
- `id, tenant_id, severity, response_target_minutes, resolution_target_minutes, created_at, updated_at`
- unique on `(tenant_id, severity)`

Defaults used whenever no policy row exists: critical 15m/4h, high 1h/8h,
medium 4h/24h, low 8h/3d, info — not tracked (`None`/`None`).

Three new nullable columns on `cases`:
- `acknowledged_at` — set once, the first time status moves off `new`
  (mirrors how `resolved_at` already works on status transitions).
- `sla_response_breach_notified_at`, `sla_resolution_breach_notified_at` —
  dedupe markers so the periodic sweep emails once per breach, not every scan.

## Computation (`app/utils/sla.py`)

One shared function, `compute_sla_state(case, policy) -> dict`, used
identically by every call site so there's exactly one definition of
"breached":
- Response clock: `created_at` → `acknowledged_at` (or now, if not yet
  acknowledged).
- Resolution clock: `created_at` → `resolved_at` (or now, if not yet
  resolved).
- Each reports `met` / `on_track` / `at_risk` (>80% of target elapsed) /
  `breached`, or `not_tracked` when the target is `None` (info severity by
  default, or a tenant explicitly clearing a target).

## Endpoints

- `GET /tenants/sla-policies` / `PUT /tenants/sla-policies` (admin-only,
  same gating as SSO config) — list/update the tenant's overrides.
- `GET /cases` and `GET /cases/{id}` compute SLA fields and attach them as
  transient attributes on the ORM objects before serialization (same
  pattern as other derived response fields already in this codebase) — no
  new response-shape endpoints needed.
- `GET /stats/` gains one more field: SLA compliance (% of open cases not
  breached), computed the same way, reusing the open-cases query already
  loaded there.

## Celery beat (embedded in the existing `worker` service via `-B`)

A new periodic task `app.tasks.sla.check_sla_breaches_task`, every 5
minutes: for each tenant, load open cases + that tenant's policy overrides,
run `compute_sla_state`; for any case newly crossing into `breached` (its
marker column still null), email the case owner + tenant admins via
`email_service.py` (extended with `send_sla_breach_email`, no-ops if SMTP
isn't configured — same graceful degradation as invitations) and set the
marker so it fires once. `acknowledged_at` is set at write time by the
existing case-update status-change logic, not by this task.

## UI

- Case list: one compact SLA pill per row (worst-of response/resolution),
  green/amber/red, next to the existing status badge.
- CaseDetail header: a small panel below the AI Triage panel showing both
  clocks with live countdown text.
- Settings: a new admin-only section (matching the existing SSO section's
  layout) — one row per severity with two number inputs (response/resolution
  minutes, blank = not tracked), save button.
- Dashboard: one additional stat tile, "SLA compliance."

## Testing

Unit tests for `compute_sla_state` (on_track/at_risk/breached/met/
not_tracked transitions, policy-override vs default fallback) — pure
function, same pattern as the triage tests. Manual E2E pass: set a very
short SLA target, watch a case flip to breached, confirm exactly one email
fires.
