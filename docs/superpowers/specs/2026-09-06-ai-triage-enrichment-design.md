# AI Triage & Enrichment — Design

**Status:** Approved, implementing directly (1 of 3 planned major updates —
see [SLA & Escalation Engine] and [Incident Reporting & Export], not yet
designed).

## Purpose

On case creation (and on demand), have the local-LLM Copilot analyze a case
and propose: severity + tags, a matching imported playbook, related cases,
and a short next-steps note — each independently confirmable, the same way
existing Copilot actions work.

## Data model

New table `case_triage_results` — one row per triage **run** (re-triage
inserts a new row rather than mutating the last one, so confirm/dismiss
history from a prior run is preserved; only the latest row is shown):

- `id, case_id, tenant_id, created_at`
- `triggered_by ('auto_on_create'|'manual'), triggered_by_user_id`
- `status ('pending'|'completed'|'failed'), error_message`
- `proposed_severity, proposed_tags (JSON), severity_tags_status ('proposed'|'confirmed'|'dismissed')`
- `proposed_playbook_template_id, playbook_status`
- `related_case_ids (JSON)` — display-only, no confirm/dismiss
- `next_steps_text, next_steps_status`

## Endpoints (`app/api/v1/case_triage.py`, prefix `/cases`)

- `POST /{case_id}/triage` (analyst-or-above) — inserts a pending row,
  enqueues the Celery task, returns 202 + the pending row.
- `GET /{case_id}/triage/latest` — most recent row, 404 if none.
- `PATCH /{case_id}/triage/{triage_id}` `{field, status}` — sets one
  sub-item's status to `confirmed`/`dismissed` (a plain state update; the
  actual write, when confirming, happens first through the existing
  `POST /copilot/actions/execute`).

New action type `apply_playbook` added to the Copilot action executor,
wrapping the existing case_tasks `apply-playbook` logic (extracted into a
shared helper so both call sites share one implementation). `update_case`
and `add_timeline_note` — both already existed — are reused as-is for the
severity/tags and next-steps confirmations.

## Celery task (`app/tasks/triage.py`)

Runs async DB work via `asyncio.run(...)` inside a sync Celery task (no
existing precedent for async DB access from a task in this codebase, but the
whole app's ORM layer is async, so this is the correct approach):

1. Load the case + its artifacts/IOCs.
2. Call `AIService.generate_triage()` (new method, local Ollama, `format:
   json`, temperature 0) for severity + tags + next-steps text. Malformed or
   unavailable → task marks the row `failed` with `error_message`; never
   blocks case creation.
3. Related cases: reuse the existing `_find_related_cases` correlation query
   from `app/api/v1/copilot.py` (IOC/artifact value-match) — no new
   correlation logic.
4. Playbook match: **deterministic** keyword-overlap against the tenant's
   imported playbook categories (phishing, malware, ransomware,
   unauthorized-access, exfiltration, credential-access) — never LLM-guessed,
   so it can't suggest a playbook that isn't imported or doesn't exist.
   Below-threshold → left null, and that sub-section is omitted client-side.

## Trigger points

- `POST /cases` enqueues the task after commit (fire-and-forget,
  non-blocking).
- CaseDetail gets a "Re-triage" button (analyst-or-above only).

## UI

A new panel on `CaseDetail`, directly below the "Initial Report" box (in the
always-visible header area, not inside a single tab) — matching the light
"Telemetry Console" theme (zinc/accent tokens), not the dark Copilot panel
styling, since it lives on the case page rather than in the chat feed.
States: empty → pending (spinner, polled via `GET /triage/latest` every 3s)
→ completed (4 sub-sections, each independently Confirm/Dismiss except
related-cases which are plain links) → failed (inline error + retry).
Viewer role sees it read-only.

## Testing

Backend: unit tests for the Celery task's parsing/matching logic (mocked
Ollama), API tests for tenant isolation and role gating on all 3 endpoints.
Frontend: component coverage of the 4 panel states and the confirm/dismiss
wiring. Manual pass with a phishing-themed seed case end to end.
