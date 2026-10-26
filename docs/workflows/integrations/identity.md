# Identity and Microsoft integrations

Workflow templates for Okta, Microsoft Entra ID (Identity Protection) and Microsoft 365 Defender. Install them from
Automations > Templates (admins). Each install creates a **disabled** workflow; add the listed secrets, review the
graph, then enable it. Node and template-context details are in [the workflows README](../README.md).

| Key | Kind | Trigger | What it does |
|---|---|---|---|
| `okta-system-log-pull` | ingest | every 5 min | Okta System Log security events → alerts (source `okta`) |
| `m365-defender-alerts-pull` | ingest | every 5 min | Graph `security/alerts_v2` → alerts (source `m365-defender`) |
| `entra-risk-detections-pull` | ingest | every 15 min | Graph `identityProtection/riskDetections` → alerts (source `entra-id`) |
| `identity-alerts-to-case` | triage | `alert.ingested` | groups identity alerts per user into a case, adds the user, applies a playbook |
| `okta-contain-user` | response | manual | suspends the case's user in Okta and revokes their sessions |
| `okta-suspend-user` | response | manual | suspends the case's user in Okta (sessions are not revoked) |
| `okta-reset-authenticators` | response | manual | resets all enrolled authenticators of the case's user; they must re-enroll |
| `entra-revoke-sessions` | response | manual | revokes the case's user's Entra sessions; optionally disables the account |

## How the ingest templates behave

All three follow the same graph: `schedule` trigger → `http_request` (`pull`) asking the vendor for events since
`{{ trigger.since }}` (the fire time of the last successful run, at most 24 h back; one hour back on the first run)
→ `condition` `steps.pull.output.status == 200` → `for_each` over the result list → `alert_create` with the vendor
item as payload. The condition's false branch goes to a `fail` step.

- **Duplicates**: `alert_create` is idempotent on (tenant, source, external_id), and each template uses the vendor's
  stable id as `external_id`, so an event read by two overlapping runs creates one alert and fires
  `alert.ingested` once.
- **Failures**: a non-200 answer (bad credentials, missing permission or consent, 429) writes nothing and fails the
  run with `<vendor> pull failed: HTTP <status>` (the response body is never put in the message; it is visible in the
  `pull` step). Network errors and 5xx are retried twice and then fail the run. Because `since` comes from the last
  successful run, the next run re-reads the failed window (capped at 24 h), and the overlap is deduplicated.
- **One page per run** (ponytail: pagination links are not followed). If a window holds more events than the page
  size, the rest are not ingested. Raise the page size in the URL and the loop's `max_items` (hard cap 500)
  together; `max_items` must be at least the page size or the loop step fails.
- **Severity** is mapped inside each template's `alert_create` step; edit the mapping there.

## Okta

### What is pulled

`GET https://<OKTA_DOMAIN>/api/v1/logs?since=<since>&sortOrder=ASCENDING&limit=200&filter=<expr>`, a polling
query (no `until`), authenticated as an Okta API Services app (private-key JWT, DPoP-bound token). The filter keeps these event types:

| Event type | Meaning |
|---|---|
| `security.threat.detected` | Request from an IP flagged by Okta ThreatInsight (brute force, password spray) |
| `security.breached_credential.detected` | A credential known to be breached was used |
| `security.session.detect_client_roaming` | Roaming session detected |
| `user.account.lock`, `user.account.lock.limit` | Account locked / lockout limit reached |
| `user.account.report_suspicious_activity_by_enduser` | User reported suspicious activity |
| `user.account.privilege.grant` | Admin privileges granted |
| `user.mfa.okta_verify.deny_push` | User rejected an Okta Verify push (Classic API) |
| `user.mfa.attempt_bypass` | Attempt to bypass a factor |
| `application.policy.sign_on.deny_access` | App sign-on policy denied access |

In Identity Engine a rejected push is usually `user.authentication.auth_via_mfa` with reason
`INVALID_CREDENTIALS`; it is left out because it also covers mistyped codes. Add it to the filter if you want it.

Mapping: source `okta`, external_id `uuid`, title `displayMessage (actor.alternateId)`, severity from the event
`severity`: `ERROR` → high, `WARN` → medium, `INFO` → low, `DEBUG` → info.

### Vendor-side setup

1. Create an Okta **API Services** app integration per privilege level (read-only pull, response). Client authentication = **Public key / Private key**; keep **Require DPoP header in token requests** ON; generate a key and copy the private key (JWK includes the `kid`).
2. Pull: grant scope `okta.logs.read` and the **Read-Only Administrator** role.
3. Response (`okta-contain-user`, `okta-suspend-user`, `okta-reset-authenticators`): grant `okta.users.manage` and **Help Desk Administrator** (cannot act on admins) or **Organization Administrator**.

Roles: https://help.okta.com/oie/en-us/Content/Topics/Security/administrators-admin-comparison.htm

