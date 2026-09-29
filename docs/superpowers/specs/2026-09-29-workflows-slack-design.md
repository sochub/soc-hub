# Workflow Automation + Slack Integration — Design

Date: 2026-09-29
Status: Approved (brainstorming), pending spec review

## Goal

Let tenants automate case/incident handling with a Tracecat-style visual workflow
builder (DAG), and interact with people over Slack — including **end users who are
not platform users** (e.g. "Did you just log in from Brazil? Yes, it was me / No").

Motivating example that must work end-to-end:

> Case created → if tag `X` present → call webhook → DM the end user in Slack asking
> for confirmation → if they answer "No" (or time out), raise severity to critical and
> add a note; if "Yes", add a note and close.

And full alert-to-incident automation:

> Alert ingested from EDR → if severity ≥ 7, promote into a case grouped by host (dedup
> within 6 h) → for each user email in the payload, DM them in Slack → escalate if anyone
> answers "No"; otherwise dismiss low-severity alerts automatically.

Authors must be able to **dry-run** a workflow against a real case/alert without side effects.

## Decisions (from brainstorming)

| Topic | Decision |
|---|---|
| "End user" | Affected employee, **not** a platform user; found in Slack by email |
| Slack connection | **Bring-your-own app** per tenant: admin pastes bot token + signing secret |
| Workflow shape | **Full DAG** with visual canvas (React Flow / `@xyflow/react`) |
| Execution engine | **Celery + run state in Postgres** (no Temporal) |
| SSRF | Block private/loopback/link-local IPs by default; per-tenant host allowlist |
| "Wait for all" node | Dropped — default join semantics already wait for all parents |
| Alerts | First-class: `alert.*` context, promote (new/link/group) + dismiss nodes |
| Loops | `for_each` container node; each item is a child run; no nesting in v1 |
| Dry-run | In v1: read-only nodes execute, side-effecting nodes simulated with mocks |

## Scope

### In v1

**Triggers** (exactly one per workflow)
- `case.created`
- `case.updated` (with `changes` for status / severity / tags / assignee)
- `alert.ingested` (context `alert`: source, external_id, title, payload, status)
- `manual` ("Run workflow" on a case)

Each workflow also has an optional `trigger_filter` expression (e.g. `'phishing' in case.tags`,
`alert.source == 'EDR' and alert.payload.severity >= 7`).

**Logic nodes**
- `condition` — evaluates an expression; `true` / `false` output handles.
- `for_each` — container node (see *Loops*).

**Action nodes** (case nodes act on the run's case by default; optional templated
`case_id` overrides it — needed inside loops)
- `http_request` — method, URL, headers, body (templated). Output `{status, headers, body}` (body parsed as JSON when possible).
- `slack_post_message` — to a channel (default channel if blank); optional case action buttons (Acknowledge / Assign to me / Close).
- `slack_ask_user` — DM a user by email with custom buttons; waits. Output `{response, responder_slack_id, responder_email, timed_out}`.
- `case_update` — severity, status, assignee, add/remove tags.
- `case_add_note`, `case_add_artifact`.
- `case_apply_playbook`.
- `case_search` — read-only; filters: status in, tags contain, title contains, artifact
  value equals, created within N hours. Output `{cases: [...], first, count}` (max 50).
- `alert_promote` — see *Alert automation*.
- `alert_dismiss` — optional templated reason.

