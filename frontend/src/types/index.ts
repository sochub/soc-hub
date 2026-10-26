export interface Tenant {
  id: number;
  name: string;
  slug: string;
  is_active: boolean;
  created_at: string;
  updated_at: string | null;
}

export interface Membership {
  tenant_id: number;
  tenant_name: string;
  tenant_slug: string;
  role: string;
}

export const IR_PHASES = ['identification', 'containment', 'eradication', 'recovery', 'lessons_learned'] as const;
export type IRPhase = typeof IR_PHASES[number];

export interface PlaybookTaskTemplate {
  id?: number;
  phase: string;
  title: string;
  description?: string | null;
  order: number;
}

export interface PlaybookTemplate {
  id: number;
  tenant_id: number | null;
  name: string;
  category: string;
  description?: string | null;
  is_system: boolean;
  source_template_id?: number | null;
  created_at: string;
  status?: 'draft' | 'published';
  tasks?: PlaybookTaskTemplate[];
}

export interface PlaybookSummary {
  id: number;
  tenant_id: number | null;
  name: string;
  category: string;
  description?: string | null;
  is_system: boolean;
  source_template_id?: number | null;
  task_count: number;
  status?: 'draft' | 'published';
  already_imported?: boolean;
}

export type TriageItemStatus = 'proposed' | 'confirmed' | 'dismissed';

export interface CaseTriage {
  id: number;
  case_id: number;
  triggered_by: 'auto_on_create' | 'manual';
  status: 'pending' | 'completed' | 'failed';
  error_message?: string | null;

  proposed_severity?: string | null;
  proposed_tags?: string[] | null;
  severity_tags_status: TriageItemStatus;

  proposed_playbook_template_id?: number | null;
  playbook_status: TriageItemStatus;

  related_case_ids?: number[] | null;

  next_steps_text?: string | null;
  next_steps_status: TriageItemStatus;

  created_at: string;
}

export interface CaseTask {
  id: number;
  case_id: number;
  phase: string;
  title: string;
  description?: string | null;
  status: 'todo' | 'done';
  order: number;
  completed_at?: string | null;
  completed_by?: number | null;
  created_at: string;
}

export interface CaseEvent {
  id: number;
  case_id: number;
  user_id: number | null;
  event_date: string;
  title: string;
  content: string | null;
  raw: string | null;
  source: string | null;
  category: string;
  tags: string[];
  flagged: boolean;
  in_summary: boolean;
  artifacts: { id: number; value: string; type_key: string }[];
  created_at: string | null;
  updated_at: string | null;
}

export type LoginMethod = 'password' | 'saml' | 'oidc';

export interface AuthSessionInfo {
    id: string;
    kind: 'web' | 'oauth';
    current: boolean;
    client_name: string | null;
    tenant_id: number | null;
    tenant_name: string | null;
    amr: string;
    created_at: string;
    last_used_at: string;
    user_agent: string | null;
    ip: string | null;
}

export interface TenantOAuthSession {
    id: string;
    client_name: string | null;
    user_email: string;
    amr: string;
    created_at: string;
    last_used_at: string;
    ip: string | null;
}

export interface SSOConfig {
  member_login_method: LoginMethod;
  guest_login_method: LoginMethod;
  oidc_issuer: string | null;
  oidc_client_id: string | null;
  oidc_scopes: string;
  has_oidc_client_secret: boolean;
  oidc_redirect_uri: string;
  idp_entity_id: string | null;
  idp_sso_url: string | null;
  idp_x509_cert: string | null;
  auto_provision: boolean;
  default_role: 'analyst' | 'viewer';
  saml_allow_idp_initiated: boolean;
  sp_entity_id: string;
  sp_acs_url: string;
  sp_metadata_url: string;
  sp_login_url: string;
}

export type CopilotActionType =
  | 'create_case'
  | 'add_artifact'
  | 'add_timeline_note'
  | 'update_case'
  | 'apply_playbook'
  | 'find_related';

export interface CopilotAction {
  type: CopilotActionType;
  summary: string;
  params: Record<string, unknown>;
  /** Set once the action ran; stored on the message so it survives a reload. */
  result?: ActionResult | null;
}

export interface RelatedCase {
  case_id: number;
  title: string;
  shared_values: string[];
}

export interface ActionResult {
  ok: boolean;
  message: string;
  case_id?: number | null;
  related?: RelatedCase[] | null;
}

