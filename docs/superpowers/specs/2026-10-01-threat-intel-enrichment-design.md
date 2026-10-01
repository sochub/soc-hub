# Threat-Intel Enrichment — Design

Date: 2026-10-01 · Status: approved in brainstorming · Branch: `feat/threat-intel`

## Goal

When an analyst opens an IOC or an artifact (IP, domain, URL or file hash), they see reputation context from external threat-intel sources without leaving SOC Hub. Each source shows a verdict, a score, a few key facts, a link and the time it was checked. For IOCs, SOC Hub also *suggests* a higher threat level and new tags, which the analyst applies with one click.

## Decisions (from brainstorming)

| Topic | Decision |
|---|---|
| Sources | No-key sources on by default: **RDAP** and **crt.sh**. Keyed sources, each tenant bringing its own key: **VirusTotal v3**, plus **URLhaus** and **ThreatFox**, which share one free abuse.ch Auth-Key. Nothing else for now. |
| Effect on records | **Suggest only.** Nothing changes an IOC unless an analyst applies the suggestion. |
| When a value leaves the system | Automatic lookups only for TLP at or below the tenant's `auto_max_tlp` (default `green`). Manual "Enrich" works at any TLP, and AMBER/RED need explicit confirmation. Artifacts have no TLP field and are treated as the tenant's `artifact_tlp` (default `amber`). Lookups only; files are never uploaded. |
| Architecture | Background service. Each source is a small module behind one interface. Lookups run as Celery tasks. Results are cached in the DB per (tenant, type, value, source). Per-tenant rate limits are kept in Redis. |

## Data model

### `tenant_enrichment_configs` (one row per tenant; defaults apply when absent)

| Column | Type | Default | Notes |
|---|---|---|---|
| `id` | int PK | | |
| `tenant_id` | FK tenants, unique, `ondelete=CASCADE` | | |
| `auto_max_tlp` | str | `green` | One of `none`, `white`, `green`, `amber`, `red`. `none` turns off automatic lookups. |
| `artifact_tlp` | str | `amber` | One of `white`, `green`, `amber`, `red`. |
| `cache_ttl_hours` | int | 24 | Must be 1–720. |
| `sources` | JSON | `{"virustotal": true, "urlhaus": true, "threatfox": true, "rdap": true, "crtsh": true}` | A keyed source runs only when it is enabled **and** its key is set. |
| `vt_per_minute` | int | 4 | Must be 1–1000. Paid keys can raise it. |
| `vt_per_day` | int | 500 | Must be 1–1,000,000. |
| `internal_domains` | JSON list[str] | `[]` | Values equal to, or under, these domains are never sent out. |
| `credentials_enc` | Text | null | Fernet-encrypted JSON `{"virustotal_api_key", "abusech_auth_key"}` via `app/utils/crypto.py`. |
| `created_at`, `updated_at` | timestamps | | |

### `enrichment_results` (shared by value; updated in place)

The row is unique on (`tenant_id`, `indicator_type`, `indicator_value`, `source`).

| Column | Type | Notes |
|---|---|---|
| `id` | int PK | |
| `tenant_id` | FK tenants, `ondelete=CASCADE`, indexed | |
| `indicator_type` | str | Normalised: `ip`, `domain`, `url` or `file_hash`. |
| `indicator_value` | str | Normalised (see below). |
| `source` | str | `virustotal`, `urlhaus`, `threatfox`, `rdap` or `crtsh`. |
| `status` | str | `pending`, `ok`, `not_found`, `error`, `rate_limited` or `skipped`. |
| `verdict` | str null | `malicious`, `suspicious`, `harmless` or `unknown`. |
| `score` | str null | Display score, e.g. `45/70` or `confidence 75`. |
| `summary` | JSON | Small dict of key facts. Never the raw API response. |
| `link` | str null | Source UI link. |
| `error` | str null | Scrubbed, 300 characters or fewer. |
| `fetched_at` | timestamp null | Time of the last finished lookup. |

### Normalisation

- **Type mapping.**
  - IOC types: `ip_address`→`ip`, `domain`, `url` and `file_hash` keep their names.
  - Artifact types: `ip`, `domain`, `url` and `file_hash` keep their names.
  - Everything else (`email`, `registry_key`, `mutex`, `user_agent`, `other`) is not enrichable, and the panel shows "Not enrichable".
- **Value normalisation by type.**
  - Trim whitespace.
  - Lowercase domains and hashes, and strip a trailing dot from domains.
  - Parse IPs with `ipaddress`; store the compressed form.
  - URLs: lowercase the scheme and host, and drop the fragment.
  - Hashes must be hex with a length of 32, 40 or 64. Anything else is not enrichable.
- **Never sent out (status `skipped`).** Private, loopback, link-local, multicast, reserved and unspecified IPs, and domains or URL hosts under `internal_domains`.

## Sources

Every source implements:

```python
class Source(Protocol):
    name: str                      # "virustotal", ...
    types: frozenset[str]          # supported indicator types
    needs_key: Optional[str]       # credential field name, or None
    async def lookup(self, itype: str, value: str, key: Optional[str]) -> LookupResult
```

