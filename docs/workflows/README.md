# Automations (Workflows)

Automations run a small graph of steps in response to an event: a case is created or updated, an alert is ingested, or an analyst clicks "Run workflow". Graphs are built in the Automations page (admins) and executed by the Celery worker.

## Concepts

- **Trigger**: exactly one per workflow: `case.created`, `case.updated` (context includes `changes` for status, severity, tags, assignee), `alert.ingested`, or `manual`.
- **Filter**: optional expression on the trigger, for example `'phishing' in case.tags` or `alert.source == 'EDR' and alert.payload.severity >= 7`. If it is false, no run is created.
- **Nodes**: the steps. Case nodes act on the run's case by default; an optional templated `case_id` overrides it.
- **Edges**: connect a node's output to the next node. A `condition` node has two outgoing handles, `true` and `false`; only the matching edge is followed.
- **Runs**: each trigger firing creates a run holding a snapshot of the graph, so later edits do not affect in-flight runs. Only enabled, valid workflows fire. Invalid graphs can be saved but not enabled or dry-run.

Example: trigger `case.created` -> condition `case.severity == 'critical'` -> (true) `slack_post_message` "Critical case {{ case.title }}".

## Template context

Text fields are Jinja2 templates (sandboxed, strict: an undefined variable is an error). Conditions and filters are expressions.

| Name | Meaning |
|---|---|
| `case` | The run's case, reloaded before each step (tags, artifacts summary). `null` until one exists, e.g. before `alert_promote`. |
| `alert` | The run's alert (`source`, `external_id`, `title`, `payload`, `status`), reloaded fresh. |
| `trigger` | The event payload: `event`, `case_id`, `alert_id`, `changes`. For `case.updated`, `changes` has one `{from, to}` entry per changed field, with enum values as plain strings (`trigger.changes.severity.to == 'critical'`, `trigger.changes.status.from == 'new'`). `tags` also has `added` and `removed` lists (`'phishing' in trigger.changes.tags.added`). An owner change appears as both `owner_id` and `assignee` (`{from, to}` user ids). |
| `steps.<node_id>.output` | Output of an already completed step. Loop child runs also see the parent's. |
| `loop.item`, `loop.index` | Inside a `for_each` body only. |
| `dry_run` | Boolean. |

## Node reference

**Logic**
- `condition`: `expression`. Outputs the boolean on the `true` / `false` handles.
- `for_each`: container node. `items` (expression evaluating to a list), `concurrency` (default 5, max 20), `max_items` (default 100, hard cap 500; more fails the step). One child run per item; output `{results: [{index, status, steps: {node_id: output}}], succeeded, failed}`.

