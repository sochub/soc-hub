# SOC Hub Case Management — current feature inventory (for DFIR-IRIS gap analysis)

Snapshot: branch `feat/httponly-session-cookie`, 2026-10-18. Repo root: `/Users/santiagofriquet/Documents/github/sochub/case_management`.
All paths below are relative to the repo root unless absolute. Everything stated was read from code (no web lookups).

Stack: FastAPI + SQLAlchemy async + PostgreSQL (asyncpg) + Alembic; Celery + Redis workers; React 19 + TypeScript + Vite 8 + TanStack React Query 5 + React Router 7 + Tailwind CSS 4 + Framer Motion 12. AI via LiteLLM-style provider layer (Ollama default; OpenAI / OpenAI-compatible / Anthropic / Gemini / Vertex / Bedrock selectable per tenant).

---

## 1. Case object

### 1.1 Models — `backend/app/models/case.py`

**`cases`** (`Case`)
- `id`, `title` (indexed), `description` (Text), `status` Enum `CaseStatus` = `new|open|in_progress|pending|resolved|closed` (default `new`), `severity` Enum `CaseSeverity` = `critical|high|medium|low|info` (default `medium`)
- `tags` JSON list, `source` String (default `"user-reported"`), `group_key` (alert-storm dedupe key set by `alert_promote` workflow node)
- `owner_id` FK users (single owner/assignee), `tenant_id` FK tenants (NOT NULL, indexed), `playbook_template_id` FK playbook_templates, `guest_summary` Text (the only description external guests see)
- `created_at`, `updated_at`, `resolved_at`, `acknowledged_at` (first move off NEW; stops response SLA clock), `sla_response_breach_notified_at`, `sla_resolution_breach_notified_at`
- relationships: `alerts`, `timeline_events`, `case_artifacts` (M2M junction), `links` (CaseLink), `iocs`, `tasks` (CaseTask)
- NOT present on the case: customer/organisation/client, classification, case number/reference beyond integer id, due date, closure reason/outcome, case template, MITRE mapping, assets.

**`timeline_events`** (`TimelineEvent`) — **this is an activity log / comment stream, not an investigation timeline**
- Columns: `id`, `case_id` FK, `user_id` FK (nullable, system events), `event_type` String (free text; written values: `comment`, `status_change`, `severity_change`, `artifact_added`, `guest_comment`, `guest_invited`; UI also offers `investigation|containment|remediation|other`), `content` Text, `created_at` (server default now), `shared_with_guests` Boolean (default false), `guest_id` FK case_guests (SET NULL) with `guest_email` property.
- There is **no** `event_date`/`occurred_at` (the only timestamp is the insertion time `created_at`), no `title`, no category/tag, no asset/IOC/artifact FK links, no source/raw-event field, no TLP, no ordering besides creation time, no colour/flag, no "in summary/in graph" flags.
- Confirmed in schema `backend/app/schemas/case.py`: `TimelineEventBase{event_type, content}`, `TimelineEventCreate` = same, `TimelineEventUpdate{event_type?, content?}`, `TimelineEvent` response adds `id, case_id, user_id, created_at, user{id,email,full_name,has_avatar,avatar_version}, shared_with_guests, guest_id, guest_email`.
- Service `backend/app/services/case_service.py`: `add_timeline_note(db, *, case, content, user_id, event_type="comment")` (canonicalises `@mentions` for comments and fires `notify.on_comment`), `edit_timeline_note(...)`; `apply_case_update` auto-inserts `status_change` / `severity_change` rows; `add_artifact_to_case` inserts `artifact_added` rows (skipped for private custom types).
- Routes `backend/app/api/v1/cases.py`: `POST /cases/{id}/timeline`, `PUT /cases/{id}/timeline/{event_id}`, `DELETE /cases/{id}/timeline/{event_id}` (author-or-admin rule enforced via `resolve_active_role`). Timeline events are embedded in `GET /cases/{id}` (`Case.timeline_events: List[TimelineEvent]`) — there is no separate list endpoint, no filtering/pagination of events.
- Guest sharing: `PUT /cases/{id}/timeline/{event_id}/share` in `backend/app/api/v1/case_guests.py` toggles `shared_with_guests`; guests read via `GET /guest/cases/{id}/timeline` and post via `POST /guest/cases/{id}/comments` (`backend/app/api/v1/guest_portal.py`).
- Notifications link to timeline events: `notifications.timeline_event_id` FK (`backend/app/models/notification.py`); frontend `useOpenNotification.ts` deep-links to `/cases/{id}#event-{eventId}` and `CaseDetail.tsx` switches to the Timeline tab and scrolls to `id="event-{id}"`.
- Frontend renderer: `frontend/src/features/cases/CaseDetail.tsx` — tab `timeline` (default tab). `EVENT_TYPES = ['comment','status_change','artifact_added','investigation','containment','remediation','other']` (line 39). "Add Event" form = `<select event_type>` + textarea (`MentionTextarea` for comments). Each entry shows an icon by type (Database/Shield/MessageSquare/Calendar), `formatDateTime(event.created_at)`, type badge, guest badge, share-with-guests eye toggle, edit/delete (author or admin). Vertical list, newest/oldest by creation order; no date axis, no visual timeline, no grouping, no filters. The "Tasks/Report" components do **not** read timeline events for dates; `case_reports` holds its own milestone dates instead (see §4.6).