`LookupResult` carries `status`, `verdict`, `score`, `summary`, `link` and `error`. Sources make HTTP calls through one shared `httpx.AsyncClient` factory with `timeout=15s`, `follow_redirects=False` (RDAP is the one exception) and a fixed `https://` host.

| Source | Types | Request | Verdict rule | Summary keys |
|---|---|---|---|---|
| VirusTotal v3 | ip, domain, url, file_hash | `GET https://www.virustotal.com/api/v3/{ip_addresses\|domains\|files}/{v}`; for URLs, `/urls/{base64url(url) without padding}`. Header `x-apikey`. | `last_analysis_stats.malicious` ≥3 → malicious. 1–2 malicious, or any suspicious → suspicious. 0 with ≥1 harmless or undetected → harmless. 404 → not_found. | `malicious`, `suspicious`, `harmless`, `undetected`, `reputation`, `popular_threat_label` (if present), `tags` (≤5) |
| URLhaus | url, domain, ip (host), file_hash (payload, md5 or sha256) | `POST https://urlhaus-api.abuse.ch/v1/{url\|host\|payload}/` form-encoded. Header `Auth-Key`. | `query_status == "ok"` with ≥1 entry → malicious. `no_results` → not_found. | `threat`, `url_status`, `tags` (≤5), `first_seen` |
| ThreatFox | ip, domain, url, file_hash | `POST https://threatfox-api.abuse.ch/api/v1/` JSON `{"query":"search_ioc","search_term":v}` (hashes: `search_hash`). Header `Auth-Key`. | Hit → malicious, with score `confidence N` (max `confidence_level`). `no_result` → not_found. | `malware_printable`, `threat_type`, `confidence_level`, `first_seen`, `tags` (≤5) |
| RDAP | domain, ip | `GET https://rdap.org/{domain\|ip}/{v}`. Follows ≤3 redirects; each hop must be `https` and pass `app/workflows/ssrf.assert_url_allowed(url, [])`. | Domain registered <30 days ago → suspicious. Otherwise unknown. 404 → not_found. | domain: `registrar`, `created`; ip: `name`, `country`, `asn`/`handle`, `cidr` |
| crt.sh | domain | `GET https://crt.sh/?q={domain}&output=json` | First certificate <30 days ago → suspicious. Otherwise unknown. Empty list → not_found. | `cert_count`, `first_cert` |

An invalid key (HTTP 401 or 403) → `error` with the message "invalid API key". HTTP 429 → `rate_limited`. A timeout or a 5xx response → `error`.

## Rate limits

- Redis fixed-window counters, keyed `ti:rl:{tenant}:{source}:{window}`, with an atomic `INCR` and `EXPIRE` done in a Lua script. This follows the existing `login_throttle` pattern.
- Defaults:

  | Source | Limit |
  |---|---|
  | VirusTotal | `vt_per_minute` per minute **and** `vt_per_day` per day |
  | abuse.ch | 60 per minute (URLhaus and ThreatFox each have their own counter) |
  | RDAP | 30 per minute |
  | crt.sh | 1 per 5 seconds **globally** (key `ti:rl:global:crtsh`) |

- **Per-minute or per-5s limit exhausted:** the task re-queues that source with a countdown until the window resets, retrying up to 10 times. If retries run out, the status becomes `rate_limited`.
- **Daily VirusTotal limit exhausted:** the status becomes `rate_limited` and the error reads "VirusTotal daily quota reached". There is no automatic retry.

## Triggers and flow

1. One entry point: `app/enrichment/schedule.py::schedule_enrichment(db, tenant_id, itype, value, tlp, *, force=False)`. It is called **after commit** at every place IOCs or artifacts are created, or have their value, type or TLP changed. The plan enumerates those places; they include the IOC/artifact API routes, Copilot `add_artifact`, alert or webhook intake and workflow nodes.
2. `schedule_enrichment` does the following:
   1. Normalises the type and value. If the value is not enrichable, it returns.
   2. For automatic calls (`force=False`), it returns unless `tlp` is at or below `auto_max_tlp`. TLP order is `white` < `green` < `amber` < `red`, and `none` never matches.
   3. It upserts a `pending` row for each enabled, runnable source whose result is stale (older than `cache_ttl_hours`) or forced.
   4. It enqueues `enrich_indicator.delay(tenant_id, itype, value, sources, force)`.
   5. A Redis lock `ti:lock:{tenant}:{itype}:{sha256(value)}` with a 60 s NX lifetime prevents a duplicate enqueue.
3. The Celery task `enrich_indicator` checks the skip rules. For each source it checks the rate limit, calls `lookup`, then upserts the result with `fetched_at = now()`. It handles each source independently, so one source failing never blocks the others.
4. The task loads the tenant config and keys itself, decrypting them in the worker. Keys never travel in Celery messages.

## Suggestions (IOCs only, computed on read)

The suggestion is worked out from that IOC's current `ok` results.

