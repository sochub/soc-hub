# Workflows

Workflows run a small graph of steps in response to an event: a case is created or updated, an alert is ingested, or an analyst clicks "Run workflow". Graphs are built on the Workflows page (admins) and executed by the Celery worker.

Ready-made workflows for Okta, Microsoft 365, Entra ID, CrowdStrike, SentinelOne, AWS, Google Workspace, Cloudflare and more are listed in [Integration templates](integrations/README.md) and installable from **Workflows › Templates**.

## Concepts

- **Trigger**: exactly one per workflow: `case.created`, `case.updated` (context includes `changes` for status, severity, tags, assignee), `alert.ingested`, `schedule` (cron), or `manual`.
- **Filter**: optional expression on the trigger, for example `'phishing' in case.tags` or `alert.source == 'EDR' and alert.payload.severity >= 7`. If it is false, no run is created.
- **Nodes**: the steps. Case nodes act on the run's case by default; an optional templated `case_id` overrides it.
- **Edges**: connect a node's output to the next node. A `condition` node has two outgoing handles, `true` and `false`; only the matching edge is followed.
- **Runs**: each trigger firing creates a run holding a snapshot of the graph, so later edits do not affect in-flight runs. Only enabled, valid workflows fire. Invalid graphs can be saved but not enabled or dry-run.

Example: trigger `case.created` -> condition `case.severity == 'critical'` -> (true) `slack_post_message` "Critical case {{ case.title }}".

## Template context

Text fields are Jinja2 templates (sandboxed, strict: an undefined variable is an error). Conditions and filters are expressions.