export interface User {
  id: number;
  email: string;
  full_name: string | null;
  // For /users/me this is the role in the ACTIVE tenant ('super_admin' for super admins);
  // in tenant user listings it's the user's role in that tenant.
  role: 'super_admin' | 'admin' | 'analyst' | 'viewer' | null;
  is_active: boolean;
  is_super_admin?: boolean;
  active_tenant_id?: number | null;
  memberships?: Membership[];
  job_title?: string | null;
  timezone?: string | null;
  has_avatar?: boolean;
  mfa_enabled?: boolean;
  // Tenant listings: false when the caller can't deactivate/activate/reset MFA on this account
  // (a super admin, or a user who also belongs to other tenants).
  account_manageable?: boolean | null;
  has_password?: boolean;
  mfa_required_by_tenant?: boolean;
  avatar_version?: string | null;
}

export interface Invitation {
  id: number;
  email: string;
  tenant_id: number;
  role: string;
  token: string;
  status: 'pending' | 'accepted' | 'expired' | 'revoked';
  invited_by: number | null;
  created_at: string;
  expires_at: string;
  invite_link: string | null;
}

export interface InvitationValidation {
  email: string;
  tenant_name: string;
  role: string;
  valid: boolean;
}

export interface Artifact {
  id: number;
  tenant_id: number;
  artifact_type: string;
  value: string;
  description: string | null;
  isolated: boolean;
  custom_type_id: number | null;
  created_at: string;
  created_by: number | null;
  case_ids: number[];
  case_count: number;
}

export interface TimelineEventUser {
  id: number;
  email: string;
  full_name: string | null;
  has_avatar?: boolean;
  avatar_version?: string | null;
}

export interface TimelineEvent {
  id: number;
  case_id: number;
  user_id: number | null;
  event_type: string;
  content: string;
  created_at: string;
  user: TimelineEventUser | null;
  shared_with_guests?: boolean;
  guest_id?: number | null;
  guest_email?: string | null;
  via?: string | null;
  via_client?: string | null;
  pinned?: boolean;
}

export type SLAStatus = 'not_tracked' | 'met' | 'on_track' | 'at_risk' | 'breached';

/** PUT /cases/{id} body from the edit modal; `reason` is only sent when status or severity changed. */
export interface CaseEditPayload {
  title: string; description: string; severity: string; status: string; tags: string[]; source: string;
  outcome: string | null; closing_note: string | null; reason?: string;
}

export interface Case {
  id: number;
  title: string;
  description: string;
  severity: 'critical' | 'high' | 'medium' | 'low' | 'info';
  status: string;
  owner_id?: number | null;
  created_at: string;
  updated_at: string | null;
  resolved_at: string | null;
  acknowledged_at: string | null;
  timeline_events: TimelineEvent[];
  guest_summary?: string | null;
  outcome?: string | null;
  closing_note?: string | null;
  milestones?: ReportMilestones | null;
  slack_channel_id?: string | null;
  slack_channel_name?: string | null;
  slack_listen?: boolean;
  war_room_url?: string | null;
  war_room_silent?: boolean;

  sla_response_target_minutes?: number | null;
  sla_response_status?: SLAStatus | null;
  sla_response_due_at?: string | null;
  sla_resolution_target_minutes?: number | null;
  sla_resolution_status?: SLAStatus | null;
  sla_resolution_due_at?: string | null;
  sla_overall_status?: SLAStatus | null;
}

export interface SLAPolicyItem {
  severity: string;
  response_target_minutes: number | null;
  resolution_target_minutes: number | null;
}

export interface IOC {
  id: number;
  tenant_id: number;
  case_id: number | null;
  ioc_type: string;
  value: string;
  threat_level: string;
  confidence: number;
  status: string;
  tlp: string;
  first_seen: string | null;
  last_seen: string | null;
  source: string | null;
  tags: string[];
  description: string | null;
  created_at: string;
  created_by: number | null;
  enrichment_verdict?: string | null;
}

export type EnrichmentResultRow = {
  source: 'virustotal' | 'urlhaus' | 'threatfox' | 'rdap' | 'crtsh';
  status: 'pending' | 'ok' | 'not_found' | 'error' | 'rate_limited' | 'skipped';
  verdict: 'malicious' | 'suspicious' | 'harmless' | 'unknown' | null;
  score: string | null; summary: Record<string, unknown>; link: string | null;
  error: string | null; fetched_at: string | null; stalled: boolean;
};
export type EnrichmentView = {
  enrichable: boolean; indicator_type: string | null; indicator_value: string | null;
  effective_tlp: string | null; auto: boolean; results: EnrichmentResultRow[];
  suggestion: { threat_level: string | null; tags: string[] } | null;
};

