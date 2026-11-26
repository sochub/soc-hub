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

## Editing before Confirm
The card's Edit toggle turns the preview into inputs. `POST /copilot/actions/execute` takes an optional
`edits` object next to `message_id`; the stored proposal stays the base and only that action's editable keys
are applied over it (`_EDITABLE` in `api/v1/copilot.py`):

| type | editable |
|---|---|
| add_timeline_note | `content` |
| add_artifact | `value`, `artifact_type`, `description` |
| create_case | `title`, `severity`, `description` |
| update_case | `tags` |

Any other key, or a value that fails its check (type, length, enum), is a 422 and the proposal stays
confirmable. The action type, target case, status and a new case's attached indicators cannot be edited. The
meta-note filter is for model output and is not applied to what a person typed. After the run the proposal
holds the params that were written and `edited: true`, so the card shows what happened after a reload. Role
checks and the lifecycle rules (409 for a cancelled, dismissed or already-run proposal) are unchanged.

## Stuck claims
The `running` claim is stored with `claimed_at`. A claim older than `CLAIM_TTL` (2 minutes,
`schemas/copilot_action.py`) no longer counts: the session read shows the proposal as confirmable again and
Confirm or Cancel work. An action is a few DB writes, so a claim that old can only belong to a worker that
died; the window is deliberately far above any real run because writes are not idempotent.

## Defanged indicators
`refang()` in `utils/copilot_heuristics.py` (shared with the Slack war-room extractor) undoes
`hxxp://`, `[.]`, `(.)`, `{.}`, `[dot]`, `[at]`, `[@]` and `[:]` before indicator detection on the analyst's
messages, for the add-artifact suggestions and for the indicators attached to a new case. The stored
artifact value is the real one.

## Streaming (`POST /copilot/chat/stream`)
Same request as `POST /copilot/chat`, answered as Server-Sent Events:

- `delta` `{text}`: the first-pass reply as the model writes it. Text inside a code fence is held until the
  fence closes; a block that is an action is dropped, any other is sent. The client never receives a raw
  action block.
- then exactly one of `message` (the stored assistant message, the same JSON `POST /copilot/chat` returns)
  or `error` `{code, detail}` with code `unavailable`, `timeout` or `provider`.

Everything refused before the model is called (auth, session, case mismatch, message too long) is a normal
HTTP error, as on the plain endpoint. Both endpoints share `_open_turn` / `_close_turn`, so action
resolution, validation, target case and suggestions are one code path. On a provider failure or when the
client disconnects (Stop button, closed tab) generation stops and nothing is stored, the user turn included.
The response carries `X-Accel-Buffering: no` so nginx passes events through as they are written.

`app/ai/llm.py` `stream_with` / `stream` go through the same provider config, SSRF and no-redirect
protections and error mapping as the plain call, with the timeout applied to the start of the response and
to each chunk. Streaming skips the Redis answer cache and in-flight dedup. A provider that cannot stream
answers in one chunk.

The UI (`features/copilot/chatStream.ts`) reads the stream with `fetch`; it falls back to the plain endpoint
when the browser has no `ReadableStream`, when the stream route is missing (older backend), and on a 401 so
the session-refresh path runs.

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
