# Subsystem A — Global Copilot Widget (design + build record)

**Status:** Implemented 2026-06-08. Part of a 3-subsystem copilot effort (A widget → C cross-incident memory → B actions). See also B/C when specced.

## Decisions
- **Form factor:** floating launcher (bottom-right) + right slide-over panel, on every protected view.
- **Context:** auto-scope to the open case on `/cases/:id`; general tenant-level assistant elsewhere. Replaces the embedded CaseDetail sidebar (one copilot UI).
- **Sessions:** `copilot_sessions.case_id` nullable. Existing per-case sessions + one persistent general session per user (`case_id = NULL`).

## Backend changes
- Migration `c9d0e1f2a3b4`: `copilot_sessions.case_id` → nullable.
- `models/copilot_session.py`: `case_id` nullable.
- `schemas/copilot.py`: `CopilotChatRequest.case_id` and `CopilotSessionSchema.case_id` optional.
- `services/ai_service.py`: `_format_general_context()`, `generate_general_welcome()`, static fallback, general framing in `chat()`.
- `api/v1/copilot.py`: `GET /copilot/sessions/general` (get-or-create general session w/ briefing); `_build_general_context(db, tenant_id)` (lightweight current-state summary: open-case counts by severity, ~10 recent cases, IOC count — NOT correlation); `chat` accepts optional `case_id`.

## Frontend changes
- `features/copilot/CopilotChat.tsx`: reusable chat (extracted from CopilotSidebar) — handles case mode (`caseId`) and general mode.
- `features/copilot/CopilotWidget.tsx`: floating launcher + slide-over; derives `caseId` from the route.
- `components/layout/Layout.tsx`: mounts `<CopilotWidget />` once.
- `features/cases/CaseDetail.tsx`: embedded sidebar removed; main column reclaims width.
- `CopilotSidebar.tsx` removed (superseded by CopilotChat).

## Boundaries (other subsystems)
- No cross-case correlation/memory retrieval (C). General context = current-state summary only.
- No actions/tool-calling (B). Chat only. Stays on `llama3`.