export interface Attachment {
  id: number;
  case_id: number;
  filename: string;
  content_type: string | null;
  size_bytes: number;
  sha256: string;
  is_malicious: boolean;
  description: string | null;
  uploaded_by: number | null;
  uploaded_by_email: string | null;
  uploaded_by_guest_email?: string | null;
  created_at: string;
  deleted_at: string | null;
  deleted_by: number | null;
  deleted_by_email: string | null;
}

export interface AttachmentList {
  max_upload_mb: number;
  items: Attachment[];
}

export type TLPLevel = 'white' | 'green' | 'amber' | 'red';
export type MilestoneKey = 'first_seen' | 'detected' | 'contained' | 'recovered';

export interface ReportMilestone {
  at: string | null;
  source: 'declared' | 'computed' | 'unknown';
}

export type ReportMilestones = Record<MilestoneKey, ReportMilestone> & {
  ttd_seconds: number | null;
  ttc_seconds: number | null;
  ttr_seconds: number | null;
  out_of_order: string[];
};

export interface CaseReportFields {
  executive_summary: string | null;
  impact: string | null;
  lessons_learned: string | null;
  tlp: TLPLevel;
  updated_by_email: string | null;
  updated_at: string | null;
}

export interface CaseReportView {
  report: CaseReportFields;
  milestones: ReportMilestones;
  min_tlp: TLPLevel;
  ai_available: boolean;
}

export interface ReportDraft {
  executive_summary: string;
  impact: string;
  lessons_learned: string;
}

export interface SecretName {
  name: string;
  description: string | null;
  allowed_hosts: string[];
}

export interface Secret extends SecretName {
  last_used_at: string | null;
  created_at: string | null;
  updated_at: string | null;
  created_by_email: string | null;
  updated_by_email: string | null;
  in_use_by: { id: number; name: string }[];
}

export interface SecretScanItem {
  workflow_id: number;
  workflow_name: string;
  node_id: string;
  location: 'header' | 'query';
  key: string;
  host: string;
  suggested_name: string;
  /** false when the host can't be a secret host pattern; `reason` says what to do */
  convertible: boolean;
  reason?: string;
}

export type NotificationType = 'mention' | 'assigned' | 'comment' | 'status_change' | 'severity_change' | 'sla_breach';

export interface AppNotification {
  id: number;
  type: NotificationType | string;
  summary: string;
  case_id: number;
  case_title: string | null;
  actor_name: string | null;
  timeline_event_id: number | null;
  read_at: string | null;
  created_at: string;
}

export interface AuditUser { id: number; email: string; full_name?: string | null }
export interface AuditItem {
    id: number; created_at: string; category: 'change' | 'write' | 'security' | 'read'; action: string;
    outcome?: 'success' | 'denied' | 'error' | null; status_code?: number | null; method?: string | null;
    route?: string | null; entity_type?: string | null; entity_id?: number | null; user?: AuditUser | null;
    user_email?: string | null; actor?: string | null; ip?: string | null; user_agent?: string | null;
    request_id?: string | null; duration_ms?: number | null; changes?: Record<string, unknown> | null;
}
export interface AuditPage { items: AuditItem[]; next_before: number | null }
export interface AuditFacets { categories: string[]; actions: string[]; users: AuditUser[]; entity_types: string[] }

export interface CaseGuest {
  id: number;
  case_id: number;
  email: string;
  permission: 'viewer' | 'contributor';
  expires_at: string;
  revoked_at: string | null;
  last_seen_at: string | null;
  created_at: string | null;
  has_account: boolean;
  active: boolean;
}

export interface GuestSettings {
  domain_allowlist: string[];
  login_method: 'password' | 'saml' | 'oidc';
  sso_ready: boolean;
  saml_ready: boolean;
  oidc_ready: boolean;
}

export interface CaseAsset {
    id: number; case_id: number; name: string; asset_type: string; ip?: string | null; domain?: string | null;
    description?: string | null; compromise_status: string; tags: string[]; created_at: string; updated_at?: string | null;
}

export interface WarRoomSuggestion {
  id: number;
  kind: 'artifact' | 'milestone';
  payload: { type?: string; value?: string; key?: string; at?: string };
  status: string;
  permalink: string | null;
  created_at: string;
}
