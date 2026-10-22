# EDR integrations: CrowdStrike Falcon and SentinelOne

Five workflow templates (Automations → Templates) pull EDR detections into the Alerts queue, triage them into cases and run containment from a case. Installing a template creates a **disabled** workflow; add the secrets, review the graph, dry-run it, then enable it. Template mechanics (`trigger.since`, `alert_create`, `http_request.auth`) are described in [the workflows README](../README.md).

| Template key | Kind | Trigger | What it does |
|---|---|---|---|
| `crowdstrike-alerts-pull` | ingest | schedule `*/5 * * * *` | Falcon Alerts API v2 → one pending alert per Falcon alert |
| `sentinelone-threats-pull` | ingest | schedule `*/5 * * * *` | SentinelOne threats → one pending alert per threat |
| `edr-alerts-to-case` | triage | `alert.ingested`, `alert.source in ['crowdstrike', 'sentinelone']` | dismiss low/info; group the rest into one case per host, add the hostname artifact, apply the playbook |
| `crowdstrike-contain-host` | response | manual (from a case) | find the Falcon device by hostname, network-contain it, note the result |
| `sentinelone-disconnect-agent` | response | manual (from a case) | find the agent by computer name, disconnect it from the network, note the result |

## CrowdStrike Falcon

### What is pulled

Every 5 minutes `crowdstrike-alerts-pull`:

1. `ids`: `GET https://<FALCON_API_HOST>/alerts/queries/alerts/v2?filter=created_timestamp:>='<since>'&sort=created_timestamp|asc&limit=100` (the FQL filter is URL-encoded). `<since>` is the fire time of the last successful run.
2. `ids_ok`: on any answer other than HTTP 200, `ids_failed` (`fail`) ends the run as failed with `CrowdStrike pull failed: alert id query returned HTTP <status>` (the response body is never included).
3. `has_ids`: no ids means an empty window: the run ends successfully without the entities call.
4. `details`: `POST /alerts/entities/alerts/v2` with `{"composite_ids": [...]}`. A non-200 answer goes to `details_failed`, which fails the run in the same way.
5. `loop` → `create` (`alert_create`), one per alert:
   - source `crowdstrike`, external id `composite_id`
   - title `display_name on device.hostname`
   - severity from the 1-100 `severity` score, using Falcon's own bands: 80-100 `critical`, 60-79 `high`, 40-59 `medium`, 20-39 `low`, 1-19 `info`
   - payload: the full alert entity

All Falcon products in the Alerts API are pulled (`epp`, `idp`, `overwatch`, `ngsiem`, `thirdparty` and so on). To keep endpoint detections only, add `+product:'epp'` inside the FQL filter in the `ids` URL.

### Vendor-side setup

1. In the Falcon console, open **Support and resources → API clients and keys → Create API client**.
2. Scopes:
   - `crowdstrike-alerts-pull`: **Alerts: Read**
   - `crowdstrike-contain-host`: **Hosts: Read** and **Hosts: Write**

   One client with all three scopes can serve both templates. For least privilege, use two clients and give the response workflow its own secret names: edit the installed graph and add matching secrets.
3. Copy the client ID and secret (the secret is shown only once).
4. For containment, the host's prevention policy must allow network containment.

### Secrets and allowed hosts

| Secret | Allowed hosts |
|---|---|
| `FALCON_CLIENT_ID` | `<FALCON_API_HOST>` |
| `FALCON_CLIENT_SECRET` | `<FALCON_API_HOST>` |

Both are used only inside `http_request.auth` (OAuth2 client credentials, token URL `https://<FALCON_API_HOST>/oauth2/token`). An OAuth token is bound to the same hosts as the secrets used to mint it. Falcon serves the token and the API from the same host, so that one host is enough. The token is cached per worker until 60 s before `expires_in` (Falcon: 1799 s). It is never stored or shown.

### Params

| Param | Example | Notes |
|---|---|---|
| `FALCON_API_HOST` | `api.crowdstrike.com` | US-1 `api.crowdstrike.com`, US-2 `api.us-2.crowdstrike.com`, EU-1 `api.eu-1.crowdstrike.com`, US-GOV-1 `api.laggar.gcw.crowdstrike.com`. Host only. |

