# Subsystem B — Copilot Actions (propose → confirm)

**Status:** Implemented 2026-06-08 (migration `e1f2a3b4c5d6`). Part 2 of 3 (A widget done, C correlation-memory still future).

## Mechanism
Works with `llama3` (no native tool-calling). The chat system prompt (`ACTIONS_GUIDE`
in `ai_service.py`, appended in both case & general modes) tells the model to emit a
fenced ` ```action ` JSON block when the user wants to *do* something. The backend
`extract_action()` parses it, `strip_action_blocks()` cleans the displayed text, and
the proposal is stored on `copilot_messages.action` (JSON column) and returned as
`CopilotMessageSchema.action`. The frontend renders a confirm card; nothing writes
until the user clicks Confirm (reads auto-run).

## Actions (`POST /copilot/actions/execute`)
| type | write? | scope | notes |
|---|---|---|---|
| create_case | ✅ | active tenant | title + severity + description |
| add_artifact | ✅ | current case | reuses non-isolated artifact, links via case_artifacts, timeline event |
| add_timeline_note | ✅ | current case | event_type='comment' |
| update_case | ✅ | current case | status / severity |
| find_related | read | active tenant | correlate IOC/artifact values across other cases (auto-runs) |

Writes require analyst-or-above in the active tenant (super_admin ok; viewers blocked),
are tenant-scoped, and write an audit log.

## Files
- Backend: `schemas/copilot_action.py`, `ai_service.py` (ACTIONS_GUIDE, extract_action,
  strip_action_blocks), `models/copilot_session.py` (CopilotMessage.action JSON),
  migration `e1f2a3b4c5d6`, `api/v1/copilot.py` (chat extracts action; `execute_action`
  endpoint; `_find_related_cases`).
- Frontend: `types/index.ts` (CopilotAction/ActionResult/RelatedCase),
  `features/copilot/CopilotActionCard.tsx`, wired into `CopilotChat.tsx`.

## Verified
All 5 actions execute correctly via the endpoint; the live chat path with llama3
emits a valid `create_case` action block that the backend extracts. End-to-end on the
running stack.

## Not done (Subsystem C)
Persistent cross-incident memory / embeddings. `find_related` here is a live SQL
correlation by shared values, not stored memory.
