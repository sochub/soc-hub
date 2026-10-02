# Incident Report Export — Design

Date: 2026-10-02 · Status: approved in brainstorming · Branch: `feat/incident-reports` (stacked on `fix/layout-spacing` #39 → `feat/evidence-attachments` #38)

## Goal

Analysts produce an incident report for a case in two forms:

- **PDF** for management and auditors. It is a curated report with two charts: the incident lifecycle with TTD/TTC/TTR, and a swimlane event timeline.
- **JSON** for other tools.

Both are generated from one shared report model. The narrative sections (executive summary, impact, lessons learned) are written by the analyst. "Draft with AI" can pre-fill them, but the analyst must save the text before it is used. Every export is audited with the SHA-256 of the bytes delivered.

## Decisions (from brainstorming)

| Topic | Decision |
|---|---|
| Formats | PDF (for people) and JSON (`sochub.incident-report/1`, for tools). STIX is later. |
| Content | A curated report by default, with an "Include full timeline & audit trail" option. The JSON always carries everything the option allows. |
| Narrative | Fields saved on the case and edited by the analyst. An optional "Draft with AI" uses the tenant's AI provider; drafts are never saved automatically. |
| Renderer | Jinja2 HTML templates turned into PDF by WeasyPrint on the server. Charts are SVG generated in Python, with no JS and no new chart dependency. |
| Charts | (1) An incident lifecycle bar with First seen → Detected → Contained → Recovered and the TTD/TTC/TTR durations. (2) A swimlane event timeline whose markers are numbered to match the key-timeline table. |

## Data model

### `case_reports` (migration `b6c7d8e9f0a1`, `down_revision = "a5b6c7d8e9f0"`)

| Column | Type | Notes |
|---|---|---|
| `id` | int PK | |
| `tenant_id` | FK tenants, CASCADE, indexed | |
| `case_id` | FK cases, CASCADE, **unique** | One report per case. The row is created on the first save. |
| `executive_summary` | Text null | ≤ 20,000 chars, plain text |
| `impact` | Text null | ≤ 20,000 chars |
| `lessons_learned` | Text null | ≤ 20,000 chars |
| `tlp` | String, not null, default `amber` | One of `white`, `green`, `amber`, `red`. TLP:WHITE is printed as "CLEAR". |
| `first_seen_at`, `detected_at`, `contained_at`, `recovered_at` | timestamptz null | Optional overrides of the computed milestones |
| `updated_by` | FK users null | |
| `updated_at` | timestamptz | |

Generated files are never stored.

## Report model — `app/reports/builder.py`

`async def build_report(db, case, *, full: bool, generated_by: User) -> dict` returns a dict that is plain JSON. All datetimes are ISO strings in UTC.

- **`meta`**: `schema = "sochub.incident-report/1"`, `generated_at`, `generated_by` (`{id, email}`), `app_version`, `full`.
- **`case`**: `id`, `title`, `severity`, `status`, `owner` (email or null), `created_at`, `resolved_at`, `tags`. Also `sla`: `{response, resolution}`, each one of `met`, `breached` or `pending`. Derive these from the existing SLA service and functions; read `app/services/sla*.py`.
- **`report`**: `executive_summary`, `impact`, `lessons_learned`, `tlp`, `updated_by`, `updated_at`.
- **`milestones`**:
  - `first_seen`, `detected`, `contained` and `recovered`, each `{at, source}`, where `source` is `"override"`, `"computed"` or `"unknown"`.
  - The durations `ttd_seconds`, `ttc_seconds` and `ttr_seconds`. Each is null when either end is unknown.
  - Computation, where an override always wins:
    - `first_seen`: the minimum of the case IOCs' `first_seen` and the `created_at` of linked alerts, if alerts link to cases (check the alerts model). When neither exists, it is unknown.
    - `detected`: the case's `created_at`.
    - `contained`: the latest `completed_at` among done tasks in phase `containment`. When there are none, it is unknown.
    - `recovered`: the case's `resolved_at`. When the case is unresolved, it is unknown.
- **`timeline`**: a list of `{n, at, lane, kind, text, actor}`, sorted by time.
  - Lanes and their sources:

    | Lane | Sources |
    |---|---|
    | `detection` | case created, linked alerts |
    | `analyst` | status changes, completed tasks, comments |
    | `indicators` | artifacts added, IOCs, enrichment results with verdict `malicious` |
    | `evidence` | `attachment_added` |

  - Use the existing `TimelineEvent` rows and their `event_type`s. Map unknown types to `analyst`.
  - Default ("key") mode keeps the event types listed above. `full` mode includes every timeline event.
  - `n` numbers events from 1.
- **`iocs`**: `{value, type, tlp, threat_level, verdicts: [{source, verdict, score}]}`, covering case IOCs and enrichable case artifacts. They are sorted malicious first, then by threat level. The `verdicts` come from `enrichment_results` keyed by the normalised value.
- **`actions`**: `{phase: [{title, completed_at, completed_by}]}` for done tasks. `outstanding` holds the tasks not done.
- **`evidence`**: `{filename, size_bytes, sha256, is_malicious, uploaded_by, created_at}` for non-deleted attachments.
- **`audit`**: only when `full`. It holds case audit logs plus attachment audit logs for the case: `{at, actor, entity, action}`. Do not include `changes` payloads.
- **`truncated`**: `{section: count_omitted}`. It applies to the PDF only; see the limits below. The JSON is never truncated.

### TLP floor

`min_tlp(case)` is the highest TLP among case IOCs, using the order white < green < amber < red. If the case has no IOCs, the floor is `white`.

- The export TLP is `max(requested or report.tlp, min_tlp)`.
- If the requested TLP is below the floor, the request is rejected with 422.
- `PUT` with a `tlp` below the floor also gets 422.

### Defanging (PDF only)

`defang(value, type)` applies these rules:

| Type | Rule |
|---|---|
| URL | scheme `http` → `hxxp`, `https` → `hxxps` |
| domain and URL host | `.` → `[.]` |
| IP | the last `.` → `[.]`; IPv6 `:` → `[:]` |
| email | `@` → `[@]` and dots in the domain → `[.]` |

Hashes and other types are left unchanged.

## Charts — `app/reports/charts.py` (pure Python SVG, no dependencies)

- **`lifecycle_svg(milestones) -> str`**
  - The canvas is 760×120 with `viewBox` set.
  - It draws three segments between the four milestones (dwell, response, recovery). Each segment's width is proportional to its duration, with a minimum of 8% each so short phases stay visible.
  - Each segment is labelled with its duration, formatted `2d 4h`, `6h 12m` or `45m`. The milestone names and dates go below.
  - An unknown milestone makes its adjacent segments render with a hatched pattern labelled "unknown".
  - Colours come from the theme: zinc greys and the accent blue `#2563eb`. Dwell uses the severity orange.
- **`timeline_svg(events) -> str`**
  - The canvas is 760×(60 + 44×lanes). There are 4 lanes with labels on the left, and the time axis runs from the first event to the last.
  - The axis has 5–8 ticks, labelled in UTC.
  - Each event is a numbered circle. Events closer than 14px on the same lane merge into one circle labelled with the count, e.g. "3", and keep the first `n`.
  - It handles 0 events with a "No events" message, and 1 event centred.
- All text goes through `xml.sax.saxutils.escape`. Use no external references, scripts or fonts other than the font-family names the PDF already loads.

## PDF — `app/reports/pdf.py` and `app/reports/templates/report.html.j2`

- Use a Jinja2 `Environment` with `autoescape=True`. Narrative fields render inside `<div class="prose">` with CSS `white-space: pre-wrap`, never as HTML.
- **WeasyPrint:**
  - `HTML(string=html, url_fetcher=deny_all_fetcher, base_url=None).write_pdf()`.
  - `deny_all_fetcher(url)` allows only `data:` URLs (delegating to `weasyprint.default_url_fetcher`) and raises `ValueError` for everything else.
  - Fonts are referenced via `@font-face` `src: url(data:font/woff2;base64,…)`, embedded from TTF/WOFF2 files vendored under `app/reports/fonts/`. The fonts are IBM Plex Sans (Regular and SemiBold) and Roboto Mono (Regular), all OFL.
- **Page CSS:**
  - `@page { size: A4; margin: 18mm 14mm 18mm 14mm; }`.
  - `@top-center` and `@bottom-center` show the TLP label, on a TLP colour background with white text. The colours follow the FIRST TLP 2.0 spec: RED `#FF2B2B`, AMBER `#FFC000` (black text), GREEN `#33FF00` (black text), CLEAR white with a black border.
  - `@bottom-right` shows "Case #0075 · page N of M".
- **Sections**, in order:
  1. Cover
  2. Executive summary, with the lifecycle chart and key facts
  3. Impact
  4. Key timeline: the swimlane chart, then the numbered table
  5. IOCs, defanged
  6. Actions taken, then Outstanding
  7. Evidence
  8. Lessons learned
  9. Appendix (when `full`): the full timeline and the audit trail
  10. Report integrity note
- **PDF row limits:**
  - timeline: 500 rows
  - IOCs: 1,000
  - evidence: 1,000
  - audit: 2,000

  Anything beyond a limit gets a "+N more — see JSON export" line.
- **Rendering:**
  - `render_pdf(report: dict) -> bytes` runs via `asyncio.to_thread`.
  - A module-level `asyncio.Semaphore(1)` allows one render per process.
  - The timeout is 60s (`asyncio.wait_for`); exceeding it returns 504 "Report rendering timed out".

## API — `app/api/v1/reports.py`, mounted under `/cases`

The case is resolved by `Case.id` and `tenant_id`; another tenant's case returns 404.

| Method and path | Role | Behaviour |
|---|---|---|
| `GET /{case_id}/report` | any member | `{report fields (or defaults if no row), milestones (computed + overrides, with source), min_tlp, ai_available}`. `ai_available` comes from `app.ai.llm.describe(tenant_id)` and is true when a provider is configured. |
| `PUT /{case_id}/report` | analyst+ | Upsert. Text fields max 20,000 chars. `tlp` is in the set and at or above `min_tlp`, else 422. Milestone overrides must be ordered first_seen ≤ detected ≤ contained ≤ recovered, else 422 "Milestones must be in chronological order". Audit: `entity_type="case_report"`, `action="update"`, changes = field names. |
| `POST /{case_id}/report/draft` | analyst+ | 409 "AI is not configured" when not available. Otherwise calls `AIService(tenant_id=…)`'s completion through `app.ai.llm.complete` (JSON mode). The prompt is built from `build_report(full=False)`'s case, timeline, IOCs, actions and evidence, with file names and hashes only. It returns `{executive_summary, impact, lessons_learned}` (strings, each truncated to 20,000 chars). An `AIError` gives 503 "AI drafting is unavailable", with the provider reason not exposed. Nothing is saved. |
| `GET /{case_id}/report/charts/{name}.svg` | any member | `name` is `lifecycle` or `timeline` (key mode). The response is `image/svg+xml` with `Content-Security-Policy: sandbox`, `X-Content-Type-Options: nosniff` and `Cache-Control: no-store`. |
| `GET /{case_id}/report/export` | analyst+ | Query `format=pdf\|json` (required), `full=false`, `tlp=<optional>`. A TLP below the floor gives 422. It builds the report, renders it, and computes the SHA-256 of the bytes. Audit: `entity_type="case"`, `entity_id=case_id`, `action="export_report"`, changes `{format, full, tlp, sha256, size_bytes}`. Returns the bytes. |

**Export response details:**
- Content-Type is `application/pdf` or `application/json`.
- `Content-Disposition: attachment; filename="case-{id:04d}-report-TLP-{LABEL}-{YYYYMMDD}.{ext}"`.
- `X-Content-SHA256` carries the hash.
- `Cache-Control: no-store` and `X-Content-Type-Options: nosniff` are set.

**Logging:** one line per operation, `report <op> tenant=%s case=%s format=%s full=%s outcome=%s`. Never log narrative text.

## Docker

- In the backend Dockerfile, `apt-get install --no-install-recommends libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz0b libharfbuzz-subset0 shared-mime-info`. These are the WeasyPrint runtime dependencies for current Debian slim; confirm against the WeasyPrint docs for the pinned version. Clean the apt lists afterwards.
- Pin `weasyprint==X.Y.Z` exactly in `requirements.txt`. pip-audit must stay clean.
- hadolint must stay clean (CI runs it non-blocking). Pin apt package versions only if the project already does.

## UI

There is a new **Report** tab in CaseDetail, after Evidence, implemented in `frontend/src/features/cases/CaseReport.tsx`. It uses `PageContainer` and the shared `Modal` from #39.

- **Header:**
  - A TLP select, coloured, with options below `min_tlp` disabled and the tooltip "Case IOCs require ≥ {MIN}".
  - A "Draft with AI" button, shown when `ai_available` is true and the user is analyst+. If any field has content, an inline confirm asks "Replace current text with an AI draft?".
  - A "Last saved by X · relative time" line.
- **Fields:** three textareas with character counters (20,000). Four `datetime-local` inputs show the computed value as placeholder text, with a "use computed" link that clears the override. Values are sent as UTC ISO.
- **Save:** analyst+ only. A dirty-state warning appears on tab switch or navigation; use `beforeunload` plus an in-app confirm before switching tabs. A 422 shows its `detail`.
- **Preview:** both SVGs are loaded as `<img src={blobUrl}>` fetched via axios with auth. Inline-injecting the SVG is not allowed. They refresh after a save.
- **Export** (analyst+):
  - "Export PDF" and "Export JSON" buttons, an "Include full timeline & audit trail" checkbox, and an export TLP select defaulting to the report TLP.
  - The download uses an `axios` blob plus `<a download>`, with the filename taken from Content-Disposition.
  - Only one export runs at a time. A 422 or 504 error is shown.
- Viewers see read-only fields and the previews, but no buttons.

## Testing

No network, no real AI. Temporary DB rows are deleted by exact id, and the `wf-test-%` leak count must be 0.

- **Builder:**
  - default vs full sections;
  - lanes mapping;
  - `n` numbering;
  - IOC sort order;
  - deleted evidence excluded;
  - verdicts joined from `enrichment_results`.
- **Milestones:**
  - computed, override, unknown;
  - durations;
  - order validation (422).
- **TLP floor:** enforced on PUT and export (422); the export label is used in the filename and the PDF.
- **Defang:** a parametrised table covering each rule above, with the JSON keeping raw values.
- **Charts:**
  - both are valid XML (`xml.etree.ElementTree.fromstring`);
  - near events merge;
  - 0 and 1 events;
  - `<script>` and `&` in labels are escaped;
  - unknown milestones are hatched.
- **PDF:**
  - the output starts with `%PDF`;
  - `deny_all_fetcher` raises for `http://169.254.169.254/`, `file:///etc/passwd` and a relative URL, and allows `data:`;
  - a case title `<img src=http://169.254.169.254/x>` renders escaped and no fetch is attempted (spy the fetcher);
  - the TLP label is present: extract the text with pypdf if it's available as a test dependency, otherwise assert on the uncompressed `TLP:AMBER` bytes by rendering with compression off.
- **API:**
  - roles: a viewer gets 403 on PUT, draft and export;
  - cross-tenant access gives 404;
  - the export audit `sha256` equals the hash of the response body;
  - draft returns 409 without AI and 503 on `AIError` (mock `app.ai.llm.complete`);
  - SVG responses carry the right headers;
  - a 504 is returned on timeout (monkeypatch `render_pdf` to sleep, with a short timeout setting).
- **Container:** the image builds, and an in-container smoke test renders a real PDF for a seeded temporary case.
- **Frontend:** `npm run build` and eslint.

## Out of scope

STIX 2.1, scheduled or emailed reports, a template editor, embedded PDF signatures, evidence thumbnails, and report versioning/history.