### Out of v1 (each addable later as a node/feature without redesign)
Sub-workflows, nested `for_each`, cron triggers, code nodes, email node,
workflow import/export/marketplace, Slack slash commands, per-user Slack OAuth
linking, built-in "notify on X" settings (that's a 2-node workflow).

## Architecture

### Data model (all tables carry `tenant_id`; cross-tenant access → 404)

**`slack_integrations`**
- `id`, `tenant_id` (unique), `team_id` (indexed, set by the Test call), `bot_token_enc`,
  `signing_secret_enc`, `default_channel`, `created_at`, `updated_at`.
- Secrets encrypted with Fernet; key derived from `SECRET_KEY` (SHA-256 → urlsafe b64).

**`workflows`**
- `id`, `tenant_id`, `name`, `description`, `enabled` (bool), `trigger_type`,
  `trigger_filter` (text, nullable), `graph` (JSON `{nodes, edges}`), `version` (int,
  bumped on each save), `created_by`, `created_at`, `updated_at`.
- Node shape: `{id: slug, type, position: {x, y}, parent_id?: for_each node id, config: {...},
  continue_on_error: bool, retries?: int, execute_in_dry_run?: bool, mock_output?: object}`.
- Edge shape: `{id, source, target, source_handle: null | "true" | "false"}`.

**`workflow_runs`**
- `id`, `tenant_id`, `workflow_id`, `workflow_version`, `case_id` (nullable),
  `alert_id` (nullable), `status` (`running` | `waiting` | `succeeded` | `failed` | `cancelled`),
  `trigger_payload` (JSON), `graph_snapshot` (JSON), `depth` (int), `is_dry_run` (bool),
  `dry_run_mocks` (JSON, nullable), `parent_run_id` (nullable), `parent_step_id` (nullable),
  `loop_index` (nullable), `loop_item` (JSON, nullable), `error`, `started_at`, `finished_at`.
- `graph_snapshot` means editing a workflow never affects in-flight runs. For child runs
  (loop items) it holds only the loop body.
- Run lists show top-level runs only (`parent_run_id IS NULL`); children appear under their for_each step.

**`workflow_run_steps`**
- `id`, `run_id`, `node_id`, `status` (`pending` | `running` | `succeeded` | `failed` |
  `skipped` | `waiting` | `cancelled`), `input` (rendered config, JSON), `output` (JSON),
  `error`, `attempt`, `wait_token` (unique, nullable, `secrets.token_urlsafe(24)`),
  `wait_expires_at`, `started_at`, `finished_at`.
- Unique `(run_id, node_id)`.

**`tenants`** gains `workflow_http_allowlist` (JSON list of hostnames, default `[]`).

**`cases`** gains `group_key` (string, nullable, indexed with `tenant_id`) — set by
`alert_promote` in `group` mode.

**`alerts`** gains `dismiss_reason` (text, nullable).

### Code layout

```
backend/app/workflows/
  engine.py        # ready_nodes() (pure), advance_run(), finish_step()
  validation.py    # validate_graph() (pure)
  templating.py    # Jinja2 SandboxedEnvironment render/eval helpers
  events.py        # emit_event(tenant_id, type, case, changes, depth)
  ssrf.py          # assert_url_allowed(url, allowlist)
  nodes/           # one module per node type: async run(ctx, config) -> dict
backend/app/tasks/workflows.py   # Celery: dispatch_event, advance_run, execute_step,
                                 #         resume_wait, expire_waits (beat, 60s)
backend/app/services/slack_service.py  # httpx calls + signature verification
backend/app/api/v1/workflows.py        # CRUD, validate, runs, cancel, manual trigger
backend/app/api/v1/slack.py            # config CRUD/test + public /interactions
frontend/src/features/automations/     # list, editor (canvas), runs, run detail
docs/slack/slack-app-manifest.yml
```

New dependencies: backend `jinja2`; frontend `@xyflow/react`. Slack calls use existing
`httpx` (no Slack SDK). Fernet from existing `cryptography`.

### Event flow

```
cases API / alerts ingest / Slack case buttons
   └─ emit_event(tenant, type, case, changes, depth) ─▶ Celery dispatch_event
        └─ for each enabled workflow with matching trigger_type and truthy trigger_filter:
             create workflow_run (+ snapshot) ─▶ Celery advance_run(run_id)

advance_run(run_id):
   lock run row (SELECT ... FOR UPDATE)
   compute ready/skipped nodes via ready_nodes()
   insert step rows (unique run_id,node_id) → enqueue execute_step for each ready node
   if nothing running/waiting/pending → finalize run status

execute_step(step_id):
   render config with context → run node → store output/error → advance_run(run_id)
```

Event sources: `POST /alerts/webhook` (`alert.ingested`), case create/update endpoints,
workflow case nodes, Slack case buttons. Dry runs never emit events.

`emit_event` is called after the DB commit of the originating change. Events are
emitted for workflow-driven case changes too (so audit + chaining work), with
`depth = run.depth + 1`. **Events with depth > 3 are dropped and logged** (loop guard).

## Execution semantics

### Readiness / joins (implemented as pure `ready_nodes(graph, step_states)`)

- An edge is **active** iff its source step `succeeded` and (for `condition` sources)
  `source_handle` matches the condition's boolean output. A `condition` node's
  `true`/`false` edges are resolved when it succeeds; the non-matching one is inactive.
- An edge is **resolved** iff its source is in a terminal state (`succeeded`, `failed`
  with `continue_on_error` counted as succeeded, `skipped`).
- A node is **ready** when all incoming edges are resolved and ≥1 is active.
- A node is **skipped** when all incoming edges are resolved and none is active. Skips
  propagate.
- The trigger node is succeeded at run creation with output = trigger payload.
- Nodes with a `parent_id` (loop body) are invisible to the enclosing graph's
  readiness; they only run inside child runs.

### Template context

- `case` — the run's case, reloaded fresh before each step (reflects earlier updates);
  includes tags, artifacts summary. `null` until one exists (e.g. before `alert_promote`).
- `alert` — the run's alert (alert-triggered runs), reloaded fresh.
- `trigger` — event payload (`trigger.changes.tags.added`, etc.).
- `steps.<node_id>.output` — outputs of completed steps (child runs also see the parent's).
- `loop.item`, `loop.index` — inside a `for_each` body only.
- `dry_run` — boolean.

Rendering: Jinja2 `SandboxedEnvironment`, `StrictUndefined`. Conditions and
`trigger_filter` are expressions evaluated via `env.compile_expression`.

### Retries / timeouts

- `http_request`: 15 s timeout; `retries` default 2 (backoff 10 s, 60 s), only on network
  errors and 5xx.
- Case action nodes: no retry.
- `slack_ask_user`: `timeout_hours` default 24, max 168. Beat task `expire_waits` every
  60 s: expired waiting steps **succeed** with `{response: null, timed_out: true}`, then
  the Slack message is edited to "This request expired."

### Failure / cancel

- A node failure fails the run; pending steps → `cancelled`. Waiting steps → `cancelled`.
- `continue_on_error: true` → step `succeeded` with `output.error` set.
- Analyst+ can cancel a `running`/`waiting` run; in-flight Celery tasks check run status
  before and after executing and no-op if cancelled.

### Save-time validation (`validate_graph`, pure)

Acyclic; exactly one trigger node, matching `trigger_type`; ≤ 50 nodes (including loop
bodies); unique node ids (slug `[a-z0-9_]+`); every `condition` has its outgoing edges
on `true`/`false` handles and non-condition sources have no handle; all
templates/expressions parse; required config per node type present. Loop rules:
`parent_id` must reference a `for_each` node; no edge crosses a container boundary; no
`for_each` inside a `for_each`; body is non-empty; the trigger is never inside a body.
`alert_*` nodes only allowed when `trigger_type == alert.ingested`. Invalid graphs may be
**saved** but not **enabled** or dry-run (those endpoints re-validate).

## Alert automation

- `alert.ingested` runs have `alert_id` set and `case_id = null`.
- `alert_promote` config: `mode` (`new` | `link` | `group`),
  - `new`: templated `title`, `description`, `severity`, `tags` → create case, link alert
    (`alert.status = promoted`, `alert.case_id`).
  - `link`: templated `case_id` (must belong to tenant) → link alert.
  - `group`: templated `group_key` + `window_hours` (default 24) + the `new` fields. In one
    transaction holding `pg_advisory_xact_lock(hash(tenant_id, group_key))`: find the
    newest case with that `group_key`, status not `resolved`/`closed`, created within the
    window → link; else create a case with that `group_key` and link. Atomic so concurrent
    alert runs in a storm can't create duplicate cases.
  - Output `{case_id, created: bool}`. **Sets `run.case_id`**, so later nodes' `case`
    context and default target are the promoted case.
  - Case creation goes through the same service path as the UI (audit log +
    `case.created` event at `depth + 1`).
- `alert_dismiss`: `alert.status = dismissed`, `dismiss_reason` stored.
- Promoting/dismissing an alert that is no longer `pending` → step fails (clear error).

## Loops (`for_each`)

- Container node; body = nodes whose `parent_id` is the `for_each` id. Config: `items`
  (expression, must evaluate to a list), `concurrency` (default 5, max 20), `max_items`
  (default 100, hard cap 500; exceeding → step fails).
- Execution: the step goes `waiting`; one **child run** per item is created with
  `graph_snapshot` = body (plus an implicit start node feeding body nodes with no
  in-body parents), `loop_item`, `loop_index`, same `case_id`/`alert_id`/`is_dry_run`.
  At most `concurrency` children are `running`/`waiting` at once; when a child finishes,
  the parent step's scheduler (in `advance_run` of the parent) starts the next.
- Children can wait (e.g. `slack_ask_user` per item) independently.
- When all children are terminal: output `{results: [{index, status, steps: {node_id: output}}],
  succeeded: n, failed: n}`; step `succeeded` if none failed, else `failed` (or succeeded
  with the same output when `continue_on_error`). Parent run then advances.
- `run.case_id` changes inside a child do not propagate to the parent.
- Cancelling the parent cancels all children.

## Dry-run

- `POST /workflows/{id}/dry-run` with one of `{case_id}` | `{alert_id}` |
  `{payload}` (sample trigger payload), plus `mocks: {node_id: output}` and
  `ask_user_answers: {node_id: choice_label | "timeout"}`. Allowed on disabled
  workflows; graph must pass validation. Uses the **current** (saved) graph.
- Creates a run with `is_dry_run = true`. Runs through the same engine and Celery tasks.
- Node behaviour:
  - Execute for real (read-only): trigger, `condition`, `for_each`, `case_search`,
    templating.
  - `http_request`: simulated unless the node has `execute_in_dry_run: true` (intended
    for GET lookups/enrichment; SSRF rules still apply). Simulated output =
    `mocks[node_id]` → node `mock_output` → `{status: 200, headers: {}, body: {}}`.
  - Case/alert mutation nodes, `slack_post_message`: simulated; output = mocks or a
    plausible stub (`alert_promote` → `{case_id: null, created: true, simulated: true}`;
    `case` context stays the source case).
  - `slack_ask_user`: never waits; resolves to `ask_user_answers[node_id]` (default first
    button) or `{response: null, timed_out: true}` for `"timeout"`.
  - Every simulated step stores `output.would_do` = its fully rendered input.
- No DB writes to cases/alerts, no Slack/HTTP side effects, no events. The run itself
  and an audit entry ("dry run of workflow X") are stored.

## Security

- **SSRF** (`http_request`): only `http`/`https`; resolve host, reject if any resolved
  IP is loopback / private (RFC1918, ULA) / link-local (incl. `169.254.169.254`) /
  unspecified / multicast, unless hostname is in the tenant allowlist. Redirects not
  followed. Response body capped at 1 MB.
- **Template sandbox**: Jinja2 `SandboxedEnvironment`; no access to dunder attrs.
- **Slack interactions**: see below; nothing is acted on before signature verification.
- **Secrets**: Slack token/secret encrypted at rest, never returned by the API (masked).
- **Permissions**: admins create/edit/enable/delete/dry-run workflows and configure Slack and
  the HTTP allowlist; analysts+ view workflows/runs, trigger manual runs, cancel runs;
  viewers read-only. All mutations and every run start are written to the audit log.

## Slack integration

### Setup
1. `docs/slack/slack-app-manifest.yml`: bot scopes `chat:write`, `im:write`,
   `users:read`, `users:read.email`; interactivity request URL
   `https://<host>/api/v1/slack/interactions`.
2. Settings → Integrations → Slack card: bot token, signing secret, default channel,
   **Test** button → `auth.test` (stores `team_id`) + posts a hello message to the default channel.
3. Local dev needs a public URL (ngrok) for interactions.

### `POST /api/v1/slack/interactions` (public)
1. Parse form body `payload`; look up `slack_integrations` by `team.id` → 401 if none.
2. Verify `X-Slack-Signature` = `v0=` + HMAC-SHA256(signing_secret,
   `v0:{X-Slack-Request-Timestamp}:{raw_body}`), constant-time compare; reject
   timestamps older than 5 min. → 401 on failure.
3. Return 200 immediately; enqueue a Celery task to process; task updates the
   message via `response_url`.

Button `value` formats: `wf:<wait_token>:<choice_index>` and `case:<case_id>:<action>`.

### Ask user
`users.lookupByEmail` → `conversations.open` → `chat.postMessage` (blocks: message +
buttons from node config). While waiting, the step's `output` holds
`{target_slack_id, channel, message_ts}` (needed to authorize the click and edit the message).
On click: wait token must match a `waiting` step of the same tenant, **clicker Slack ID
must equal target**, else ephemeral "This request isn't for you." On accept: record
response, edit message to "✅ You answered: *<label>*" (removes buttons), resume run.
Unknown email → step fails (use `continue_on_error` to branch to a fallback).

### Case buttons
Clicker's Slack email (`users.info`) → platform user with **analyst+ membership in the
tenant**; otherwise ephemeral "Your Slack account isn't linked to a SOC Hub user" and
no change. Actions reuse the same service code paths as the UI (audit log + events fire).
Acknowledge → status `in_progress`; Assign to me → assignee; Close → status `closed`.

## API

- `GET/POST /workflows`, `GET/PUT/DELETE /workflows/{id}`
- `POST /workflows/{id}/validate`, `POST /workflows/{id}/enable`, `POST /workflows/{id}/disable`
- `POST /workflows/{id}/run` `{case_id}` (manual trigger)
- `POST /workflows/{id}/dry-run` `{case_id | alert_id | payload, mocks?, ask_user_answers?}`
- `GET /workflow-runs?workflow_id=&case_id=&alert_id=&status=&dry_run=`, `GET /workflow-runs/{id}`
  (with steps and child runs),
  `POST /workflow-runs/{id}/cancel`
- `GET/PUT /slack/config`, `POST /slack/config/test`, `POST /slack/interactions`
- `GET/PUT /workflows/settings/http-allowlist`

## Frontend

In `frontend/src/features/automations/`, light "Telemetry Console" theme; sidebar item
**Automations**.

- **List**: name, trigger, enabled toggle, last run status/time, run count.
- **Editor**: React Flow canvas; left palette of node types; right inspector with the
  node's config form (templated fields in Roboto Mono with a context-path hint); top bar
  Save / Validate (errors pinned to nodes) / Enable toggle / **Dry run**.
  `for_each` renders as a resizable group (React Flow sub-flow); dropping a node inside
  sets its `parent_id`.
- **Dry-run dialog**: pick a case or alert (searchable) or paste JSON payload; optional
  per-node mock outputs and ask-user answers; submits then opens the run detail.
- **Runs + run detail**: read-only canvas of `graph_snapshot`, nodes colored by step
  status (green succeeded, red failed, gray skipped, amber waiting); click → input,
  output JSON, error; simulated steps show `would_do` with a "SIMULATED" label. Dry runs
  carry a DRY RUN badge and are filterable. `for_each` steps list child runs (index,
  status) with drill-down. Cancel button for running/waiting.
- **Alerts page**: alerts show the automation run that promoted/dismissed them (link).
- **CaseDetail**: "Run workflow ▾" (manual workflows, analyst+) and an "Automation runs"
  list for the case.
- **Settings → Integrations**: Slack card; HTTP allowlist editor.

## Testing

Pure-function unit tests in `backend/tests/` (existing style):
- `ready_nodes`: linear chain, diamond join, condition true/false + skip propagation,
  merge after condition, all-parents-skipped cascade, `continue_on_error` failure.
- `validate_graph`: cycle, 0/2 triggers, dangling/incorrect handles, > 50 nodes, bad template.
- Templating: renders context; sandbox blocks `{{ ''.__class__.__mro__ }}`.
- Slack signature: valid passes; bad sig, stale timestamp, wrong secret fail.
- SSRF: `127.0.0.1`, `10.0.0.1`, `169.254.169.254`, `[::1]`, hostname resolving to
  private IP blocked; allowlisted host passes; non-http scheme blocked.
- Ask-user click authorization: wrong Slack user rejected.
- Loop validation: edge crossing container, nested `for_each`, `parent_id` to non-loop node.
- Loop scheduling (pure helper `next_children_to_start(total, statuses, concurrency)`):
  respects concurrency, finishes when all terminal, aggregates succeeded/failed.
- Dry-run node dispatch (pure helper `dry_run_behaviour(node) -> execute | simulate`):
  every node type classified; `execute_in_dry_run` honoured only on `http_request`;
  mock precedence `mocks` → `mock_output` → default.
- Alert group mode: DB-backed test that two concurrent promotes with the same
  `group_key` yield one case (run against the dev Postgres, scoped cleanup by row IDs).

Manual E2E: both motivating examples (case → Slack confirm; alert → group promote →
for_each ask users) against a real Slack workspace via ngrok, each first as a dry run.

## Build phases (one plan, four shippable phases)

1. Engine, triggers/events (incl. `alert.ingested`), validation, templating, SSRF,
   `condition` + `http_request` + case nodes + `case_search` + `alert_promote`/`alert_dismiss`,
   **dry-run**, workflows/runs API, builder + runs UI + dry-run dialog, CaseDetail manual run.
2. `for_each`: child runs, concurrency scheduling, container UI, child-run drill-down.
3. Slack config + Test, interactions endpoint + signature verification,
   `slack_post_message` + case buttons, manifest + docs.
4. `slack_ask_user`, wait/resume, timeout beat task, cancel of waiting runs (incl.
   children), dry-run answers for ask-user, E2E.