**Actions**
- `http_request`: `method`, `url`, `headers`, `body` (templated), optional `retries`, `execute_in_dry_run`, `mock_output`. Output `{status, headers, body}` (body parsed as JSON when possible). 4xx responses don't fail the step: the status is in `output.status`, so branch on it with a condition. Bodies over 1 MB are truncated and `output.truncated` is true.
- `slack_post_message`: see [Slack nodes](#slack-nodes).
- `slack_ask_user`: DM a user by email with buttons and wait for the answer; see [Slack nodes](#slack-nodes).
- `case_update`: severity, status, assignee, add/remove tags.
- `case_add_note`: `content`. `case_add_artifact`: artifact type and value.
- `case_apply_playbook`: playbook template.
- `case_search`: read-only. Filters: status in, tags contain, title contains, artifact value equals, created within N hours. Output `{cases: [...], first, count}` (max 50).
- `alert_promote`: `mode` `new` | `link` | `group`, see below. Output `{case_id, created}`. Sets the run's case, so later nodes act on the promoted case.
- `alert_dismiss`: optional templated `reason`. Sets `alert.status = dismissed` and stores the reason.

Every non-trigger step must have an incoming edge; a disconnected step means the workflow cannot be enabled.

`alert_*` nodes are only allowed in `alert.ingested` workflows. Promoting or dismissing an alert that is no longer `pending` fails the step.

## Join and skip semantics

A node runs when all its incoming edges are resolved and at least one is active; it is skipped when all are resolved and none is active, and skips propagate. A node that fails fails the run (pending steps are cancelled) unless it has `continue_on_error`, in which case it succeeds with `output.error` set.

## Retries and timeouts

`http_request` bounds the whole request (connect plus read) to 15 s. `retries` (default 2, backoff 10 s then 60 s) applies only to network errors, timeouts and 5xx responses; 4xx responses are never retried and never raised. Case action nodes do not retry. `slack_ask_user` waits up to `timeout_hours`; on expiry the step succeeds with `{response: null, timed_out: true}`. Analysts can cancel a running or waiting run.

## Dry run

Admins can dry-run a workflow (even a disabled one) against a case, an alert, or a sample payload from the Automations page. The saved graph is used and the run is stored, marked as a dry run.

- Executes for real (read-only): trigger, `condition`, `for_each`, `case_search`, templating.
- Simulated: case and alert mutation nodes, `slack_post_message`, and `http_request` unless `execute_in_dry_run: true` (meant for GET lookups; SSRF rules still apply). Each simulated step stores its fully rendered input in `output.would_do`.
- Simulated output is the run's mock for that node (`mocks: {node_id: output}`), then the node's `mock_output`, then a stub (`alert_promote` gives `{case_id: null, created: true, simulated: true}`).
- `slack_ask_user` never waits; it resolves to the `ask_user_answers` choice (default: first button) or a timeout.
- No case, alert, Slack, or HTTP side effects and no events.

## Loop guard

Cases created by an automation emit `case.created` at depth + 1, and updates likewise. Events beyond depth 3 are dropped (with a warning in the worker log), which stops A -> B -> A cycles.

## HTTP allowlist and SSRF

`http_request` allows only `http`/`https`. The host is resolved and rejected if any address is loopback, private (RFC1918, ULA), link-local (including `169.254.169.254`), shared/CGNAT (`100.64.0.0/10`), reserved, unspecified, or multicast, unless the hostname is on the tenant's HTTP allowlist (Automations settings, admin only). The request then connects to the vetted address itself, so a second DNS answer cannot rebind it to an internal host; the hostname is still sent as `Host` and used for TLS SNI and certificate verification. Allowlisted hosts are trusted by name and resolved normally. Redirects are not followed and the response body is capped at 1 MB.

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

Config: `channel` (blank uses the integration's default channel), `text` (templated, mrkdwn), `include_case_buttons` (`yes`/`no`; adds Acknowledge / Assign to me / Close buttons for the run's case). Output `{channel, ts}`. The bot must be in the channel (`/invite @SOC Hub`).

### `slack_ask_user`

Sends a DM to the workspace member with the given email and pauses the run until they click a button or the timeout expires.

Config:
- `email`: templated, for example `{{ loop.item }}`. An empty result fails the step.
- `message`: templated question. An empty result fails the step.
- `buttons`: comma-separated labels, for example `Yes, No` (default `Yes, No`). At most 5 buttons; more fails the step.
- `timeout_hours`: default 24, must be greater than 0 and at most 168. Fractions are fine (`0.02` is about 72 seconds, handy for testing).

Output: `{response, responder_slack_id, responder_email, timed_out}`. While waiting, the step output holds `{target_slack_id, channel, message_ts, buttons}` and the run status is `waiting`.

Waiting, timeout and cancel behaviour:
- **Waiting**: the run stays `waiting` and holds no worker. The first valid click resumes the run; the DM is edited to show the answer and later clicks are ignored.
- **Timeout**: a beat sweep completes the step with `{response: null, timed_out: true}` (the run continues, it does not fail) and edits the DM to say the request expired. Branch on it with `steps.ask.output.timed_out`.
- **Cancel**: cancelling a run (or a parent run whose loop children are waiting) edits every open question to "This request was withdrawn." after the cancel commits. This is best effort and never blocks the cancel.
- **Security**: interaction requests are verified with the app's signing secret and rejected with 401 when the signature is wrong, the team is unknown, or the timestamp is more than 5 minutes old. Buttons only resolve steps of the tenant that owns the Slack team.

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

- [ ] Install the Slack app from the manifest, save the token and signing secret, click **Test** once (stores `team_id`; buttons return 401 until then).
- [ ] Expose the backend with `ngrok http 80` and set the interactivity URL to `https://<id>.ngrok.app/api/v1/slack/interactions`.
- [ ] Example 1, enabled: create a case tagged `x`; webhook.site receives the POST; the DM arrives. Answer **No**: case becomes critical with the note. Repeat with **Yes**: note plus closed. Repeat unanswered with `timeout_hours` `0.02`: escalated with "nothing (timeout)".
- [ ] Example 2, enabled: ingest the alert with two real Slack users: one case, two DMs; one **No** makes the case critical.
- [ ] Cancel a waiting run from the UI: the DM changes to "This request was withdrawn."
- [ ] UI click-throughs: build both graphs in the Automations editor, dry-run with answers, and check skipped (gray) and simulated markers on the run page.
- [ ] Viewer role: sees Automations and runs but cannot save, enable, dry-run, run or cancel (API returns 403, covered by `tests/test_wf_api.py`).
