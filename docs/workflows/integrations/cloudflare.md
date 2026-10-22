# Cloudflare integrations

Four workflow templates (Automations → Templates). Three pull Cloudflare WAF activity, account audit logs and Security Center insights into the Alerts queue. The fourth blocks a case's IP for the whole account. Installing a template creates a **disabled** workflow. Add the secret, review the graph, dry-run it, then enable it. Template mechanics (`trigger.since`, `alert_create`, `fail`) are described in [the workflows README](../README.md).

| Template key | Kind | Trigger | Alert source | Token permission |
|---|---|---|---|---|
| `cloudflare-waf-top-ips-pull` | ingest | schedule `*/15 * * * *` | `cloudflare-waf` | Zone > **Analytics > Read** (`Analytics Read`) |
| `cloudflare-audit-logs-pull` | ingest | schedule `*/15 * * * *` | `cloudflare-audit` | Account > **Account Settings > Read** (`Account Settings Read`) |
| `cloudflare-security-insights-pull` | ingest | schedule `0 */6 * * *` | `cloudflare-security-center` | Account > **Security Center Insights > Read** (`Account Security Center Insights`) |
| `cloudflare-block-ip` | response | manual (from a case) | none | Account > **Account Firewall Access Rules > Edit** (`Account Firewall Access Rules Write`) |

## Secret and host

All four templates use one secret: **`CLOUDFLARE_API_TOKEN`**, allowed host **`api.cloudflare.com`**. It is sent as `Authorization: Bearer {{ secrets.CLOUDFLARE_API_TOKEN }}`, so no `auth` block is needed. Create a custom API token (My Profile > API Tokens > Create Token) with the permissions in the table for the templates you install, scoped to the account and zone(s). One token with all four permissions can serve all templates. Use a separate token for the block template if you want the read-only pulls to have no write access. If you do, the second token needs another secret name, so edit that template's header.

## Params

- `ACCOUNT_ID`: 32-character account ID (audit logs, insights, block IP).
- `ZONE_ID`: 32-character zone ID (WAF pull). Install the WAF template once per zone.
- `MIN_EVENTS`: a whole number (WAF pull). A client IP needs at least this many blocked or challenged requests in one window to raise an alert.

## Common behaviour of the pulls

- A status check follows every call. For the REST endpoints the check also needs `success: true`. For GraphQL it also needs **no `errors` array**, because the GraphQL Analytics API reports query errors with HTTP 200. Any other answer goes to a `fail` step that ends the run with `Cloudflare ... failed: HTTP <status>`, plus ` (GraphQL errors)` for a GraphQL error on HTTP 200. The response body is never copied into the message, and the next run re-reads the window.
- Every alert has a stable external id. Re-reading an item returns the existing alert and does not fire `alert.ingested` again.
- **One page per run** (ponytail ceiling). No cursor or page is followed. The limits are listed per template.

## WAF top blocked IPs: `cloudflare-waf-top-ips-pull`

**What it calls:** `POST https://api.cloudflare.com/client/v4/graphql` with JSON `{query, variables}`:

```graphql
query WafTopClientIPs($zoneTag: string, $start: Time, $end: Time) { viewer { zones(filter: {zoneTag: $zoneTag}) {
  firewallEventsAdaptiveGroups(filter: {datetime_geq: $start, datetime_lt: $end,
      action_in: ["block", "challenge", "js_challenge", "managed_challenge"]}, limit: 100, orderBy: [count_DESC]) {
    count dimensions { clientIP clientCountryName clientAsn clientASNDescription } } } } }
```

- **Window:** `start` is the previous successful run and `end` is this run. Both are shifted **5 minutes back**, so events indexed late are still counted, and each counted once. `start` is clamped to at most **6 hours** before `end`. The sandbox has no date filters, so the template computes the timestamps with integer date arithmetic in Jinja macros.
- Groups are by client IP only. Grouping by `action` or `source` as well would split one IP into several rows that share an external id.
- Each group with `count >= MIN_EVENTS` becomes an alert:
  - external id `<zone id>:<client IP>:<window start>`. A new window can alert on the same IP again, but a re-read window cannot duplicate.
  - title `Cloudflare WAF: <count> blocked/challenged requests from <ip> (<country>)`.
  - severity: `count` at least 10 × `MIN_EVENTS` is **high**, at least 3 × is **medium**, otherwise **low**. The traffic was already mitigated, so nothing is critical.
  - payload: the group (`count`, `dimensions`).