### Secrets and params

| Secret | Allowed hosts | Used by |
|---|---|---|
| `OKTA_READ_PRIVATE_KEY` | `<OKTA_DOMAIN>` | `okta-system-log-pull` |
| `OKTA_RESPONSE_PRIVATE_KEY` | `<OKTA_DOMAIN>` | `okta-contain-user`, `okta-suspend-user`, `okta-reset-authenticators` |

Params: `OKTA_DOMAIN` (the org host, e.g. `acme.okta.com`, no scheme) and `OKTA_CLIENT_ID` (the API Services app's client ID). A PEM key has no `kid`: set `key_id` in the HTTP steps' auth block (an optional `OKTA_KEY_ID` param is not offered because template params cannot be empty).

### Response: `okta-contain-user`

Target: the case's `username` attribute (custom artifact type), else its first `email` artifact; without one the
workflow only writes a note. Steps: `GET /api/v1/users/<login>` to resolve the user id (not found → note);
`POST /api/v1/users/<id>/lifecycle/suspend` (200; only ACTIVE users can be suspended);
`DELETE /api/v1/users/<id>/sessions?oauthTokens=true` (204; also revokes OIDC/OAuth tokens, but not sessions held
by downstream apps); then a note with both HTTP statuses. Undo with `POST /api/v1/users/<id>/lifecycle/unsuspend`.

### Response: `okta-suspend-user`

Same target and user lookup as `okta-contain-user`, but only `POST /api/v1/users/<id>/lifecycle/suspend`, then a note
with the HTTP status. Sessions and tokens are not revoked. Undo with `POST /api/v1/users/<id>/lifecycle/unsuspend`.

### Response: `okta-reset-authenticators`

Same target and lookup; `POST /api/v1/users/<id>/lifecycle/reset_factors` removes every enrolled authenticator, so
the user must re-enroll. Sessions are not revoked (use `okta-contain-user` for that). There is no undo.

### Notes

- A 403 on a user lookup usually means the app's admin role cannot act on that user (Help Desk Administrator cannot
  act on admins; use Organization Administrator). If Okta reports a missing scope for the lookup, grant
  `okta.users.read` on the app and also add it to the HTTP node's `auth.scope` (space-separated:
  `okta.users.manage okta.users.read`).
- An explicit `key_id` in the auth block overrides the JWK's own `kid`.
- Workflows installed earlier with an API token (SSWS) keep working until they are reinstalled; reinstalling uses the
  API Services app described above.

### Rate limits and ceilings

- `limit=200` per run (Okta allows 0-1000; default 100).
- System Log keeps 90 days; each query times out after 30 s.
- Okta rate-limits per endpoint and org; a 429 (`E0000047`) fails the run and the window is re-read by the next
  run. Lower the schedule frequency if 429s repeat. An outage longer than 24 h leaves a gap (the `since` cap).