- **Suggested threat level (raise only):**
  - `critical`: VirusTotal reports ≥15 malicious **and** URLhaus or ThreatFox reports malicious.
  - `high`: any source reports `malicious`.
  - `medium`: at least one source reports `suspicious` and none report `malicious`.
  - A level is suggested only if it ranks higher than the IOC's current `threat_level`. The order is `info` < `low` < `medium` < `high` < `critical`.
- **Suggested tags:** tags the IOC doesn't already have, at most 5, in this order:
  - `malware:<malware_printable lowercased>` from ThreatFox;
  - `urlhaus:<threat>`;
  - VirusTotal's `popular_threat_label`, lowercased.
- **No suggestion** if there is no level to raise and no new tag. Applying a suggestion goes through the existing IOC update endpoint, so it is audit-logged.

## API (`/api/v1/enrichment`, tenant from `get_effective_tenant_id`; cross-tenant access → 404)

| Method and path | Role | Behaviour |
|---|---|---|
| `GET /config` | admin | Returns the config fields and `credentials_set: {virustotal: bool, abusech: bool}`. Secrets are never returned. |
| `PUT /config` | admin | Validates ranges and TLP values. A blank or omitted key keeps the stored one; `clear_<key>: true` removes it. Calls `create_audit_log` (field names only). |
| `DELETE /config` | admin | Resets the tenant to defaults by deleting the row. Results are kept. |
| `POST /config/test` | admin | Looks up `8.8.8.8` on VirusTotal and the abuse.ch sources using the saved keys. Returns `{source: {ok, message}}`. |
| `GET /{ioc\|artifact}/{id}` | any member | Returns `{enrichable, indicator_type, effective_tlp, results: [...per source], suggestion: {threat_level?, tags[]} \| null}`. Artifacts never get a suggestion. |
| `POST /{ioc\|artifact}/{id}/run` | analyst+ | `force=True`. TLP amber or red needs a body with `{"confirm": true}`; without it the response is 409 with a warning `detail`. Returns 202 with the pending result list. Values that are not enrichable → 422. |

## UI

- **`EnrichmentPanel`** (IOC detail and artifact rows in CaseDetail):
  - one row per source: verdict badge, score, 2–3 summary facts, an external link, and relative `fetched_at`;
  - status texts for pending, not found, skipped (private or internal), quota reached, and errors;
  - an Enrich or Re-check button, shown to analysts and above, with a confirm dialog for AMBER or RED;
  - React Query polls every 3 s while any row is `pending`.
- **`EnrichmentSuggestionChip`:** amber, in the style of the copilot suggestion chip, with an **Apply** button that PATCHes the IOC.
- **IOC list:** a worst-verdict dot per row. The list endpoint gains an `enrichment_verdict` field, computed with one grouped query per page, not N+1 queries.
- **Integrations, "Threat intel" card** (admin, light theme like `AIProviderCard`):
  - write-only key fields showing "· set" hints;
  - a toggle per source;
  - the automatic TLP and artifact TLP settings;
  - cache TTL;
  - VirusTotal limits;
  - internal domains;
  - a Test button.
- Keyed by `active_tenant_id`.

## Logging and secrets

- Each lookup logs one line: `ti_lookup tenant=… source=… type=… outcome=… duration_ms=…`. Indicator values, keys and response bodies are never logged.
- Errors are passed through `app.ai.errors.scrub` together with the tenant's keys, then truncated to 300 characters.
- Keys are decrypted only in the worker task and in `/config/test`.

## Error handling

- A decrypt failure → that source records `error` "credentials could not be decrypted — re-enter in Integrations", and an ERROR log line with the tenant id only.
- A tenant config row that is missing → defaults apply, and keyed sources are inactive.
- If Celery is unavailable when enqueueing: rows stay `pending`, the API still returns 202, and the next manual run re-enqueues. Rows that stay pending more than 10 minutes are shown as "stalled — re-check".

## Testing

- **Sources:** each one is tested against `httpx.MockTransport` fixtures shaped like real responses: hit, miss, 401, 429, timeout and malformed JSON. RDAP additionally checks redirect-hop validation: a non-https hop is refused, and so is a private-IP hop.
- **Pure functions:** normalisation, the skip rules, the TLP rules, the verdict rules and the suggestion rules.
- **Rate limiter:** tested against the live Redis using unique key prefixes, deleted afterwards.
- **Task:** dedupe lock, per-source independence, retry on the minute limit, stop on the daily limit.
- **API:** in-process ASGI with `dependency_overrides` (as in `test_ai_api.py`), covering:
  - role checks;
  - cross-tenant 404;
  - 409 without confirm;
  - write-only secrets;
  - validation.
- **No test touches the network.** Tests that write to the DB delete only the exact rows they created.
- **Frontend:** `npm run build` and `npm run lint`.

## Out of scope

- Other keyed sources: AbuseIPDB, Shodan, GreyNoise, OTX.
- An "Enrich" workflow node.
- Promoting an artifact to an IOC.
- Dismissing suggestions.
- Auto-applying suggestions.
- Enriching email, registry key, mutex or user-agent values.
- Uploading files.