### Containment (`crowdstrike-contain-host`)

- Target host:
  - the case's `hostname` attribute (custom artifact type `hostname`, see triage below)
  - otherwise `device.hostname` of the newest CrowdStrike alert promoted into the case
  - no host at all: a note is written and nothing is sent. Hostnames containing `'` or `\` are refused, so a value cannot break out of the FQL string.
- `find`: `GET /devices/queries/devices/v1?filter=hostname:'<host>'&limit=2`.
- `contain`: sent only when exactly one device matches: `POST /devices/entities/devices-actions/v2?action_name=contain` with `{"ids": ["<device id>"]}`. Falcon answers 202 when it accepts the request.
- `done` / `not_found`: a case note with the lookup and contain HTTP statuses. Lift containment from the Falcon console (or `action_name=lift_containment`).

### Rate limits

Falcon limits API calls per customer (CID) and reports the limit in the `X-RateLimit-Limit` / `X-RateLimit-Remaining` response headers; going over returns 429. The pull makes two API calls every 5 minutes, plus a token request about every 29 minutes per worker, which is far below any limit.

### Docs relied on

- OAuth2 token, `POST /oauth2/token` (success is **HTTP 201**): https://developer.crowdstrike.com/api-reference/collections/oauth2/
- Alerts v2: https://developer.crowdstrike.com/api-reference/collections/alerts/
- Hosts and device actions: https://developer.crowdstrike.com/api-reference/collections/hosts/
- Alert FQL fields (`created_timestamp`, severity bands, `composite_id`, `device.hostname`, timestamp filter examples): https://github.com/CrowdStrike/falcon-mcp/blob/main/falcon_mcp/resources/detections.py
- SDK cross-check (`composite_ids` body, `ids` body for `contain`): https://github.com/CrowdStrike/falconpy (`src/falconpy/alerts.py`, `_endpoint/_hosts.py`)

## SentinelOne

### What is pulled

Every 5 minutes `sentinelone-threats-pull`:

1. `pull`: `GET https://<S1_CONSOLE>/web/api/v2.1/threats?createdAt__gt=<since>&limit=100` with header `Authorization: ApiToken <token>`. `<since>` is the fire time of the last successful run.
2. `pull_ok`: on any answer other than HTTP 200, `pull_failed` (`fail`) ends the run as failed with `SentinelOne pull failed: threats query returned HTTP <status>` (the response body is never included).
3. `loop` → `create`, one per item of `data`:
   - source `sentinelone`, external id = threat `id`
   - title `threatInfo.threatName on agentRealtimeInfo.agentComputerName`
   - severity from `threatInfo.confidenceLevel` and `threatInfo.classification`: malicious + Ransomware `critical`, malicious `high`, suspicious `medium`, anything else `low`
   - payload: the full threat

Only threats are pulled. SentinelOne alerts from STAR / custom detection rules (`/web/api/v2.1/cloud-detection/alerts`) are not.

### Vendor-side setup

1. **Settings → Users → Service Users → Create New Service User.**
2. Scope it to the Account or Site to pull. Role:
   - **Viewer** for `sentinelone-threats-pull` only
   - a role that can run *Disconnect from network* (for example **IR Team** or **SOC**) for `sentinelone-disconnect-agent`
3. Choose an expiry date and copy the API token. Renew it before it expires: with an expired token every pull fails with HTTP 401. Once the token is renewed, the next run re-reads the missed window, up to 24 h back.

### Secrets and allowed hosts

| Secret | Allowed hosts |
|---|---|
| `S1_API_TOKEN` | `<S1_CONSOLE>` |

The token is sent as the `Authorization: ApiToken {{ secrets.S1_API_TOKEN }}` header.

### Params

| Param | Example | Notes |
|---|---|---|
| `S1_CONSOLE` | `usea1-acme.sentinelone.net` | The console host from the browser address bar. Host only. |

### Disconnect (`sentinelone-disconnect-agent`)

- Target host:
  - the case's `hostname` attribute
  - otherwise `agentRealtimeInfo.agentComputerName` of the newest SentinelOne alert promoted into the case
  - no host at all: a note is written and nothing is sent
