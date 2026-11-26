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
| create_case | ✅ | active tenant | title + severity + description; indicators found in this message and the three user turns before it (newest first, 10 at most) are attached |
| add_artifact | ✅ | current case | reuses non-isolated artifact, links via case_artifacts, timeline event |
| add_timeline_note | ✅ | current case | event_type='comment' |
| update_case | ✅ | current case | status / severity / tags. Tags are **added** to the case's tags (de-duplicated), never replaced. Closing asks for an outcome and a closing note on the card |
| find_related | read | active tenant | correlate IOC/artifact values across other cases (auto-runs) |

Writes require analyst-or-above in the active tenant (super_admin ok; viewers blocked),
are tenant-scoped, and write an audit log.

## Parsing the model's reply
- An action block is a fenced block (` ```action `, ` ```json ` or unlabelled, closing fence optional) whose JSON
  is an object with a known `type`. Only those are removed from the displayed reply; any other code block
  (a sample payload the analyst asked for) is shown as written.
- `params` must be an object. Fields written next to `type` (flat) are folded into `params`; anything else
  drops the action. The proposal is validated with `ActionProposal` before it is stored, and a malformed
  action already in the table is dropped when the session is read instead of failing the read.
- Second pass (`force_action_extraction`): only when the first reply had no block and the message contains a
  command word as a whole word (`_ACTION_KEYWORDS`; "stage", "markdown", "blocked?" don't count). It receives
  the three turns before the message so "yes, add it" has a referent.
- Provider failure: `/copilot/chat` answers 503 (504 on a timeout, 502 for other provider errors) and stores
  nothing for that exchange. The UI shows it as a status line and gives the typed text back.

## Prompt size
`PROMPT_CHAR_BUDGET` (`ai_service.py`) caps the chat prompt in characters, not tokens. Over it, the oldest
turns are dropped first, then the timeline is shortened. The actions guide sits before the case data, so a
provider that truncates the end of a long prompt cannot cut it. The timeline section holds the 30 most recent
entries plus every pinned one and says how many earlier entries were left out; artifacts are listed newest
first, 50 at most, with the omitted count.

## Notes and suggestions (`utils/copilot_heuristics.py`)
- A note request with literal text ("add a note that …", "record that …", "add a comment: …", quoted text
  in double quotes) saves exactly that text.
- A request to compose ("write a comment summarizing …", "create a timeline of …") or a referential one
  ("add this to the timeline") has no literal text: the note is composed from the analyst's own messages.
- A message that asks for another action first ("create a case and add a note …") is left to the model.
- The "record this in the timeline?" chip is offered only for statements: not for questions, commands, or a
  message that already produced an action.

## Target case
The case a proposal acts on is fixed when it is proposed and stored as `case_id` on the proposal: the
session's case, or in the general session a case the analyst named ("case #12"), else the case most recently
created from that conversation. The card shows "→ case #12 · title" before Confirm. With no target the card
says so and Confirm is disabled (the server answers 400); a target deleted since is shown as such.
`/copilot/chat` takes the case from the session; a `case_id` that disagrees with it is a 400.

## Proposal lifecycle
State lives in the proposal JSON on `copilot_messages` (no extra column): `result` once it ran, or `state`
= `cancelled` / `dismissed` (`POST /copilot/actions/dismiss`) / `running` (claimed under a row lock while a
confirmed write executes). A write runs once: a second Confirm, or a Confirm of a cancelled or dismissed
proposal, is a 409. A failed run releases the claim so it can be confirmed again. `find_related` stores its
result too, so the card does not query again each time the panel opens.

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