**Limits and ceilings:**
- Top **100** IPs per window (ordered by count). Other IPs over the threshold in a very noisy window are not alerted.
- **6-hour window ceiling.** Each plan sets the maximum time range one query may request for a dataset. Read yours with `settings { firewallEventsAdaptiveGroups { maxDuration } }` (zone scope). Cloudflare's published example value for `firewallEventsAdaptive` is 259200 s. 6 h keeps a catch-up run after an outage under the limit. Without the clamp, a run could fail with `query time range is too large` forever, because `since` would not advance. After an outage longer than 6 h, only the last 6 h are counted.
- `count` is Cloudflare's adaptive-sampling estimate (the sum of sample intervals), not an exact request count.
- Adaptive datasets keep at least 31 days on every plan. The GraphQL API allows 300 queries per 5 minutes per user, and this template uses one per run.

## Audit logs: `cloudflare-audit-logs-pull`

**What it calls:** Audit Logs **v2**, generally available since 2026-03-10. v1 still runs in parallel with no deprecation date yet.
`GET https://api.cloudflare.com/client/v4/accounts/<ACCOUNT_ID>/logs/audit?since=<previous run>&before=<this run>&direction=asc&limit=200&action_type.not=view`. Both `since` and `before` are required.

**Which entries alert:** an entry alerts when its `raw.uri` (lowercased) contains one of these paths:

| Severity | Paths | Covers |
|---|---|---|
| high | `/members`, `/tokens`, `/iam/`, `/roles`, `/sso` | members added/removed/role changes, account API token create/roll/delete (`/tokens/<id>/value` is a roll), IAM user and resource groups, SSO connectors |
| medium | `/access/`, `/firewall/`, `/rulesets`, `/rate_limits`, `/bot_management`, `/logpush/`, `/settings/security_level`, `/settings/ssl`, `/settings/min_tls_version`, `/settings/waf`, `/settings/browser_check`, `/settings/always_use_https`, `/settings/security_header` | Access apps, policies, IdPs and service tokens; IP Access rules and other firewall tools; WAF custom, managed and rate-limiting rulesets; bot settings; Logpush jobs (log tampering); security-related zone settings |

Each entry in the page is checked in its own loop child, by the `relevant` condition. To widen or narrow the list, edit the path lists in that condition and in `create`'s severity expression. For example, add `/dns_records` to watch DNS changes.

**Alert:** external id = entry `id`. The title is `Cloudflare audit: <action.description> by <actor.email | actor.token_name | actor.type>`, followed by ` on <zone>` when the entry has a zone and ` (failed)` when `action.result` is `failure`. The payload is the entry (actor IP, context, Ray ID, request URI and method).

**Not available:**
- **Login failures.** At GA, Audit Logs v2 does not log `GET` requests or `4xx` responses. Dashboard sign-ins are user-level events, not account audit entries.
- **Before and after values of a change.** These are not in v2 yet. Cloudflare suggests v1 (`GET /accounts/<id>/audit_logs`) for them.

**Limits:** 200 entries per 15-minute window, oldest first. `result_info.cursor` is not followed. To raise it, increase `limit` and the loop's `max_items` (500 at most) together. Logs are kept 18 months.

## Security Center insights: `cloudflare-security-insights-pull`

**What it calls:** `GET https://api.cloudflare.com/client/v4/accounts/<ACCOUNT_ID>/security-center/insights?dismissed=false&issue_type~neq=configuration_suggestion&per_page=500`.

- **No time filter.** The endpoint has none. Every run reads the current list and keeps items with `status: active`, and alerts are deduplicated on the insight `id`. So each insight raises one alert the first time it is seen, and the first run back-fills everything active now.
- **Alert:** title `Cloudflare Security Center: <issue class with spaces> on <subject>`. Severity maps `Critical` to critical, `Moderate` to medium and `Low` to low. The payload is the insight (`issue_type`, `issue_class`, `subject`, `resolve_text`, `resolve_link`, `payload.detection_method`, ...).
- **Changing the filters:** remove `issue_type~neq=configuration_suggestion` to also ingest suggestions. Add `&severity~neq=low` to drop low ones.
- **Scan frequency:** Free every 7 days, Pro and Business every 3 days, Enterprise daily. Automatic scans on inactive Free accounts may pause. The 6-hour schedule is frequent enough.