**`alerts`** (`Alert`) — `id, source, external_id, title, payload JSON, status (pending|promoted|dismissed), dismiss_reason, case_id FK nullable, tenant_id, created_at`; unique `(tenant_id, source, external_id)` for idempotent webhook ingest.

**`case_links`** (`CaseLink`) — `id, case_id, tenant_id, system ("Jira"/"ServiceNow"), external_id, url`. **No API routes read or write CaseLink** (only referenced by `app/db/base.py` and `app/utils/tenant_deletion.py`); the Jira integration (`POST /integrations/jira/sync`, `backend/app/services/jira_service.py`) creates an issue via Celery but does not surface links in the UI. Case-to-case relations exist only as display-only `case_triage_results.related_case_ids` JSON and the copilot `find_related` action (shared artifact values) — there is no persisted case↔case link table.

### 1.2 Schemas — `backend/app/schemas/case.py`
`CaseBase{title, description?, status, severity, owner_id?, tags[], source}`, `CaseCreate`, `CaseUpdate` (all optional + `guest_summary` ≤4000), `Case` response adds `id, created_at, updated_at, resolved_at, acknowledged_at, timeline_events[], guest_summary` and computed SLA fields `sla_response_target_minutes/status/due_at`, `sla_resolution_*`, `sla_overall_status` (attached by `_attach_sla` in cases.py from `app/utils/sla.py`). Also `Alert*`, `TimelineEvent*`, `CaseLink*` schemas.

### 1.3 Routes — `backend/app/api/v1/cases.py` (prefix `/api/v1/cases`)
`GET /` (skip/limit ≤500, ordered created desc; no server-side filters/search), `GET /tags`, `POST /` (analyst+; kicks off Celery `run_case_triage_task` and emits `case.created` workflow event), `GET /{id}/follow`, `PUT /{id}/follow`, `DELETE /{id}/follow`, `GET /{id}` (audited read `case.view`), `PUT /{id}` (emits `case.updated` with `changes` dict), timeline CRUD above. No `DELETE /cases/{id}`, no merge, no bulk ops, no export.
Other case-scoped routers mounted under `/cases`: tasks (`case_tasks.py`), triage (`case_triage.py`), attachments (`attachments.py`), reports (`reports.py`), guests (`case_guests.py`).

### 1.4 Service — `backend/app/services/case_service.py`
Single mutation path shared by HTTP, workflow nodes, Slack buttons and copilot: `create_case_record` (audit + `notify.on_case_created` + pending `CaseTriageResult`), `apply_case_update` (returns `changes{field:{from,to}}`, tags added/removed, `assignee` alias; sets `resolved_at`/`acknowledged_at`; audit + notifications + guest digest), `add_timeline_note`, `edit_timeline_note`, `add_artifact_to_case` (dedupe by value+type+tenant unless `isolated`), `add_artifact_by_key` (custom types), `add_alert_artifacts` (regex IOC sweep of alert payload capped at 64k chars/20 hits + `payload_key` mapped custom types).

### 1.5 SLA — `backend/app/utils/sla.py`, `backend/app/models/sla_policy.py`
`sla_policies(tenant_id, severity, response_target_minutes, resolution_target_minutes)` with `DEFAULT_SLA_MINUTES` per severity; states `not_tracked|met|on_track|at_risk|breached`; Celery sweep emails once per breach; UI `SLABadge.tsx`, `SLAPanel.tsx`, tenant policies editor in Settings (`GET/PUT /tenants/sla-policies`).

---

## 2. Artifacts / IOCs / custom types / enrichment