Docs: [List System Log events](https://developer.okta.com/docs/api/openapi/okta-management/management/tag/SystemLog/),
[System Log query](https://developer.okta.com/docs/reference/system-log-query/),
[Event types](https://developer.okta.com/docs/reference/api/event-types/),
[User lifecycle](https://developer.okta.com/docs/api/openapi/okta-management/management/tag/UserLifecycle/),
[User sessions](https://developer.okta.com/docs/api/openapi/okta-management/management/tag/UserSessions/),
[Admin roles](https://help.okta.com/oie/en-us/Content/Topics/Security/administrators-admin-comparison.htm),
[Rate limits](https://developer.okta.com/docs/reference/rl-global-mgmt/).

## Microsoft (Graph)

All Microsoft templates use `http_request.auth` with the client-credentials grant:
`POST https://login.microsoftonline.com/<AZURE_TENANT_ID>/oauth2/v2.0/token`,
`scope=https://graph.microsoft.com/.default`. The token is cached per worker until 60 s before it expires and is
never stored or shown. Because the token is bound to the same hosts as the secrets that obtained it, **every client
ID and client secret secret must allow both `login.microsoftonline.com` and `graph.microsoft.com`**.

### Vendor-side setup

1. Entra admin center > App registrations > New registration (single tenant, no redirect URI).
2. API permissions > Microsoft Graph > **Application permissions**, as listed below.
3. **Grant admin consent** for the tenant.
4. Certificates & secrets > New client secret; store the secret value. Track its expiry: an expired secret makes
   the token request fail with `token request failed: HTTP 401`.

Use one app for the read-only pulls and a separate one for response actions.

| Template | Application permissions | Notes |
|---|---|---|
| `m365-defender-alerts-pull` | `SecurityAlert.Read.All` | Sentinel alerts appear only if Sentinel is onboarded to the Defender portal |
| `entra-risk-detections-pull` | `IdentityRiskEvent.Read.All` | Needs Entra ID P1 or P2; without P2 most detections are `generic` / `hidden` |
| `entra-revoke-sessions` | `User.RevokeSessions.All`, `User.EnableDisableAccount.All`, `User.Read.All` | Disabling a user who holds an Entra admin role also needs the app to hold a sufficiently privileged admin role; otherwise Graph answers 403 |

### Secrets and params

| Secret | Allowed hosts | Used by |
|---|---|---|
| `MS_CLIENT_ID`, `MS_CLIENT_SECRET` | `login.microsoftonline.com`, `graph.microsoft.com` | both pulls |
| `ENTRA_RESPONSE_CLIENT_ID`, `ENTRA_RESPONSE_CLIENT_SECRET` | `login.microsoftonline.com`, `graph.microsoft.com` | `entra-revoke-sessions` |

Param `AZURE_TENANT_ID`: the Directory (tenant) ID.

### Microsoft 365 Defender alerts

`GET https://graph.microsoft.com/v1.0/security/alerts_v2?$filter=createdDateTime ge <since>&$top=100`.
Mapping: source `m365-defender`, external_id `id`, title `title (serviceSource)`, severity `high` → high,
`medium` → medium, `low` → low, `informational` → info, `unknown` → medium. The payload keeps `evidence`, which
the triage template reads for `userAccount.userPrincipalName`.

Ceilings: 100 alerts per run (`@odata.nextLink` not followed); Graph allows 150 requests per minute per app per
tenant on security alerts; the window is on `createdDateTime`, so an alert that becomes visible in Graph after the
next run has started can be missed.

### Entra ID Protection risk detections

`GET https://graph.microsoft.com/v1.0/identityProtection/riskDetections?$filter=detectedDateTime ge <since>&$top=200`.
Mapping: source `entra-id`, external_id `id`, title `Entra risk: <riskEventType> for <userPrincipalName>`,
severity from `riskLevel`: `high` → high, `medium` → medium, `low` → low, `hidden` → medium, `none` → info.

Ceilings: 200 per run (`$top` max is 500; default 20); Identity Protection allows **one request per second per
tenant for all apps** and sends no `Retry-After` on 429; offline detections written with a `detectedDateTime`
older than the previous run are missed.

### Response: `entra-revoke-sessions`

Target: the case's `username` attribute, else its first `email` artifact, used as the user principal name; without
one the workflow only writes a note. Steps: `POST /v1.0/users/<upn>/revokeSignInSessions` (2xx; can take a few
minutes to take effect; guest users are not affected); if the case is tagged `entra-disable`,
`PATCH /v1.0/users/<upn>` with `{"accountEnabled": false}` (204); then a note with both statuses.

Docs: [Client credentials flow](https://learn.microsoft.com/en-us/entra/identity-platform/v2-oauth2-client-creds-grant-flow),
[List alerts_v2](https://learn.microsoft.com/en-us/graph/api/security-list-alerts_v2?view=graph-rest-1.0),
[alert resource](https://learn.microsoft.com/en-us/graph/api/resources/security-alert?view=graph-rest-1.0),
[List riskDetections](https://learn.microsoft.com/en-us/graph/api/riskdetection-list?view=graph-rest-1.0),
[riskDetection resource](https://learn.microsoft.com/en-us/graph/api/resources/riskdetection?view=graph-rest-1.0),
[revokeSignInSessions](https://learn.microsoft.com/en-us/graph/api/user-revokesigninsessions?view=graph-rest-1.0),
[Update user](https://learn.microsoft.com/en-us/graph/api/user-update?view=graph-rest-1.0),
[Throttling limits](https://learn.microsoft.com/en-us/graph/throttling-limits).

## Triage: `identity-alerts-to-case`

Trigger `alert.ingested`, filtered to alerts from `okta`, `entra-id` or `m365-defender` that name a user:
`actor.alternateId` (Okta), `userPrincipalName` (Entra), or the first `evidence[].userAccount.userPrincipalName`
(Defender; device-only alerts are ignored).

1. `alert_promote` mode `group`, group key `user:<principal>`, window 24 h, tags `identity, <source>`: alerts for
   the same user within a day share one open case. A new case takes the alert's severity when it is `critical` or
   `high`, otherwise `medium`; a case that later alerts join keeps the severity it was created with.
2. `case_add_artifact`: the principal as `email` (or `other` without `@`).
3. On a new case only: `case_apply_playbook` with `PLAYBOOK_ID`, the id of your imported
   "Compromised Identity (T1078)" playbook (Playbooks > Marketplace > import, then copy the id).

No secrets. Param `PLAYBOOK_ID`.

## Testing

`backend/tests/test_wf_templates_identity.py` installs each template through the API into a throwaway tenant and
dry-runs it with mocked vendor responses shaped like the documentation examples, checking the rendered URLs and the
`alert_create` source, external id, title and severity.
