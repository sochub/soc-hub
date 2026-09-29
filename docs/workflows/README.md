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
| `trigger` | The event payload, e.g. `trigger.changes.tags.added`. |
| `steps.<node_id>.output` | Output of an already completed step. Loop child runs also see the parent's. |
| `loop.item`, `loop.index` | Inside a `for_each` body only. |
| `dry_run` | Boolean. |

## Node reference

**Logic**
- `condition`: `expression`. Outputs the boolean on the `true` / `false` handles.
- `for_each`: container node. `items` (expression evaluating to a list), `concurrency` (default 5, max 20), `max_items` (default 100, hard cap 500; more fails the step). One child run per item; output `{results: [{index, status, steps: {node_id: output}}], succeeded, failed}`.

**Actions**
- `http_request`: `method`, `url`, `headers`, `body` (templated), optional `retries`, `execute_in_dry_run`, `mock_output`. Output `{status, headers, body}` (body parsed as JSON when possible).
- `slack_post_message`: channel (default channel if blank), text, optional case buttons (Acknowledge / Assign to me / Close).
- `slack_ask_user`: DM a user by email with custom buttons; waits. `timeout_hours` default 24, max 168. Output `{response, responder_slack_id, responder_email, timed_out}`.
- `case_update`: severity, status, assignee, add/remove tags.
- `case_add_note`: `content`. `case_add_artifact`: artifact type and value.
- `case_apply_playbook`: playbook template.
- `case_search`: read-only. Filters: status in, tags contain, title contains, artifact value equals, created within N hours. Output `{cases: [...], first, count}` (max 50).
- `alert_promote`: `mode` `new` | `link` | `group`, see below. Output `{case_id, created}`. Sets the run's case, so later nodes act on the promoted case.
- `alert_dismiss`: optional templated `reason`. Sets `alert.status = dismissed` and stores the reason.

`alert_*` nodes are only allowed in `alert.ingested` workflows. Promoting or dismissing an alert that is no longer `pending` fails the step.

## Join and skip semantics

A node runs when all its incoming edges are resolved and at least one is active; it is skipped when all are resolved and none is active, and skips propagate. A node that fails fails the run (pending steps are cancelled) unless it has `continue_on_error`, in which case it succeeds with `output.error` set.

## Retries and timeouts

`http_request` has a 15 s timeout and `retries` (default 2, backoff 10 s then 60 s), only on network errors and 5xx. Case action nodes do not retry. `slack_ask_user` waits up to `timeout_hours`; on expiry the step succeeds with `{response: null, timed_out: true}`. Analysts can cancel a running or waiting run.

## Dry run

Admins can dry-run a workflow (even a disabled one) against a case, an alert, or a sample payload from the Automations page. The saved graph is used and the run is stored, marked as a dry run.

- Executes for real (read-only): trigger, `condition`, `for_each`, `case_search`, templating.
- Simulated: case and alert mutation nodes, `slack_post_message`, and `http_request` unless `execute_in_dry_run: true` (meant for GET lookups; SSRF rules still apply). Each simulated step stores its fully rendered input in `output.would_do`.
- Simulated output is the run's mock for that node (`mocks: {node_id: output}`), then the node's `mock_output`, then a stub (`alert_promote` gives `{case_id: null, created: true, simulated: true}`).
- `slack_ask_user` never waits; it resolves to the `ask_user_answers` choice (default: first button) or a timeout.
- No case, alert, Slack, or HTTP side effects and no events.

## Loop guard

Cases created by an automation emit `case.created` at depth + 1, and updates likewise. Events at depth 3 or more do not trigger workflows, which stops A -> B -> A cycles.

## HTTP allowlist and SSRF

`http_request` allows only `http`/`https`. The host is resolved and rejected if any address is loopback, private (RFC1918, ULA), link-local (including `169.254.169.254`), unspecified, or multicast, unless the hostname is on the tenant's HTTP allowlist (Automations settings, admin only). Redirects are not followed and the response body is capped at 1 MB.

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