### 2.1 Artifacts — `backend/app/models/artifact.py`, `case_artifact.py`, `artifact_type_definition.py`
- `artifacts`: `id, tenant_id, artifact_type Enum(ip|domain|url|file_hash|email|other), value (indexed), description, isolated Bool, custom_type_id FK artifact_type_definitions (RESTRICT), created_at, created_by`. Shared across cases via M2M `case_artifacts(case_id, artifact_id, added_at, added_by)` (migration `e2f3g4h5i6j7`). Non-isolated artifacts dedupe by (value,type,tenant) so the same value links multiple cases (basis of the correlation graph).
- `artifact_type_definitions` (custom per-tenant types, PR #45): `tenant_id, key(32), label(64), show_in_mindmap, private (workflow-only, never in timeline/UI dropdowns), payload_key (dotted path into alert payload)`.
- Routes `backend/app/api/v1/artifacts.py`: `POST /artifacts/` (with `case_id`, `isolated`), `GET /artifacts/` (skip/limit), `GET /artifacts/case/{case_id}`, `PUT /artifacts/{id}`, `GET /artifacts/search/?value=` (exact value match), `DELETE /artifacts/{id}`, `DELETE /artifacts/{id}/case/{case_id}` (unlink). `backend/app/api/v1/artifact_types.py`: CRUD `/artifact-types/`.
- Frontend: `frontend/src/features/artifacts/ArtifactsList.tsx` (table, client-side value filter, isolated badge, shows multi-case links, edit/delete), `ArtifactForm.tsx`, `useArtifactTypes.ts` (builtin + custom options), `ArtifactMindMap.tsx` = `/graph` force-directed SVG graph (hand-rolled `requestAnimationFrame` simulation, no graph library) of cases ◉ / artifacts ▢ / IOCs ◇ with value-match bridges, filters, search, zoom/pan/drag, node dossier. Case detail tab `artifacts` (inline add form with type select, list with unlink). Settings → `ArtifactTypesSection.tsx` manages custom types.
- No artifact tags, no TLP on artifacts, no comments on artifacts, no file/hash-type-specific validation beyond regex IOC detection, no bulk import.

### 2.2 IOCs — `backend/app/models/ioc.py`, `backend/app/api/v1/iocs.py`, `backend/app/schemas/ioc.py`
- `iocs`: `id, tenant_id, case_id (nullable → tenant-wide IOC), ioc_type (ip_address|domain|url|file_hash|email|registry_key|mutex|user_agent|other), value, threat_level (critical..info), confidence 0-100, status (active|expired|whitelisted|false_positive), tlp (white|green|amber|red), first_seen, last_seen, source, tags JSON, description, created_at, created_by`.
- Routes: `GET /iocs/?case_id&status&ioc_type&skip&limit`, `POST /iocs/`, `GET/PUT/DELETE /iocs/{id}`.
- Frontend: `frontend/src/features/iocs/IOCList.tsx` under hub `/observables/iocs` (create modal, client-side filter, delete). IOCs are separate from artifacts (two tables, two tabs); the graph bridges them by equal value.
- No IOC export (STIX/CSV/MISP), no IOC import, no IOC sharing between tenants, no MISP/OpenCTI sync (grep for `stix`, `misp`, `csv` in IOC code: none).

### 2.3 Threat-intel enrichment — `backend/app/enrichment/`, `backend/app/models/enrichment_result.py`, `tenant_enrichment_config.py`, `backend/app/api/v1/enrichment.py`
- Sources registry (`enrichment/sources/`): `virustotal`, `urlhaus`, `threatfox` (abuse.ch), `rdap`, `crtsh`. Per-tenant config: `auto_max_tlp` (auto-run gating by TLP), `artifact_tlp`, `cache_ttl_hours`, `sources` JSON, VT rate limits (`vt_per_minute/day`), `internal_domains`, encrypted credentials. Kill switch + session-hook triggers (`enrichment/hooks.py`) run enrichment on artifact/IOC create.
- `enrichment_results`: `tenant_id, indicator_type, indicator_value, source, status, verdict, score, summary JSON, link, error, fetched_at, updated_at` (cache keyed by indicator+source).
- Routes: `GET/PUT/DELETE /enrichment/config`, `POST /enrichment/config/test`, `GET /enrichment/{kind}/{obj_id}`, `POST /enrichment/{kind}/{obj_id}/run`.
- Frontend: `frontend/src/features/enrichment/EnrichmentPanel.tsx` (inside case artifacts tab), `useCanRun.ts`; Settings → Integrations `ThreatIntelCard.tsx`.

---

## 3. Alerts, triage, webhook ingestion
- Model `alerts` (see §1.1). Routes `backend/app/api/v1/alerts.py`: `POST /alerts/webhook` (auth `X-API-Key` per-webhook key → `deps.get_webhook_from_key`; idempotent on `(tenant, webhook name as source, external_id)`; emits `alert.ingested`), `GET /alerts/`, `POST /alerts/{id}/promote/{case_id}` (links alert to case, copies artifacts via `add_alert_artifacts`), `POST /alerts/{id}/dismiss` (with `dismiss_reason`).
- Webhooks table `backend/app/models/webhook.py`: `webhooks(tenant_id, name, key_hash sha256, key_prefix)`; managed via `GET/POST/DELETE /integrations/webhooks` (`integrations.py`) and Settings → Integrations UI.
- Frontend `frontend/src/features/alerts/AlertsList.tsx` (status filter pending/promoted/dismissed, Promote/Dismiss buttons) + `PromoteAlertModal.tsx` (promote into existing case or create a new case inline).
- Workflow nodes `alert_promote` (modes incl. `group` by `group_key`) and `alert_dismiss` (`backend/app/workflows/nodes/alerts.py`).
- AI triage on case create: `case_triage_results` (`backend/app/models/case_triage.py`): `triggered_by (auto_on_create|manual), status, proposed_severity, proposed_tags, severity_tags_status, proposed_playbook_template_id, playbook_status, related_case_ids JSON, next_steps_text, next_steps_status`; routes `POST /cases/{id}/triage`, `GET /cases/{id}/triage/latest`, `PATCH /cases/{id}/triage/{tid}` (confirm/dismiss each proposal); Celery task `app/tasks/triage.py`; UI `frontend/src/features/cases/TriagePanel.tsx`.

---

## 4. Other subsystems

### 4.1 Playbooks & case tasks — `backend/app/models/playbook.py`, `case_task.py`; routes `playbooks.py`, `case_tasks.py`
- `playbook_templates(tenant_id NULL = global marketplace, name, category, description, is_system, source_template_id)` + `playbook_task_templates(template_id, phase, title, description, order)`. `case_tasks(case_id, tenant_id, phase, title, description, status todo|done, order, source_template_id, completed_at, completed_by)`. Phases fixed: `identification|containment|eradication|recovery|lessons_learned` (`frontend/src/types/index.ts IR_PHASES`).
- Routes: `GET /playbooks/marketplace`, `GET/POST /playbooks/`, `GET/PUT/DELETE /playbooks/{id}`, `POST /playbooks/import`, `POST /playbooks/marketplace` (super admin publish), `GET/POST /cases/{id}/tasks`, `PUT/DELETE /cases/{id}/tasks/{tid}`, `POST /cases/{id}/apply-playbook/{tid}`. Seed: `python -m app.scripts.seed_playbooks`.
- UI: `frontend/src/features/playbooks/Playbooks.tsx` (My/Marketplace tabs), `TemplateModal.tsx`; case tab `tasks` → `CaseTasks.tsx`; New Case modal "start from playbook".
- Tasks have no assignee, no due date, no per-task comments/attachments, only `todo|done`.

### 4.2 Workflows / automation engine + Slack — `backend/app/workflows/`, `backend/app/models/workflow.py`, routes `workflows.py`, `slack.py`
- `workflows(tenant_id, name, description, enabled, trigger_type, trigger_filter, graph JSON, version)`, `workflow_runs(status, trigger_payload, graph_snapshot, depth, is_dry_run, dry_run_mocks, parent_run_id/parent_step_id (sub-runs), loop_index/loop_item, error, started/finished_at)`, `workflow_run_steps(run_id, node_id, status, input, output, error, attempt, wait_token, wait_expires_at)`.
- Triggers: `case.created, case.updated, alert.ingested, manual`. Node types (`node_types.py`): `trigger, condition, http_request, case_update, case_add_note, case_add_artifact, case_apply_playbook, case_search, alert_promote, alert_dismiss, for_each, slack_ask_user, slack_post_message`. SSRF guard + tenant `workflow_http_allowlist`; templating; dry-run with mocks; validation.
- Routes: `/workflows/*` CRUD + `validate/enable/disable/run/dry-run`, `/workflows/settings/http-allowlist`, `/workflow-runs/` list/detail/cancel.
- Workflow secrets: `tenant_secrets(name, value_enc, allowed_hosts, description, last_used_at)`; routes `/secrets/*` incl. `scan` and `convert/{workflow_id}`; nonce placeholders, host binding (PR #41).
- Slack: `slack_integrations(tenant_id, team_id, bot_token_enc, signing_secret_enc, default_channel)`; `GET/PUT/DELETE /slack/config`, `POST /slack/config/test`, `POST /slack/interactions` (buttons / ask-user answers). Services `slack_service.py`, `slack_actions.py`.
- Frontend `frontend/src/features/automations/`: `AutomationsList.tsx`, `WorkflowEditor.tsx` (React Flow via `@xyflow/react` 12), `WorkflowNode.tsx`, `NodeInspector.tsx`, `nodeCatalog.ts`, `DryRunDialog.tsx`, `RunsTable.tsx`, `RunDetail.tsx`, `CaseAutomation.tsx` (case tab `automation`: manual-run + runs for this case).

### 4.3 Notifications — `backend/app/notifications/`, `backend/app/models/notification.py`, routes `notifications.py`
- `notifications(tenant_id, user_id, case_id, type, actor_id, timeline_event_id, summary, read_at)`; types `mention|assigned|comment|status_change|severity_change|sla_breach`. `case_followers(case_id, user_id, tenant_id)`.
- Triggers in `notifications/service.py`: `on_comment`, `on_case_update`, `on_case_created`, `on_sla_breach`. `@mentions` canonicalised (`mentions.py`, `GET /users/mentionable`). External delivery after commit (`delivery.py`): Slack DM then email (`email_service.py`).
- Routes: `GET /notifications/`, `GET /notifications/unread-count`, `POST /notifications/read-all`, `POST /notifications/{id}/read`. UI: `frontend/src/features/notifications/` (`NotificationBell.tsx` in header, `NotificationsPage.tsx`, `useOpenNotification.ts`), `FollowButton.tsx`, `MentionTextarea.tsx`/`MentionText.tsx`.

### 4.4 Evidence / attachments — `backend/app/models/case_attachment.py`, `backend/app/api/v1/attachments.py`, `backend/app/services/attachments.py`, `backend/app/storage/`
- `case_attachments(tenant_id, case_id, filename, content_type, size_bytes, sha256, is_malicious (user-declared checkbox at upload; no AV scan), storage_key, description, uploaded_by, uploaded_by_guest_id, created_at, deleted_at, deleted_by)` — soft delete, audit log carries filename+sha256.
- Routes: `POST /cases/{id}/attachments` (multipart `file`, `is_malicious`, `description`), `GET /cases/{id}/attachments`, `GET .../{att_id}/download`, `DELETE .../{att_id}`. Guest upload via `/guest/cases/{id}/attachments`.
- UI: case tab `evidence` → `frontend/src/features/cases/CaseEvidence.tsx`.
- No evidence-specific metadata beyond description/sha256 (no acquisition date, custodian, source host, evidence number, chain-of-custody log; "chain of custody" appears only as a comment in `reports/builder.py`).

### 4.5 Incident reports — `backend/app/models/case_report.py`, `backend/app/api/v1/reports.py`, `backend/app/reports/`
- `case_reports(case_id unique, executive_summary, impact, lessons_learned, tlp (default amber), first_seen_at, detected_at, contained_at, recovered_at, updated_by, updated_at)` — the four milestone dates live here, not on timeline events.
- Routes: `GET/PUT /cases/{id}/report`, `POST /cases/{id}/report/draft` (AI draft of summary/impact/lessons), `GET /cases/{id}/report/charts/{name}.svg`, `GET /cases/{id}/report/export?format=pdf|json&full=` (key vs full mode; TLP-based filename; defanged IOCs; audit row with sha256).
- Builder `reports/builder.py` assembles: case meta, owner, sla, milestones, timeline (activity log entries), iocs + enrichment verdicts, evidence list, tasks (phase/completed), actions/outstanding, charts (`charts.py`), Jinja template `reports/templates/report.html.j2` → PDF via `reports/pdf.py` (`render_pdf_async`, `RenderTimeout`).
- UI: case tab `report` → `frontend/src/features/cases/CaseReport.tsx` (dirty-state guard when switching tabs).
- Single fixed template; **no report template editor / DOCX export / per-tenant templates** (grep `docx`: none).

### 4.6 Case guests (external portal) — `backend/app/models/case_guest.py`, `backend/app/guests/`, routes `case_guests.py`, `guest_auth.py`, `guest_portal.py`; docs `docs/guests/README.md`
- `guest_accounts(email unique, password_hash, mfa_secret_enc, mfa_enabled_at, token_version)`, `case_guests(tenant_id, case_id, email, permission viewer|contributor, expires_at, revoked_at, invited_by, last_seen_at, guest_account_id)`, `guest_notifications(kind shared_entry|status_change, summary, sent_at)` (digest emails).
- Tenant settings `guest_domain_allowlist`, `guest_require_sso` (`tenants` table). Routes: `POST/GET /cases/{id}/guests`, `PATCH/DELETE .../{gid}`, `resend`, `reset-mfa`, `PUT /cases/{id}/timeline/{eid}/share`, `GET/PUT /tenants/current/guests`; guest API `/guest/auth/*` (link, register, login, mfa, forgot/reset) and `/guest/me`, `/guest/cases`, `/guest/cases/{id}`, `/timeline`, `/comments`, `/attachments`.
- Frontend: public routes `/guest/*` (`features/guest-portal/*`), analyst case tab `guests` → `features/guests/GuestsPanel.tsx`, Settings `GuestSettingsSection.tsx`.

### 4.7 Audit log — `backend/app/models/audit_log.py`, `backend/app/audit/`, `backend/app/utils/audit.py`, route `audit_logs.py`
- `audit_logs(category change|access|auth|..., entity_type, entity_id, action, changes JSON (scrubbed), user_id, user_email snapshot, actor (guest:<email>/system/workflow:<id>/webhook:<id>), tenant_id, outcome, status_code, method, route, ip, user_agent, request_id, duration_ms, created_at)`. Platform middleware `AuditMiddleware` records every request; `audit_read("case.view")` dependency marks sensitive reads.
- Routes: `GET /audit-logs` (cursor `next_before`), `GET /audit-logs/facets`, `GET /audit-logs/export?format=csv|json`, `GET /audit-logs/{entity_type}/{entity_id}`.
- UI: Settings → `features/audit/AuditLogPage.tsx` (admin), case tab `audit` renders `/audit-logs/case/{id}` as a from→to diff list.

### 4.8 Copilot AI — `backend/app/api/v1/copilot.py`, `backend/app/models/copilot_session.py`, `backend/app/ai/`, `backend/app/services/ai_service.py`, `backend/app/utils/copilot_heuristics.py`; docs `docs/copilot/`
- `copilot_sessions(case_id nullable = general chat, tenant_id, user_id)`, `copilot_messages(session_id, role, content, action JSON, suggestions JSON)`.
- Routes: `GET /copilot/sessions/general`, `GET /copilot/sessions/{case_id}`, `POST /copilot/sessions/{case_id}/restart`, `POST /copilot/chat`, `POST /copilot/analyze` (case analysis incl. prompt asking for MITRE ATT&CK techniques as free text — not stored structurally), `POST /copilot/actions/execute`.
- Action types (propose → confirm card): `create_case, add_artifact, add_timeline_note, update_case, apply_playbook, find_related`. Proactive suggestions: regex IOC detection → add_artifact chips; declarative findings → add_timeline_note.
- Tenant AI config `tenant_ai_configs(provider ollama|openai|openai_compatible|anthropic|gemini|vertex|bedrock, model, api_base, region, project, location, auth_mode, role_arn, external_id, credentials_enc)`; routes `/ai/config`, `/ai/config/test`, `/ai/info`.
- UI: global floating widget `features/copilot/CopilotWidget.tsx`, `CopilotChat.tsx`, `CopilotActionCard.tsx`; Settings → Integrations `AIProviderCard.tsx`.

### 4.9 Tenants, memberships, RBAC, SSO, MFA
- `tenants(name, slug, is_active, workflow_http_allowlist, require_mfa, guest_domain_allowlist, guest_require_sso)`; `tenant_memberships(user_id, tenant_id, role admin|analyst|viewer)`; `users(email, hashed_password, full_name, is_active, is_super_admin, job_title, timezone, password_login_enabled, avatar_key, token_version, mfa_secret_enc, mfa_enabled_at)`; `invitations(email, tenant_id, role, token, status, expires_at)`; `tenant_sso_configs(enabled, idp_entity_id, idp_sso_url, idp_x509_cert, auto_provision, default_role)` (SAML, `backend/app/services/saml_service.py`).
- Auth: `POST /auth/login/access-token`, `/auth/login/mfa`, `/auth/switch-tenant`, `/auth/logout`; JWT (`sub`, `active_tenant_id`) now held in an **HttpOnly cookie** (this branch), CSRF header `X-Requested-With` required on cookie-authenticated writes (`frontend/src/api/client.ts`). Deps: `get_current_active_user`, `require_admin`, `require_analyst_or_above`, `require_super_admin`, `get_effective_tenant_id` (`backend/app/api/deps.py`). Roles resolved from active membership (`app/utils/roles.resolve_active_role`).
- Users/admin routes `users.py` (invite, role, membership removal, deactivate, avatar, password, `/users/me`), `invitations.py`, `tenants.py` (super-admin CRUD, permanent delete via `utils/tenant_deletion.py`, `/tenants/{id}/users`), `mfa.py` (`/users/me/mfa/*`, admin reset, `/tenants/current/security`).
- UI: `features/admin/UserManagement.tsx`, `InviteUserModal.tsx`, `features/superadmin/*` (TenantManagement/Detail), `features/profile/ProfilePage.tsx` + `MfaSetupDialog.tsx`, Settings → SSO/MFA sections, sidebar `TenantSwitcher` in `components/layout/Layout.tsx`.
- RBAC is 3 coarse tenant roles + global super_admin + per-case guest grants. No per-case ACL for internal users, no custom roles/permissions matrix, no groups.

### 4.10 API docs page, dashboard, queue, hubs, ⌘K
- `/docs` → `features/docs/ApiDocs.tsx` renders Scalar (`@scalar/api-reference-react`) against `GET /api/v1/openapi.json` (auth-required; anonymous `/docs`/`/redoc` disabled in `backend/app/main.py`).
- Overview dashboard `features/cases/Dashboard.tsx` + `GET /stats/` (`stats.py`): KPI tiles, opened-vs-resolved 30d line, severity pie, severity×status heatmap, open-case aging, cases by status bar, IOC types bar, top shared indicators, recent activity, priority queue (Recharts 3).
- My Queue `features/queue/MyQueue.tsx`: assigned to me / SLA at risk-breached / unassigned (client-side from `GET /cases/`).
- Nav groups (`Layout.tsx` NAV_GROUPS): operate (Overview, My Queue, Cases, Alerts), intel (Observables hub [Artifacts|IOCs], Graph), automate (Playbooks, Workflows), admin (Settings hub). `HubLayout.tsx` renders hub tabs; `SettingsHub.tsx` tabs: General, Profile, Integrations, Users (admin), Audit (admin), Tenants (super admin).
- `CommandPalette.tsx` (⌘K): jump to case #, "New case", nav pages, search cases/IOCs/artifacts (client-side over `limit=500` fetches).
- Cases list `CasesList.tsx`: severity/status filters in URL, table or **board (kanban by status)** view (`CasesBoard.tsx`), `NewCaseModal.tsx`, `EditCaseModal.tsx` (title, description, severity, status, source, tags; owner change elsewhere).

---

## 5. Frontend structure

### 5.1 Routes — `frontend/src/App.tsx`
Public: `/login`, `/invite/:token`, `/guest/link[/:token]`, `/guest/login`, `/guest/sso`, `/guest/forgot`, `/guest/reset[/:token]`, `/guest/register`, `/guest`, `/guest/cases/:id`.
Protected (`ProtectedRoute` + `Layout`): `/` Dashboard, `/queue`, `/cases`, `/cases/:id`, `/alerts`, `/observables` (hub: index ArtifactsList, `iocs` IOCList), `/graph`, `/playbooks`, `/workflows`, `/workflows/runs/:runId`, `/workflows/:id`, `/docs`, `/settings` (hub: index Settings, `profile`, `integrations`, admin-only `users` + `audit`, super-admin `tenants`, `tenants/:id`). Legacy redirects in `MOVED` (e.g. `/automations`→`/workflows`, `/mindmap`→`/graph`). All heavy pages are `React.lazy`.

### 5.2 Case detail tabs — `frontend/src/features/cases/CaseDetail.tsx`
`CASE_TABS = ['timeline','tasks','artifacts','evidence','guests','report','network','audit','automation']` (line 41). Components: timeline (inline in CaseDetail), `CaseTasks.tsx`, artifacts (inline + `EnrichmentPanel`), `CaseEvidence.tsx`, `GuestsPanel.tsx` (hidden for viewers), `CaseReport.tsx`, **`network` = placeholder "Network graph visualization unavailable."**, audit (inline, reads `/audit-logs/case/{id}`), `CaseAutomation.tsx`. Header: title, severity chip, id, created date, owner (hard-coded label "Owner: System"), `FollowButton`, edit button; description panel; `SLAPanel`, `TriagePanel` above tabs. Tab switch animated with framer-motion `layoutId="activeTab"` underline and `AnimatePresence`.

### 5.3 Design system — `frontend/src/components/ui/index.ts`
Primitives exported: `Button` (+`ButtonProps`), `Input`, `Label`, `Card/CardHeader/CardTitle/CardContent`, `Badge` (+`severityVariant`). That is all — **no Dialog/Modal primitive in ui/** (modal lives in `components/layout/Modal.tsx` with exported class strings `modalInput, modalLabel, btnPrimary, btnSecondary, btnDanger`), no Tabs, Select, DatePicker, Table, Tooltip, Dropdown, Toast, Popover, Combobox, Textarea components; pages use raw `<select>`, `<input type="date">`-free forms and Tailwind classes. Layout helpers: `PageContainer.tsx`, `PageSkeleton.tsx`, `HubLayout.tsx`, `CommandPalette.tsx`; `components/Avatar.tsx`, `ErrorBoundary.tsx`; `lib/utils.ts` (`cn` via clsx + tailwind-merge); `utils/datetime.ts` (`formatDateTime` with user timezone), `useMyTimezone.ts`.
Theme: Tailwind v4 semantic tokens in `frontend/src/index.css` (`--color-canvas/surface/panel/raised/raised-2/line/line-strong/fg/fg-2/fg-3/muted/accent/accent-hover/on-accent/lime/magenta/severity-critical..info`, `--font-display/sans/mono`, console shadows) — dark sochub.io theme.

### 5.4 Libraries — `frontend/package.json`
Installed: `react 19.3`, `react-router-dom 7.18`, `@tanstack/react-query 5.104`, `axios`, `framer-motion 12.34` (used widely: CaseDetail tabs, modals, lists), `recharts 3.10` (Dashboard; report charts are server-side SVG), `@xyflow/react 12.12` (workflow editor only), `lucide-react`, `@radix-ui/react-icons` (icons only; no Radix primitives), `react-markdown` + `remark-gfm` (copilot/markdown), `@scalar/api-reference-react`, `qrcode` (MFA), `clsx`, `tailwind-merge`, Tailwind 4, Vite 8, TS 5.9. **Not installed**: vis-timeline, d3, cytoscape, dayjs/date-fns, react-datepicker, shadcn/Radix primitives, TipTap/rich-text editors, react-table.

### 5.5 Data-fetching pattern
`frontend/src/api/client.ts` exports `api` (axios, `baseURL:'/api/v1'`, cookie session, 401 → `auth:expired` event) and `session` flag; `frontend/src/api/queryClient.ts`; `api/auth.ts`. Pages call `useQuery({ queryKey:['cases'], queryFn: async () => (await api.get('/cases/')).data })` and `useMutation` + `queryClient.invalidateQueries` inline in components; a few shared hooks (`features/artifacts/useArtifactTypes.ts`, `features/notifications/api.ts`, `features/enrichment/useCanRun.ts`, `features/guest-portal/guestApi.ts` separate axios instance). Shared TS types in `frontend/src/types/index.ts` (`Case`, `TimelineEvent`, `Artifact`, `IOC`, `CaseTask`, `PlaybookTemplate`, `CaseTriage`, `Attachment`, `CaseReportView`, `AppNotification`, `AuditItem`, `CaseGuest`, `User`, `Membership`, `Secret`, `SSOConfig`, `CopilotAction`, SLA types, `TLPLevel`, `MilestoneKey`…). Workflow types live in `features/automations/types.ts`.

---

## 6. Backend conventions for adding a feature

- **Migrations**: `backend/alembic/versions/*.py`, hand-written (`revision`, `down_revision`, `upgrade()/downgrade()`); ids are 12-char pseudo-hashes (e.g. `f1a2b3c4d5e6_playbooks_and_tasks.py`). Current head (no child revision references it): **`f8b2c3d4e5a6`** — `backend/alembic/versions/f8b2c3d4e5a6_hash_webhook_keys_backfill_tags.py` (down_revision `e7a1b2c3d4f5` platform_audit_log). Chain is linear. Memory rule: never run data-losing downgrade/upgrade cycles on the dev DB.
- **Models**: `backend/app/models/<x>.py` using `app.db.base_class.Base`; register import in `backend/app/db/base.py` (mapper registry). Every data table carries `tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)`.
- **Tenant scoping**: endpoints take `tenant_id: int = Depends(deps.get_effective_tenant_id)` and `current_user = Depends(deps.get_current_active_user | require_analyst_or_above | require_admin)`; queries always add `.where(Model.tenant_id == tenant_id)`; cross-tenant → 404 (`HTTPException(404, "Case not found")`), never 403. Tenant permanent delete must be extended in `backend/app/utils/tenant_deletion.py`.
- **Audit**: `await create_audit_log(db, entity_type: str, entity_id: int, action: str, tenant_id: int, user_id: Optional[int]=None, changes: Optional[dict]=None, actor: Optional[str]=None)` in `backend/app/utils/audit.py` — flushes a `category="change"` row in the caller's transaction, `changes` scrubbed by `app/audit/redact.scrub`. Sensitive reads: `dependencies=[audit_read("case.view")]` from `app.audit.context`.
- **Services only flush; routers commit** (`case_service.py` docstring); after commit emit `emit_event(tenant_id, "case.updated", case_id=..., changes=...)` from `app.workflows.events` and notifications are delivered via after-commit hook.
- **Router registration**: create `backend/app/api/v1/<feature>.py` with `router = APIRouter()`, then in `backend/app/api/api.py` `api_router.include_router(<feature>.router, prefix="/<feature>", tags=["<feature>"])` (order matters where static paths collide with `/{id}` routes — see comments there). App mounts `api_router` at `/api/v1` in `backend/app/main.py`.
- **Schemas**: Pydantic v2 in `backend/app/schemas/<x>.py` (`class Config: from_attributes = True` or `model_config`), `model_dump(exclude_unset=True)` for PATCH semantics.
- **Background work**: Celery tasks in `backend/app/tasks/` (`app/worker.py`), Redis broker; macOS note: `docker restart case_management-backend-1 case_management-worker-1` after backend edits.
- **Tests**: `backend/tests/` (109 files, pytest, `conftest.py` autouse fixtures stub enrichment/notification/guest/SAML Redis and purge audit rows by id). Two styles: (a) pure-unit with stub `_DB` objects (`tests/test_case_changes.py`), (b) API tests against the **live dev Postgres** via `httpx.AsyncClient(transport=httpx.ASGITransport(app=app))` with `AsyncSessionLocal`/`engine` and per-test random tenant/user rows (`tests/test_reports_api.py`), overriding `deps` with `monkeypatch`. Cleanup must be scoped to explicit row ids (memory rule).
- **Frontend**: add page under `frontend/src/features/<area>/`, lazy-import + `<Route>` in `App.tsx`, nav entry in `components/layout/Layout.tsx NAV_GROUPS`, types in `types/index.ts`, fetch with `api` + React Query; frontend container is nginx → rebuild image per change.
- **Docs**: feature docs in `docs/` (`features.md`, `architecture.md`, `audit/`, `copilot/`, `guests/`, `playbooks/`, `slack/`, `sso/`, `workflows/`, `backlog/`).

---

## 7. Confirmed absences (grep-verified) relative to a DFIR case platform

| Capability | Evidence of absence |
|---|---|
| **Assets / hosts / accounts model** (compromised systems linked to case/IOC/events) | grep `asset` in backend → only `schemas/ai_config.py` (unrelated word); no model, route, or UI |
| **Investigation timeline with event dates** (event_date, category, source, linked assets/IOCs, graph/summary flags) | `timeline_events` has only `event_type, content, created_at, shared_with_guests, guest_id`; grep `event_date|occurred_at` → 0 hits; UI is a comment-style activity log |
| **MITRE ATT&CK mapping** (techniques/tactics on case or events) | grep `mitre|att&ck` → only prose in `scripts/seed_playbooks.py` and the copilot analysis prompt in `services/ai_service.py`; no columns/UI |
| **Case templates** (pre-filled title/description/tags/tasks/notes) | grep `case_template|CaseTemplate` → 0; only playbook task templates exist |
| **Customers / client organisations on cases** | grep `customer` → 0; `organization` hits are SSO/guest "your organisation" copy and OpenAI org field |
| **Notes with directories/groups, rich-text/markdown notes, note revisions** | no notes table; `timeline_events.content` plain text; no `directory` hits; no rich-text editor dependency |
| **IOC export/import (STIX/MISP/CSV), IOC sharing feeds** | grep `stix|misp` → 0; `csv` only in audit-log export |
| **Report template editor / DOCX export / multiple templates** | single `reports/templates/report.html.j2`; export formats `pdf|json` only |
| **Evidence metadata & chain of custody** (acquisition date, custodian, hashes beyond sha256, evidence numbering) | `case_attachments` fields only; "custody" appears once in a code comment |
| **Case ↔ case links / merge / duplicates** | `CaseLink` is external-system only with no routes; no merge endpoint |
| **Per-case ACL / custom roles / groups for internal users** | roles fixed `admin|analyst|viewer` via memberships; no case ACL table |
| **Task assignee / due date / task comments** | `case_tasks` has `status todo|done`, `completed_by` only |
| **Case custom attributes / classification / outcome fields** | no such columns on `cases`; the `case.attributes` seen in workflow templates (`backend/app/workflows/context.py`, `docs/workflows/README.md`) is a runtime view derived from custom-type artifacts, not stored case metadata |
| **Network/graph tab per case** | `network` tab renders placeholder text; only the global `/graph` page exists |
| **Server-side case search/filter** | `GET /cases/` supports skip/limit only; filtering is client-side |
| **Date picker / Tabs / Select / Dialog UI primitives** | `components/ui/` exports Button, Input, Label, Card, Badge only |
| **vis-timeline / d3 / date libraries** | not in `frontend/package.json` |
