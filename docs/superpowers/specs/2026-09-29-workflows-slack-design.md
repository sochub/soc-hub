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

## Decisions (from brainstorming)

| Topic | Decision |
|---|---|
| "End user" | Affected employee, **not** a platform user; found in Slack by email |
| Slack connection | **Bring-your-own app** per tenant: admin pastes bot token + signing secret |
| Workflow shape | **Full DAG** with visual canvas (React Flow / `@xyflow/react`) |
| Execution engine | **Celery + run state in Postgres** (no Temporal) |
| SSRF | Block private/loopback/link-local IPs by default; per-tenant host allowlist |
| "Wait for all" node | Dropped — default join semantics already wait for all parents |

## Scope

### In v1

**Triggers** (exactly one per workflow)
- `case.created`
- `case.updated` (with `changes` for status / severity / tags / assignee)
- `alert.ingested`
- `manual` ("Run workflow" on a case)

Each workflow also has an optional `trigger_filter` expression (e.g. `'phishing' in case.tags`).

**Logic node**
- `condition` — evaluates an expression; `true` / `false` output handles.

**Action nodes**
- `http_request` — method, URL, headers, body (templated). Output `{status, headers, body}` (body parsed as JSON when possible).
- `slack_post_message` — to a channel (default channel if blank); optional case action buttons (Acknowledge / Assign to me / Close).
- `slack_ask_user` — DM a user by email with custom buttons; waits. Output `{response, responder_slack_id, responder_email, timed_out}`.
- `case_update` — severity, status, assignee, add/remove tags.
- `case_add_note`, `case_add_artifact`.
- `case_apply_playbook`.

### Out of v1 (each addable later as a node/feature without redesign)
Loops/for-each, sub-workflows, cron triggers, code nodes, email node, dry-run mode,
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
- Node shape: `{id: slug, type, position: {x, y}, config: {...}, continue_on_error: bool, retries?: int}`.
- Edge shape: `{id, source, target, source_handle: null | "true" | "false"}`.

**`workflow_runs`**
- `id`, `tenant_id`, `workflow_id`, `workflow_version`, `case_id` (nullable),
  `status` (`running` | `waiting` | `succeeded` | `failed` | `cancelled`),
  `trigger_payload` (JSON), `graph_snapshot` (JSON), `depth` (int), `error`,
  `started_at`, `finished_at`.
- `graph_snapshot` means editing a workflow never affects in-flight runs.

**`workflow_run_steps`**
- `id`, `run_id`, `node_id`, `status` (`pending` | `running` | `succeeded` | `failed` |
  `skipped` | `waiting` | `cancelled`), `input` (rendered config, JSON), `output` (JSON),
  `error`, `attempt`, `wait_token` (unique, nullable, `secrets.token_urlsafe(24)`),
  `wait_expires_at`, `started_at`, `finished_at`.
- Unique `(run_id, node_id)`.

**`tenants`** gains `workflow_http_allowlist` (JSON list of hostnames, default `[]`).

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

### Template context

- `case` — case reloaded fresh before each step (reflects earlier updates); includes tags, artifacts summary.
- `trigger` — event payload (`trigger.changes.tags.added`, etc.).
- `steps.<node_id>.output` — outputs of completed steps.

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

Acyclic; exactly one trigger node, matching `trigger_type`; ≤ 50 nodes; unique node
ids (slug `[a-z0-9_]+`); every `condition` has its outgoing edges on `true`/`false`
handles and non-condition sources have no handle; all templates/expressions parse;
required config per node type present. Invalid graphs may be **saved** but not
**enabled** (enable endpoint re-validates).

## Security

- **SSRF** (`http_request`): only `http`/`https`; resolve host, reject if any resolved
  IP is loopback / private (RFC1918, ULA) / link-local (incl. `169.254.169.254`) /
  unspecified / multicast, unless hostname is in the tenant allowlist. Redirects not
  followed. Response body capped at 1 MB.
- **Template sandbox**: Jinja2 `SandboxedEnvironment`; no access to dunder attrs.
- **Slack interactions**: see below; nothing is acted on before signature verification.
- **Secrets**: Slack token/secret encrypted at rest, never returned by the API (masked).
- **Permissions**: admins create/edit/enable/delete workflows and configure Slack and
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
- `GET /workflow-runs?workflow_id=&case_id=&status=`, `GET /workflow-runs/{id}` (with steps),
  `POST /workflow-runs/{id}/cancel`
- `GET/PUT /slack/config`, `POST /slack/config/test`, `POST /slack/interactions`
- `GET/PUT /workflows/settings/http-allowlist`

## Frontend

In `frontend/src/features/automations/`, light "Telemetry Console" theme; sidebar item
**Automations**.

- **List**: name, trigger, enabled toggle, last run status/time, run count.
- **Editor**: React Flow canvas; left palette of node types; right inspector with the
  node's config form (templated fields in Roboto Mono with a context-path hint); top bar
  Save / Validate (errors pinned to nodes) / Enable toggle.
- **Runs + run detail**: read-only canvas of `graph_snapshot`, nodes colored by step
  status (green succeeded, red failed, gray skipped, amber waiting); click → input,
  output JSON, error. Cancel button for running/waiting.
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

Manual E2E: the motivating example against a real Slack workspace via ngrok.

## Build phases (one plan, three shippable phases)

1. Engine, triggers/events, validation, templating, SSRF, `condition` + `http_request` +
   case nodes, workflows/runs API, builder + runs UI, CaseDetail manual run.
2. Slack config + Test, interactions endpoint + signature verification,
   `slack_post_message` + case buttons, manifest + docs.
3. `slack_ask_user`, wait/resume, timeout beat task, cancel of waiting runs, E2E.