- `find`: `GET /web/api/v2.1/agents?computerName__like=<host>&limit=50`.
- `disconnect`: sent only when exactly one returned agent has that exact computer name (case-insensitive): `POST /web/api/v2.1/agents/actions/disconnect` with `{"filter": {"ids": ["<agent id>"]}}`.

  The action is always sent by agent id, never by a name filter. A misspelled or ignored filter key on an agents action could otherwise match every agent in scope.
- `done` / `not_found`: a case note with the lookup and disconnect HTTP statuses and `data.affected`. Reconnect from the console (or `agents/actions/connect`).

### Rate limits

SentinelOne limits API calls per endpoint and per token and returns 429 when a limit is exceeded. Your console's API doc lists the per-endpoint numbers. The pull makes one call every 5 minutes.

### Docs relied on

The Management API reference is behind the console login (`https://<console>/api-doc/overview`). These public sources were used instead:

- Public open-source SentinelOne API clients. It confirms:
  - the `/web/api/v2.1/` base path
  - `Authorization: ApiToken <token>`
  - `GET threats` with `createdAt__gt` and `limit`
  - `pagination.nextCursor`
  - the `threatInfo.*` / `agentRealtimeInfo.agentComputerName` fields
  - `agents/actions/disconnect` with `{"filter": {"ids": [...]}}`
  - `computerName__like` on `GET agents`
- PS-SentinelOne module (`/web/api/v2.1/agents/actions/disconnect`, POST, `filter.ids`): https://powershellgallery.com/packages/PS-SentinelOne/2.1.2/Content/Public/Invoke-S1AgentAction.ps1

Double-check in your console's API doc before enabling:

- `createdAt__gt` and `limit` on `GET /threats`
- `computerName__like` on `GET /agents`
- the `threatInfo.confidenceLevel` values (`malicious`, `suspicious`, `n/a`)

## Triage (`edr-alerts-to-case`)

Setup:

1. Import **EDR Detection Triage** from Playbooks → Marketplace and enter its id as `PLAYBOOK_ID`.
2. Create the custom artifact type `hostname` (Settings → Artifact types). If it is missing, the `host_artifact` step records an error and the run continues (`continue_on_error`). The response templates then fall back to the alert payload.

Rules:

- The rules use `alert.severity`, set by the pull templates from the CrowdStrike score bands and the SentinelOne confidence.
- `is_low`: `low` and `info` alerts are dismissed (`alert_dismiss`, reason `auto: <severity> severity <source> alert`).
- Otherwise `promote` (`alert_promote`, mode `group`):
  - group key `host:<hostname>`, window 24 h, title `EDR: <alert title>`, tags `edr` and the source
  - case severity = the alert's severity (set when the case is created; a later, more severe alert does not raise it)
  - alerts from the same host join the newest open case for that host
  - an alert without a hostname gets its own case (`host:unknown:<external id>`)
- Then `host_artifact` (`case_add_artifact`, type `hostname`) and `playbook` (`case_apply_playbook`).

## Known ceilings (both vendors)

- **One page per run.** At most 100 items per run. A full page is not paginated (no `offset`/`after` for Falcon, no `nextCursor` for SentinelOne). After a successful run the next one starts at this run's fire time, so items beyond the first page of a window are not fetched. To raise the ceiling, change `limit` on the HTTP node and `max_items` on `loop` together (`max_items` hard cap 500; Falcon ids max 10000; SentinelOne max 1000).
- **Failed pulls are re-read, up to 24 h.** Any non-200 answer (401 expired token, 403 missing scope, 429 throttled) fails the run through the `fail` step. 5xx and network errors are retried twice, then fail the run. `trigger.since` is the fire time of the last *successful* run, so the next run re-reads the failed window, at most 24 h back. Overlaps are deduplicated by `alert_create`. An outage longer than 24 h loses the older part: review it in the vendor console.
- **Creation time, not update time.** Falcon `created_timestamp` / SentinelOne `createdAt` select new items only. Later changes on the vendor side (status, verdict, more detections in the same incident) do not update existing alerts.
- **Falcon OAuth token cache is per worker process.** Each worker requests its own token.
