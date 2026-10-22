# Cloud and SaaS integrations

Seven workflow templates (Automations → Templates) pull findings from AWS GuardDuty, AWS Security Hub, Microsoft Sentinel, Wiz, Proofpoint TAP and GitHub into the Alerts queue, plus a generic template that turns high and critical alerts from any source into cases. Installing a template creates a **disabled** workflow: add the secrets, review the graph, dry-run it, then enable it. Template mechanics (`trigger.since`, `alert_create`, `http_request.auth`, `fail`) are described in [the workflows README](../README.md).

| Template key | Kind | Trigger | Auth | Alert source |
|---|---|---|---|---|
| `aws-guardduty-findings-pull` | ingest | schedule `*/5 * * * *` | `aws_sigv4` | `aws-guardduty` |
| `aws-securityhub-findings-pull` | ingest | schedule `*/5 * * * *` | `aws_sigv4` | `aws-securityhub` |
| `sentinel-incidents-pull` | ingest | schedule `*/5 * * * *` | `oauth2_client_credentials` | `ms-sentinel` |
| `wiz-issues-pull` | ingest | schedule `*/5 * * * *` | `oauth2_client_credentials` + `audience` | `wiz` |
| `proofpoint-tap-pull` | ingest | schedule `*/5 * * * *` | `basic` | `proofpoint-tap` |
| `github-security-alerts-pull` | ingest | schedule `*/15 * * * *` | bearer header | `github` |
| `generic-alerts-to-case` | triage | `alert.ingested`, `alert.severity in ['critical', 'high']` | none | any |

## Common behaviour of the pulls

- Each pull asks for data since `trigger.since`, the fire time of the last **successful** run (one hour back on the first run, never more than 24 hours).
- A status check follows every vendor call. Any other answer goes to a `fail` step that ends the run as failed with `<vendor> ... failed: HTTP <status>`. The response body is never copied into the message. The next run then re-reads the same window.
- Every item becomes an `alert_create` with a stable vendor id as external id. Re-reading an item (overlapping windows, retries after a failure) returns the existing alert and does not fire `alert.ingested` again.
- **One page per run** (ponytail ceiling): no template follows `nextToken`/`nextLink`/cursors. The per-run maximum is listed per template below. A `for_each` with more items than its `max_items` fails the step, so every loop is sized to the page it reads.
- Secrets are bound to hosts. For OAuth2 templates, the client id and secret must list **both** the token host and the API host.

## AWS GuardDuty: `aws-guardduty-findings-pull`

**Params:** `AWS_REGION` (e.g. `eu-west-1`), `DETECTOR_ID` (GuardDuty > Settings, or `aws guardduty list-detectors`).
**Secrets:** `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, host `guardduty.<region>.amazonaws.com`.

**AWS side:** create an IAM user (no console access) with an inline policy allowing `guardduty:ListFindings` and `guardduty:GetFindings` on the detector ARN, or attach `AmazonGuardDutyReadOnlyAccess`. Create an access key. In a delegated administrator account, the detector also returns member accounts' findings.

**What it calls:**
1. `list`: `POST https://guardduty.<region>.amazonaws.com/detector/<id>/findings` (ListFindings), SigV4 service `guardduty`, body:
   `{"findingCriteria": {"criterion": {"updatedAt": {"greaterThanOrEqual": <since in epoch ms>}, "service.archived": {"equals": ["false"]}}}, "sortCriteria": {"attributeName": "updatedAt", "orderBy": "DESC"}, "maxResults": 50}`.
   The epoch-millisecond value is computed from `trigger.since` with integer date arithmetic in the template (a Jinja macro), because the sandbox has no date filters.
2. `has_ids`: no ids means the run ends successfully.
3. `details`: `POST .../detector/<id>/findings/get` (GetFindings) with `findingIds`.
4. One alert per finding: external id = finding `id`, title = `title (accountId/region)`, severity: 9.0-10 `critical`, 7.0-8.9 `high`, 4.0-6.9 `medium`, 1.0-3.9 `low`.

