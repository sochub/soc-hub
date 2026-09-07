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

export interface SSOConfig {
  enabled: boolean;
  idp_entity_id: string | null;
  idp_sso_url: string | null;
  idp_x509_cert: string | null;
  auto_provision: boolean;
  default_role: 'analyst' | 'viewer';
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
  created_at: string;
  created_by: number | null;
  case_ids: number[];
  case_count: number;
}

export interface TimelineEventUser {
  id: number;
  email: string;
  full_name: string | null;
}

export interface TimelineEvent {
  id: number;
  case_id: number;
  user_id: number | null;
  event_type: string;
  content: string;
  created_at: string;
  user: TimelineEventUser | null;
}

export type SLAStatus = 'not_tracked' | 'met' | 'on_track' | 'at_risk' | 'breached';

export interface Case {
  id: number;
  title: string;
  description: string;
  severity: 'critical' | 'high' | 'medium' | 'low' | 'info';
  status: string;
  created_at: string;
  updated_at: string | null;
  resolved_at: string | null;
  acknowledged_at: string | null;
  timeline_events: TimelineEvent[];

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
}