| Name | Meaning |
|---|---|
| `case` | The run's case, reloaded before each step. `null` until one exists, e.g. before `alert_promote`. Includes `artifacts` (`[{type, value}]`, `type` is the custom key for custom types), `attributes` (first value per type, e.g. `case.attributes.username`) and `attributes_all` (all values per type). See [Custom artifact types](#custom-artifact-types). |
| `alert` | The run's alert (`source`, `external_id`, `title`, `severity`, `payload`, `status`), reloaded fresh. For a run without its own alert (manual, `case.*`), this is the newest alert promoted into the case, or `null`. |
| `trigger` | The event payload: `event`, `case_id`, `alert_id`, `changes`. For `schedule`, it also has `fired_at` and `since` (UTC `YYYY-MM-DDTHH:MM:SSZ`: the fire time of the last successful run, so a failed or throttled pull's window is read again; one hour back on the first run; never more than 24 hours back), so a pull can ask a vendor API for `{{ trigger.since }}` onwards; `since_ms` is the same instant in UNIX epoch milliseconds. For `case.updated`, `changes` has one `{from, to}` entry per changed field, with enum values as plain strings (`trigger.changes.severity.to == 'critical'`, `trigger.changes.status.from == 'new'`). `tags` also has `added` and `removed` lists (`'phishing' in trigger.changes.tags.added`). An owner change appears as both `owner_id` and `assignee` (`{from, to}` user ids). |
| `steps.<node_id>.output` | Output of an already completed step. Loop child runs also see the parent's. |
| `loop.item`, `loop.index` | Inside a `for_each` body only. |
| `dry_run` | Boolean. |

**Template limits.** Templates and trigger filters run on the shared worker, so each render is bounded; going
over a limit fails the template like any other template error (a step fails, a trigger filter counts as no
match). Per render: `range()` up to 10,000 items; any produced string or list up to 1,000,000 characters/items
(also the rendered output); at most 4,000,000 characters emitted in total (macros and `{% set %}` blocks
included); integer results of `*`/`**` up to 4,096 bits; at most 100,000 operations (loop iterations, calls,
filters, `* ** + %`); at most 1 s. Format widths are capped (6 digits), `str.translate` and `lipsum` are not
available, and `tojson` indent is at most 16. Note Jinja's `**` is left-associative: `2 ** 3 ** 2` is 64.

## Custom artifact types

Admins define types in Settings → Artifact types: a key (`username`), a label, an optional alert payload key
(`user_name`; dotted paths like `user.email` work), "show in mindmap", and "private" (hidden everywhere in the UI;
only workflows see it; switching an existing type to private hides its artifacts from then on, but timeline entries written
while it was public stay). "Private" covers the artifacts only: the raw alert payload, shown on the Alerts page and
copied into a case description by "Promote → new case", still contains the value. Promoting an alert copies each mapped payload value onto the case as an artifact of that type
(a list becomes one artifact per item, max 20; objects and empty values are skipped). IPs, emails, URLs, domains and
hashes found anywhere in the payload are also added as built-in artifacts.

Use them in templates:
- `{{ case.attributes.username }}`: first value; the step fails if the case has none.
- `{{ case.attributes.get('username', '') }}`: optional.
- `{{ case.attributes_all.username | join(', ') }}`: every value.
- `case_add_artifact` accepts custom keys in its type field.

## Node reference

**Logic**
- `condition`: `expression`. Outputs the boolean on the `true` / `false` handles.
- `for_each`: container node. `items` (expression evaluating to a list), `concurrency` (default 5, max 20), `max_items` (default 100, hard cap 500; more fails the step). One child run per item; output `{results: [{index, status, steps: {node_id: output}}], succeeded, failed}`.

- `run_workflow` ("Run workflow"): starts another workflow of this organization on the same case (or `case_id`), so
  workflows can call each other. `workflow_name` (exact name; newest wins if two share it) or `workflow_id`; the
  target must be an **enabled manual** workflow. Optional `inputs` (JSON object, values templated) reach the started
  workflow as `trigger.inputs`, next to `trigger.started_by` (`{run_id, workflow_id, workflow_name}` of the caller).
  The started workflow runs on its own: this step only starts it and outputs `{run_id, workflow_id, workflow_name,
  case_id}`. Fails the step when the workflow is missing, disabled, the caller itself, or the chain is deeper than 3
  (see [Loop guard](#loop-guard)). A dry run starts nothing. Example: the *Ask the user on Slack to confirm* template
  starts **Escalate to incident** when the user doesn't confirm.
- `fail`: `message` (templated). Stops the run as failed with that message. Use it on the false branch of a status check after a vendor pull, so a 401/429 shows as a failed run and the next schedule re-reads the window.

**Actions**
- `http_request`: `method`, `url`, `headers`, `body` (templated), optional `retries`, `execute_in_dry_run`, `mock_output`. Output `{status, headers, body}` (body parsed as JSON when possible). 4xx responses don't fail the step: the status is in `output.status`, so branch on it with a condition. Bodies over 1 MB are truncated and `output.truncated` is true.
  - Optional `auth` (templated like headers) for OAuth2 client credentials: `{"type": "oauth2_client_credentials", "token_url": "...", "client_id": "...", "client_secret": "{{ secrets.CLIENT_SECRET }}", "scope": "...", "audience": "..."}` (`scope` and `audience` optional; some providers such as Wiz require `audience`). The OAuth token is bound to the same hosts as the secrets used in `auth`, so those secrets must list both the token host and the API host. Before the request the node POSTs `grant_type=client_credentials` (form-encoded) to `token_url`, with the same SSRF rules as `url`; a secret in the token request must list the token host in its allowed hosts. A non-2xx answer or one without `access_token` fails the step with `token request failed: HTTP <status>` (the body is never shown). The token is sent as `Authorization: Bearer ...`, replacing any Authorization header, and is masked from the step output and errors; it is never stored. Tokens are cached per worker process until 60 s before `expires_in`. In a dry run nothing is requested and `would_do.auth` shows the secret placeholders.
  - `{"type": "basic", "username": "...", "password": "{{ secrets.PASSWORD }}"}` sends `Authorization: Basic base64(username:password)`, built inside the node after secret resolution. `password` must be a secret reference.
  - `{"type": "aws_sigv4", "access_key_id": "{{ secrets.AWS_ACCESS_KEY_ID }}", "secret_access_key": "{{ secrets.AWS_SECRET_ACCESS_KEY }}", "session_token": "{{ secrets.AWS_SESSION_TOKEN }}", "region": "us-east-1", "service": "guardduty"}` signs the request with AWS Signature Version 4 (`session_token` optional). A JSON body is serialised once and the exact bytes sent are signed, with the URL host as `Host` (also when the connection is pinned to a vetted IP). `secret_access_key` must be a secret reference; `region` and `service` must match `[a-z0-9-]{2,32}`. Static keys from tenant secrets only: no IAM role, instance profile or assume-role.
  - `{"type": "oauth2_private_key_jwt", "token_url": "https://acme.okta.com/oauth2/v1/token", "client_id": "...", "private_key": "{{ secrets.OKTA_PRIVATE_KEY }}", "scope": "okta.users.manage", "key_id": "...", "dpop": true}` (private-key JWT client authentication, RFC 7523). `token_url`, `client_id`, `private_key` and `scope` are required; `key_id` is optional (the `kid` header of the client assertion; a JWK carries its own `kid`, a PEM does not; an explicit `key_id` overrides the JWK's `kid`) and `private_key` must be a secret reference holding a PEM or JWK private key. The node signs the client assertion, exchanges it at `token_url` (SSRF guard applies) and sends `Authorization: Bearer`. `dpop` must be the boolean `true` or `false` (a string fails validation); it defaults to off. With `dpop: true` the node generates an in-memory EC P-256 key, sends a DPoP proof with the token request and a fresh proof (bound to the method, URL and access token) with every API request, and uses `Authorization: DPoP`. A `use_dpop_nonce` challenge from the token endpoint or the API is retried exactly once with the supplied nonce. The token and its DPoP key are cached together (a token without its key is useless); a token response that is not `token_type: DPoP` fails, and an API 401 with `invalid_token` evicts the cached token so the next call mints a fresh one. Host binding as with OAuth2: the token is bound to the secret's hosts, so `private_key`'s secret must list the token host and the API host. The key, assertion, proofs and token never reach output, errors or logs.
  - `{"type": "google_service_account", "service_account_key": "{{ secrets.GWS_SA_KEY }}", "scopes": "https://www.googleapis.com/auth/apps.alerts", "subject": "admin@example.com"}` (`scopes` space-separated; `subject` optional, the user to impersonate with domain-wide delegation). The secret holds the full service-account JSON key and must be a secret reference. Inside the node the key signs an RS256 JWT (`iss` = `client_email`, `aud` = the token URL, 1 h lifetime, `kid` = `private_key_id`), exchanged at the fixed URL `https://oauth2.googleapis.com/token` (the key's `token_uri` is ignored). As with OAuth2, the token is bound to the secret's hosts, so `GWS_SA_KEY` must list `oauth2.googleapis.com` and the API host; tokens are cached per subject, scopes and key. A malformed key fails with a generic error; the key, JWT and token never reach output, errors or logs.
  - For basic and aws_sigv4, the credentials go to the request host itself, so every secret used must allow that host. Any Authorization header is replaced. The password, keys, session token, the Basic header value and the SigV4 signature are masked from the step output and errors and never logged. Unknown `auth.type` or missing required keys fail validation.
- `slack_post_message`: see [Slack nodes](#slack-nodes).
- `slack_ask_user`: DM a user by email with buttons and wait for the answer; see [Slack nodes](#slack-nodes).
- `case_update`: severity, status, assignee, add/remove tags.
- `case_add_note`: `content`. `case_add_artifact`: artifact type and value.
- `case_apply_playbook`: playbook template.
- `case_search`: read-only. Filters: status in, tags contain, title contains, artifact value equals, created within N hours. Output `{cases: [...], first, count}` (max 50).
- `alert_promote`: `mode` `new` | `link` | `group`, see below. Output `{case_id, created}`. Sets the run's case, so later nodes act on the promoted case.
- `alert_dismiss`: optional templated `reason`. Sets `alert.status = dismissed` and stores the reason.
- `alert_create`: creates a `pending` alert from data a workflow pulled (e.g. a vendor API on a schedule). `source` (1-100 chars), `external_id` (1-255), `title` (cut to 500), `severity` (`critical`/`high`/`medium`/`low`/`info`; empty or anything else becomes `medium`, and an unknown value adds `severity_defaulted: true` to the output), `payload` (expression, no `{{ }}`, that evaluates to an object or list of at most 256 KB, e.g. `loop.item`). Idempotent on (tenant, `source`, `external_id`) like the webhook: an event seen twice (overlapping schedule windows) returns the existing alert with `created: false` and does not fire `alert.ingested` again. Output `{alert_id, created}`. A new alert fires `alert.ingested` (counts toward the loop guard). Allowed in any workflow.
- `case_task_complete` ("Complete task"): records a result on a case task and ticks it. It applies to the task the
  run was started from (**Run** on a task linked to the workflow); otherwise to the task named `task_title` on the
  case (case-insensitive, open tasks first); otherwise it does nothing, so any workflow can end with it. `result`
  (templated text shown on the task, e.g. `Confirmed by {{ steps.ask.output.email }} on Slack`), `result_status` (`ok`,
  `warning` or `failed`, templatable; it colours the result), `complete` (default true; `false` records the result and
  leaves the task open). Adds an activity entry; ticking writes the usual "Task completed" entry and audit row. Output
  `{task_id, completed, result_status}`. Every built-in response template ends with it: a successful action ticks the
  task, a skipped or failed one records why and leaves it open. The template *Ask the user on Slack to confirm (for a
  case task)* records who confirmed: *Confirmed by alice@acme.com on Slack* (ok), *Not confirmed: alice@acme.com
  answered "It wasn't me" on Slack* (failed), or *No answer from alice@acme.com on Slack within the time limit*
  (warning, task left open), then runs **Escalate to incident** unless the user confirmed.
- `email_ask_user`: like `slack_ask_user`, by email, for people who aren't on Slack. `email`, `message` (plain
  text, shown escaped), optional `buttons` (comma-separated, ≤ 5, each ≤ 75 characters; default *It was me* /
  *It wasn't me*) and `timeout_hours` (≤ 168, default 1). Needs SMTP and `FRONTEND_URL`. The email has one
  **Answer** link to `/verify/<token>`; opening it records nothing (mail scanners open links), the person picks an
  answer there, once. The run waits; the output is `{response, responder_email, timed_out}` like the Slack node,
  and the same expiry sweep times it out. The 256-bit token is the only credential, valid only while the question is
  open; answering is audited. Dry runs simulate the answer like `slack_ask_user`.
- `containment_guard`: put it right before any step that isolates a host, suspends a user or blocks an IP. `action`
  (label), `target_kind` (`host`, `user` or `ip`), templated `target`, optional `release_hint` (how to undo it at the
  vendor). It checks the tenant's **containment guardrails** (Settings → Integrations): targets on the protected list
  (case-insensitive globs for hosts and users, address or CIDR for IPs, `any` for every kind) are refused, as are
  actions past the tenant's 24-hour cap or a new target past the per-case cap (re-containing a target already on the
  case is fine). Allowed: the action is recorded on the case (**Artifacts & assets → Containment**, plus an activity
  entry) and the run goes on with `{allowed: true, action_id, target}`. Refused: the refusal is recorded and noted
  on the case, and the step fails with the reason, which stops the run. In a dry run it checks the policy and records
  nothing. Guards of one tenant are serialised, so caps hold under concurrent runs. Every built-in response template
  has one.

Every non-trigger step must have an incoming edge; a disconnected step means the workflow cannot be enabled.

`alert_promote` and `alert_dismiss` are only allowed in `alert.ingested` workflows. Promoting or dismissing an alert that is no longer `pending` fails the step.

## Join and skip semantics

A node runs when all its incoming edges are resolved and at least one is active; it is skipped when all are resolved and none is active, and skips propagate. A node that fails fails the run (pending steps are cancelled) unless it has `continue_on_error`, in which case it succeeds with `output.error` set.

## Retries and timeouts

`http_request` bounds the whole request (connect plus read) to 15 s. `retries` (default 2, backoff 10 s then 60 s) applies only to network errors, timeouts and 5xx responses; 4xx responses are never retried and never raised. Case action nodes do not retry. A step that runs longer than the worker's task time limit (soft 120 s) fails with `step timed out` and the run finalizes as failed. `slack_ask_user` waits up to `timeout_hours`; on expiry the step succeeds with `{response: null, timed_out: true}`. Analysts can cancel a running or waiting run.

## Dry run

Admins can dry-run a workflow (even a disabled one) against a case, an alert, or a sample payload from the Workflows page. The saved graph is used and the run is stored, marked as a dry run.

- Executes for real (read-only): trigger, `condition`, `for_each`, `case_search`, templating.
- Simulated: case and alert mutation nodes, `slack_post_message`, and `http_request` unless `execute_in_dry_run: true` (meant for GET lookups; SSRF rules still apply). Each simulated step stores its fully rendered input in `output.would_do`.
- Simulated output is the run's mock for that node (`mocks: {node_id: output}`), then the node's `mock_output`, then a stub (`alert_promote` gives `{case_id: null, created: true, simulated: true}`).
- `slack_ask_user` never waits; it resolves to the `ask_user_answers` choice (default: first button) or a timeout.
- No case, alert, Slack, or HTTP side effects and no events.

## Loop guard

Cases created by an automation emit `case.created` at depth + 1, and updates likewise. Events beyond depth 3 are dropped (with a warning in the worker log), which stops A -> B -> A cycles. A `run_workflow` step starts its workflow at depth + 1 too and fails past depth 3, and a workflow can't start itself.

## HTTP allowlist and SSRF

`http_request` allows only `http`/`https`. The host is resolved and rejected if any address is loopback, private (RFC1918, ULA), link-local (including `169.254.169.254`), shared/CGNAT (`100.64.0.0/10`), reserved, unspecified, or multicast (allowlisted private hosts excepted, see below). The request then connects to the vetted address itself, so a second DNS answer cannot rebind it to an internal host; the hostname is still sent as `Host` and used for TLS SNI and certificate verification. Redirects are not followed and the response body is capped at 1 MB.

The tenant **HTTP allowlist** (Integrations, tenant admin) lists hostnames that may resolve to a private address (RFC 1918, ULA `fc00::/7`, CGNAT `100.64.0.0/10`), e.g. an on-prem SOAR. It applies to the HTTP node, OAuth `token_url`s and the tenant AI `api_base`:

- Entries are DNS hostnames with at least two labels (`soar.corp.example.com`) or wildcards (`*.corp.example.com`, subdomains only). IP addresses, single-label names (`redis`, `db`, `localhost`), schemes, ports and paths are rejected with `422`; at most 100 entries.
- Allowlisted hosts are still resolved, checked and pinned. Loopback, link-local (including `169.254.169.254` and `fd00:ec2::254`), unspecified, multicast and reserved addresses, and their IPv4-mapped/compatible forms, are refused for every host.
- Private addresses are reachable through the allowlist **only if the platform operator sets `WORKFLOW_ALLOW_PRIVATE_ALLOWLIST=true`** (env, default `false`). With it off, the allowlist has no effect on address checks. **Enabling it lets every tenant admin reach private addresses, including the platform's own internal network** (any multi-label name that resolves there, e.g. via wildcard DNS or container/cluster network names such as `db.svc.cluster.local`). Enable it only on single-tenant or on-prem deployments.
- A request that carries a secret must use `https://`, allowlisted or not ("secrets are only sent over https").

## Worked example: group EDR alerts into one case

Goal: every EDR alert with severity 7 or higher is promoted into one case per host (six-hour window); low-severity alerts are dismissed.

Workflow 1, trigger `alert.ingested`, filter `alert.payload.severity is defined and alert.payload.severity >= 7`:

1. `promote` (`alert_promote`): mode `group`, title `{{ alert.title }}`, severity `high`, tags `edr`, group_key `host:{{ alert.payload.host }}`, window_hours `6`.
2. `note` (`case_add_note`): content `Alert {{ alert.external_id }} grouped ({{ 'new case' if steps.promote.output.created else 'existing case' }})`.
3. Edges: start -> promote -> note.

Workflow 2, same trigger, filter `alert.payload.severity is defined and alert.payload.severity < 7`: `alert_dismiss` with reason `auto: low severity`.

Grouping takes a per-(tenant, group_key) advisory lock, finds the newest non-resolved, non-closed case with that key inside the window, and links to it, otherwise creates one. So a burst of concurrent alerts never produces duplicate cases.

Dry-running workflow 1 on a pending alert shows `promote` as simulated with `would_do.group_key` = `host:web-9`.

Send alerts to `POST /api/v1/alerts/webhook` with `X-API-Key`:

```bash
for i in 1 2 3; do curl -s -X POST localhost:8000/api/v1/alerts/webhook -H "X-API-Key: <key>" \
  -H 'Content-Type: application/json' \
  -d "{\"external_id\":\"edr-$i\",\"title\":\"Beacon on web-1\",\"payload\":{\"host\":\"web-1\",\"severity\":9}}" & done; wait
```

Result: all three alerts are promoted into one case; one note says "new case" and two say "existing case". An alert with severity 2 is dismissed with the reason, and an alert without a `severity` matches neither filter and stays pending.

## Slack nodes

Both Slack nodes need a configured Slack integration for the tenant. Set it up first: [docs/slack/setup.md](../slack/setup.md).

In the mrkdwn fields (`slack_post_message.text`, `slack_ask_user.message`) every `{{ value }}` is escaped for Slack (`&`, `<`, `>` become `&amp;`, `&lt;`, `&gt;`), so alert or case data can't inject links or `<!channel>` mentions. Markup you type yourself, such as `<https://example.com|runbook>` or `<!here>`, is kept.

### `slack_post_message`

Config: `channel` (blank uses the integration's default channel), `text` (templated, mrkdwn), `include_case_buttons` (`yes`/`no`; adds Acknowledge / Assign to me / Assign… / Add note / War room / Close buttons for the run's case). Output `{channel, ts}`. The bot must be in the channel (`/invite @sochub`).

### `slack_ask_user`

Sends a DM to the workspace member with the given email and pauses the run until they click a button or the timeout expires.

Config:
- `email`: templated, for example `{{ loop.item }}`. An empty result fails the step.
- `message`: templated question. An empty result fails the step.
- `buttons`: comma-separated labels, for example `Yes, No` (default `Yes, No`). At most 5 buttons; more fails the step.
- `timeout_hours`: default 24, must be greater than 0 and at most 168. Fractions are fine (`0.02` is about 72 seconds, handy for testing).

Output: `{response, responder_slack_id, responder_email, email, timed_out}` (`email` is the address that was asked, also set when it times out and in dry runs). While waiting, the step output holds `{target_slack_id, channel, message_ts, buttons, email}` and the run status is `waiting`.

Waiting, timeout and cancel behaviour:
- **Waiting**: the run stays `waiting` and holds no worker. The first valid click resumes the run; the DM is edited to show the answer and later clicks are ignored.
- **Timeout**: a beat sweep completes the step with `{response: null, timed_out: true}` (the run continues, it does not fail) and edits the DM to say the request expired. Branch on it with `steps.ask.output.timed_out`.
- **Cancel**: cancelling a run (or a parent run whose loop children are waiting) edits every open question to "This request was withdrawn." after the cancel commits. This is best effort and never blocks the cancel.
- **Security**: in Socket Mode (the default) clicks arrive over the tenant's own `slack-socket` connection, which only Slack can open with that tenant's app-level token; payloads from another workspace are dropped. In HTTP mode, requests are verified with the app's signing secret and rejected with 401 when the signature is wrong, the team is unknown, or the timestamp is more than 5 minutes old. Either way, buttons only resolve steps of the tenant that owns the Slack team.

### Dry run

In a dry run `slack_ask_user` and `slack_post_message` never contact Slack. The ask step resolves to `ask_user_answers[node_id]` from the dry-run request: a button label, or the literal `timeout` to simulate expiry. With no entry it answers with the first button. Simulated ask output has `responder_email: null` and `simulated: true`, so a template such as `{{ steps.ask.output.responder_email }}` renders `None` in a dry run only.

## Worked example 1: case -> webhook -> Slack confirmation -> branch

Trigger `case.created`, filter `'x' in case.tags`.

1. `hook` (`http_request`): POST `https://webhook.site/<your-id>`, body `{"case": "{{ case.id }}", "title": "{{ case.title }}"}`.
2. `ask` (`slack_ask_user`): email `<user email>`, message `Did you report "{{ case.title }}"?`, buttons `Yes, No`, `timeout_hours` `1`.
3. `check` (`condition`): `steps.ask.output.response == 'No' or steps.ask.output.timed_out`.
4. `check` true: `escalate` (`case_update`, severity `critical`) -> `esc_note` (`case_add_note`): `Escalated: user answered {{ steps.ask.output.response or 'nothing (timeout)' }}`.
5. `check` false: `ok_note` (`case_add_note`): `Confirmed by {{ steps.ask.output.responder_email }}` -> `close` (`case_update`, status `closed`).

Dry-run it against a case tagged `x` with `ask_user_answers: {"ask": "No"}`: `escalate` and `esc_note` are simulated, `ok_note` and `close` are skipped. With `Yes` the reverse. With `timeout` the escalate branch runs (`esc_note` renders "nothing (timeout)"). Set `execute_in_dry_run` on `hook` to make the HTTP call for real while everything else stays simulated.

Verified end to end through the real engine as dry runs (HTTP step returned status 200 against httpbin.org, the case was unchanged in all three runs). Live, enabled behaviour is on the [human acceptance checklist](#human-acceptance-checklist).

## Worked example 2: alert -> group promote -> ask each user

Trigger `alert.ingested`, filter `alert.payload.severity is defined and alert.payload.severity >= 7`.

1. `promote` (`alert_promote`): mode `group`, group_key `host:{{ alert.payload.host }}`, window_hours `6`.
2. `users` (`for_each`): items `alert.payload.users`, concurrency `5`. Body: `ask` (`slack_ask_user`), email `{{ loop.item }}`, message `Unusual activity on {{ alert.payload.host }} - was this you?`.
3. After the loop, `check` (`condition`): `steps.users.output.results | selectattr('steps.ask.response', 'equalto', 'No') | list | length > 0`.
4. `check` true: `crit` (`case_update`, severity `critical`).

Each loop item runs as its own child run, so a two-user alert produces two DMs and two child runs. Dry-run it on a pending alert with `"users": ["a@x.com", "b@x.com"]` and `ask_user_answers: {"ask": "No"}`: 2 child runs each with a simulated ask, `check` evaluates true, `crit` is simulated, the alert stays `pending` and no case is created.

If the `selectattr` expression is awkward for your data, put a `condition` plus a `case_update` inside the loop body instead: it fires per user as soon as that user answers `No`.

## Human acceptance checklist

The engine paths above are covered by automated tests and dry runs. These live steps still need a person, a real Slack workspace and a browser:

- [ ] Install the Slack app from the manifest, save the bot token and the app-level token (Socket Mode), click **Test** once (stores `team_id`; buttons are ignored until then), and check the card says *socket connected*. HTTP mode instead: save the signing secret and expose the backend (for example `ngrok http 80`) as the interactivity URL, see [Slack setup](../slack/setup.md).
- [ ] Example 1, enabled: create a case tagged `x`; webhook.site receives the POST; the DM arrives. Answer **No**: case becomes critical with the note. Repeat with **Yes**: note plus closed. Repeat unanswered with `timeout_hours` `0.02`: escalated with "nothing (timeout)".
- [ ] Example 2, enabled: ingest the alert with two real Slack users: one case, two DMs; one **No** makes the case critical.
- [ ] Cancel a waiting run from the UI: the DM changes to "This request was withdrawn."
- [ ] UI click-throughs: build both graphs in the Workflows editor, dry-run with answers, and check skipped (gray) and simulated markers on the run page.
- [ ] Viewer role: sees Workflows and runs but cannot save, enable, dry-run, run or cancel (API returns 403, covered by `tests/test_wf_api.py`).