**Updates do not re-alert.** ListFindings cannot filter on `createdAt`, only `updatedAt`. GuardDuty updates a finding in place when the activity repeats (`service.count` goes up). The external id is the finding id only, so a repeat is re-read but deduplicated. To get one alert per update, change the external id to `{{ loop.item.id }}:{{ loop.item.updatedAt }}`.

**Limits:** 50 findings per 5-minute window (the ListFindings maximum), newest first. One region per installed workflow. Static keys only; to use temporary credentials, add `session_token` to both `auth` blocks.

Docs: [ListFindings](https://docs.aws.amazon.com/guardduty/latest/APIReference/API_ListFindings.html), [GetFindings](https://docs.aws.amazon.com/guardduty/latest/APIReference/API_GetFindings.html), [Condition](https://docs.aws.amazon.com/guardduty/latest/APIReference/API_Condition.html), [Severity levels](https://docs.aws.amazon.com/guardduty/latest/ug/guardduty_findings-severity.html).

## AWS Security Hub: `aws-securityhub-findings-pull`

**Params:** `AWS_REGION`. Use the aggregation (home) region if cross-Region aggregation is on.
**Secrets:** `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, host `securityhub.<region>.amazonaws.com`. The secret names match the GuardDuty template. When both templates use the same key, list both hosts on both secrets.

**AWS side:** an IAM user with `securityhub:GetFindings`, or `AWSSecurityHubReadOnlyAccess`.

**What it calls:** `POST https://securityhub.<region>.amazonaws.com/findings` (GetFindings, ASFF) with:
- `UpdatedAt` Start = `trigger.since`, End = this run's fire time
- `RecordState` ACTIVE
- `WorkflowStatus` NEW or NOTIFIED
- `SeverityLabel` CRITICAL, HIGH, MEDIUM or LOW (INFORMATIONAL, which includes passed control checks, is skipped)
- `SortCriteria` UpdatedAt desc, `MaxResults` 100

One alert per finding: external id = `Id` (last 255 characters), title = `Title (ProductName, AwsAccountId)`, severity from `Severity.Label`: CRITICAL `critical`, HIGH `high`, MEDIUM `medium`, LOW `low`, INFORMATIONAL `info`.

**Updates do not re-alert:** same rule as GuardDuty. Control findings are re-evaluated periodically, which bumps `UpdatedAt`, but deduplication on `Id` absorbs that.

**Overlap with GuardDuty:** GuardDuty findings are also imported into Security Hub. If you install both templates, add a `"ProductName": [{"Value": "GuardDuty", "Comparison": "NOT_EQUALS"}]` filter to the `pull` body.

**Limits:** 100 findings per window. GetFindings is throttled at 3 requests per second (burst 6).

Docs: [GetFindings](https://docs.aws.amazon.com/securityhub/1.0/APIReference/API_GetFindings.html), [DateFilter](https://docs.aws.amazon.com/securityhub/1.0/APIReference/API_DateFilter.html), [timestamps and throttling](https://docs.aws.amazon.com/securityhub/1.0/APIReference/Welcome.html), [ASFF required attributes](https://docs.aws.amazon.com/securityhub/latest/userguide/asff-required-attributes.html).

## Microsoft Sentinel: `sentinel-incidents-pull`

**Params:** `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID`, `RESOURCE_GROUP`, `WORKSPACE_NAME`.
**Secrets:** `SENTINEL_CLIENT_ID`, `SENTINEL_CLIENT_SECRET`, hosts `login.microsoftonline.com` and `management.azure.com`.

**Azure side:**
1. Register an app in Entra ID (single tenant) and create a client secret.
2. Assign the app's service principal the **Microsoft Sentinel Reader** role on the workspace or its resource group. Azure Resource Manager authorises by RBAC, so no API permission or admin consent is needed.

**What it calls:**
- Token: `POST https://login.microsoftonline.com/<tenant>/oauth2/v2.0/token`, scope `https://management.azure.com/.default`.
- `GET https://management.azure.com/subscriptions/<sub>/resourceGroups/<rg>/providers/Microsoft.OperationalInsights/workspaces/<ws>/providers/Microsoft.SecurityInsights/incidents?api-version=2025-09-01&$filter=properties/createdTimeUtc ge <since>&$orderby=properties/createdTimeUtc asc&$top=100`

One alert per incident: external id = incident `name` (GUID), title = `#<incidentNumber> <title>`, severity High `high`, Medium `medium`, Low `low`, Informational `info`. The payload is the incident resource, including `incidentUrl`. Incidents are pulled when they are created; later changes in Sentinel are not synced.

**Limits:** 100 incidents per window, oldest first. A 403 usually means the role assignment is missing or still propagating.

Docs: [Incidents - List](https://learn.microsoft.com/en-us/rest/api/securityinsights/incidents/list?view=rest-securityinsights-2025-09-01), [client credentials flow](https://learn.microsoft.com/en-us/entra/identity-platform/v2-oauth2-client-creds-grant-flow), [Sentinel roles](https://learn.microsoft.com/en-us/azure/sentinel/roles).

## Wiz: `wiz-issues-pull`

**Params:** `WIZ_API_HOST`: the API endpoint host from User settings > Tenant info, e.g. `api.us17.app.wiz.io`.
**Secrets:** `WIZ_CLIENT_ID`, `WIZ_CLIENT_SECRET`, hosts `auth.app.wiz.io` and your API host.

**Wiz side:** create a service account (Settings > Access Management > Service Accounts) of type *Custom Integration (GraphQL API)* with the `read:issues` scope. Add a project scope if needed.

**What it calls:**
- Token: `POST https://auth.app.wiz.io/oauth/token` with form fields `grant_type=client_credentials` and `audience=wiz-api`. Tenants on legacy Cognito auth (`auth.wiz.io`, audience `beyond-api`) or Wiz for Gov (`auth.app.wiz.us`) must change `token_url`, `audience` and the secrets' hosts.
- `POST https://<WIZ_API_HOST>/graphql` with the `issuesV2` query and these variables: `filterBy: {createdAt: {after: <since>}, status: [OPEN, IN_PROGRESS]}`, `first: 100`, `orderBy: {field: CREATED_AT, direction: ASC}`.

One alert per issue: external id = issue `id`, title = `<sourceRule.name> on <entitySnapshot.name>`, severity CRITICAL/HIGH/MEDIUM/LOW/INFORMATIONAL → `critical`/`high`/`medium`/`low`/`info`. GraphQL reports errors as HTTP 200 with an `errors` array. That case also fails the run, with `Wiz issues pull failed: HTTP 200 (GraphQL errors)`.

**Limits:** 100 issues per window. Issues are pulled when they are created; later status changes are not synced.

Docs: the Wiz API documentation (docs.wiz.io, *Using the Wiz API* and *Issues*) is behind the customer login. The token endpoint, the `wiz-api` audience and the `issuesV2` fields follow those pages and public integrations. Check the query in your tenant's GraphQL explorer before you enable the workflow.

## Proofpoint TAP: `proofpoint-tap-pull`

**Params:** none.
**Secrets:** `PROOFPOINT_PRINCIPAL` (Basic user name), `PROOFPOINT_SECRET` (Basic password), host `tap-api-v2.proofpoint.com`.

**Proofpoint side:** in the TAP dashboard, go to Settings > Connected Applications > Create New Credential to get a service principal and secret.

**What it calls:** `GET https://tap-api-v2.proofpoint.com/v2/siem/all?format=json&sinceSeconds=<n>` with HTTP Basic auth.
- `<n>` is the time since the last successful run plus a 5-minute overlap, capped at 3600. The API returns at most one hour per call and looks back at most 7 days.
- `sinceSeconds` counts back from the API server's clock, so the window always ends at "now" and a slow run never asks for a window the API rejects.

Alerts are created for:
- `messagesDelivered` (threat messages that reached a mailbox): external id `msg:<GUID>`, title `Delivered <classification> email to <recipients>: <subject>`, severity `high` for malware, phish or impostor, `low` for spam, `medium` otherwise.
- `clicksPermitted` (a user clicked a malicious URL and was let through): external id `click:<id>`, severity `critical` for malware or phish, `high` otherwise.

Blocked messages and blocked clicks are not ingested. Add loops over `messagesBlocked` and `clicksBlocked` if you want them.

**Limits:**
- At most 250 delivered messages and 250 permitted clicks per run. The loop inputs are sliced, and the rest of that window is dropped.
- If the last successful run is more than an hour old, or a run starts more than 5 minutes after its fire time, the older part of the window is not read.
- Rate limit: 1800 requests per rolling 24 h. A 5-minute schedule uses 288.

Docs: [TAP SIEM API](https://help.proofpoint.com/Threat_Insight_Dashboard/API_Documentation/SIEM_API).

## GitHub: `github-security-alerts-pull`

**Params:** `GITHUB_ORG`.
**Secrets:** `GITHUB_TOKEN`, host `api.github.com`.

**GitHub side:** the token's user must be an organization owner or security manager. Use one of:
- a fine-grained PAT on the org (all repositories) with repository permissions **Secret scanning alerts: Read** and **Dependabot alerts: Read**
- a GitHub App installation token with the same permissions
- a classic token with `security_events`, plus `repo` for private repositories' secret scanning alerts

**What it calls** (headers `Accept: application/vnd.github+json`, `X-GitHub-Api-Version: 2022-11-28`, `Authorization: Bearer`):
- `GET https://api.github.com/orgs/<org>/secret-scanning/alerts?state=open&sort=created&direction=desc&per_page=100`
- `GET https://api.github.com/orgs/<org>/dependabot/alerts?state=open&sort=created&direction=desc&per_page=100`

Neither endpoint has a time filter. The loops keep only alerts whose `created_at` is at or after `trigger.since`, so older open alerts are never back-filled.
- Secret scanning: external id `secret-scanning:<repo>:<number>`, title `<secret type> leaked in <repo>`, severity `critical` when `validity` is `active`, otherwise `high`.
- Dependabot: external id `dependabot:<repo>:<number>`, title `<advisory summary> (<package>, <repo>)`, severity = the advisory severity.

The two calls run in parallel branches. If one fails, the run fails with that call's status, but the other branch's alerts are still created.

**Limits:** 100 new alerts per kind per 15 minutes. Code scanning alerts are not pulled; add a branch for `GET /orgs/<org>/code-scanning/alerts` if you need them. Works on GitHub.com only; for GitHub Enterprise Server, change the host to `<host>/api/v3`.

Docs: [secret scanning org alerts](https://docs.github.com/en/rest/secret-scanning/secret-scanning?apiVersion=2022-11-28#list-secret-scanning-alerts-for-an-organization), [Dependabot org alerts](https://docs.github.com/en/rest/dependabot/alerts?apiVersion=2022-11-28#list-dependabot-alerts-for-an-organization), [API versions](https://docs.github.com/en/rest/about-the-rest-api/api-versions).

## Generic triage: `generic-alerts-to-case`

**Trigger:** `alert.ingested` with filter `alert.severity in ['critical', 'high']`. Medium, low and info alerts stay pending in the Alerts queue.

**Step:** one `alert_promote`:
- mode `group`, group key `source:<source>:<title>` (title cut to 200 characters), window 24 h
- the case gets the alert's title and severity and a tag with the source

Repeats of the same finding or rule land in one open case.

**Narrowing the filter** (edit the workflow's trigger filter):
- only some sources: `alert.severity in ['critical', 'high'] and alert.source in ['aws-guardduty', 'wiz']`
- skip sources that have their own triage template: `alert.severity in ['critical', 'high'] and alert.source not in ['crowdstrike', 'sentinelone', 'okta', 'm365-defender', 'entra-id']`
- critical only: `alert.severity == 'critical'`

Keep triage filters disjoint. When two triage workflows match the same alert, the second promote finds the alert no longer pending, and that run fails.

**Grouping per entity:** change `group_key`, for example per AWS account:
`source:{{ alert.source }}:{{ alert.payload.get('accountId') or alert.payload.get('AwsAccountId') or alert.title }}`.