**Limits:**
- One page of 500 (the loop maximum). Above 500 matching insights, narrow the query.
- Every active insight is re-checked each run. The existing alert is returned, not duplicated.
- A resolved insight that becomes active again keeps its id and does not alert twice.

## Block IP: `cloudflare-block-ip`

**Run it from a case.** The IP is the case's first `ip` artifact. Without one, the run only writes a note: *"Cloudflare block not sent: this case has no IP artifact."*

`POST https://api.cloudflare.com/client/v4/accounts/<ACCOUNT_ID>/firewall/access_rules/rules`:

```json
{"mode": "block", "configuration": {"target": "ip", "value": "203.0.113.69"}, "notes": "Blocked from case #<id>: <case title, 150 chars max>"}
```

- **Target:** `ip6` when the value contains `:`, `ip_range` when it contains `/`. Ranges must be IPv4 /16 or /24, or IPv6 /32, /48 or /64. Cloudflare rejects other values with HTTP 400.
- **Scope:** an account-level rule applies to **every zone in the account**.
- **Case note:** records the HTTP status and, on success, the rule id. On failure it suggests checking the permission, an existing rule for the same IP, and the run history.
- **Unblocking:** Security > WAF > Tools, or `DELETE /accounts/<id>/firewall/access_rules/rules/<rule id>`.

**Why IP Access rules:** they are **not deprecated** (only the old Firewall Rules product is), they work on every plan, and they take effect with one call. Cloudflare recommends WAF custom rules with an IP list for new blocking setups. If you already block through a list referenced by a custom rule, change the request to `POST /accounts/<id>/rules/lists/<list id>/items` with `[{"ip": "<ip>", "comment": "case #<id>"}]` and the *Account Filter Lists Edit* permission. That call is asynchronous and returns an `operation_id`.

## Docs relied on

- GraphQL Analytics API: <https://developers.cloudflare.com/analytics/graphql-api/>. Firewall events tutorial: <https://developers.cloudflare.com/analytics/graphql-api/tutorials/querying-firewall-events/>. Limits: <https://developers.cloudflare.com/analytics/graphql-api/limits/>. Settings node: <https://developers.cloudflare.com/analytics/graphql-api/features/discovery/settings/>. Errors (HTTP 200 with `errors`, `Analytics: Read` on 403): <https://developers.cloudflare.com/analytics/graphql-api/errors/>.
- Action values `block`, `challenge`, `js_challenge`, `managed_challenge` in the GraphQL firewall datasets: <https://developers.cloudflare.com/logs/reference/change-notices/2023-02-01-security-fields-updates/>. Dataset filter, dimension and `orderBy` names were checked against the introspected GraphQL schema (`ZoneFirewallEventsAdaptiveGroupsFilter_InputObject`, `ZoneFirewallEventsAdaptiveGroupsDimensions`).
- Audit Logs: <https://developers.cloudflare.com/fundamentals/account/account-security/audit-logs/>. List account audit logs (v2): <https://developers.cloudflare.com/api/resources/accounts/subresources/logs/subresources/audit/methods/list/>. v2 GA: <https://developers.cloudflare.com/changelog/2026-03-10-audit-logs-v2-ga/>.
- Security Insights: <https://developers.cloudflare.com/security/security-insights/>. How it works (scan frequency): <https://developers.cloudflare.com/security/security-insights/how-it-works/>. Insights API: <https://developers.cloudflare.com/api/resources/security_center/subresources/insights/methods/list/>.
- IP Access rules: <https://developers.cloudflare.com/waf/tools/ip-access-rules/>. Create an account rule: <https://developers.cloudflare.com/api/resources/firewall/subresources/access_rules/methods/create/>.
- API token permissions: <https://developers.cloudflare.com/fundamentals/api/reference/permissions/>.
- Request and response field names were cross-checked against Cloudflare's published OpenAPI schema (`github.com/cloudflare/api-schemas`).
