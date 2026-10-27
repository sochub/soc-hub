import type { LucideIcon } from 'lucide-react';
import { Zap, GitBranch, Globe, PenLine, StickyNote, Database, BookText, Search, ArrowUpCircle, XCircle, Repeat, MessageSquare, MessageCircleQuestion, BellPlus, OctagonX, ShieldCheck } from 'lucide-react';
import type { TriggerType } from './types';

export type FieldKind = 'text' | 'textarea' | 'select' | 'json' | 'number' | 'expression';

export interface FieldDef {
    key: string;
    label: string;
    kind: FieldKind;
    options?: string[];
    placeholder?: string;
    help?: string;
    required?: boolean;
}

export interface NodeDef {
    type: string;
    label: string;
    category: 'Trigger' | 'Logic' | 'Cases' | 'Alerts' | 'HTTP' | 'Slack';
    icon: LucideIcon;
    fields: FieldDef[];
    /** node has true/false output handles */
    branching?: boolean;
    alertOnly?: boolean;
    /** renders as a resizable container (for_each) */
    container?: boolean;
    /** has side effects -> simulated in dry-run, mock_output editable */
    sideEffect?: boolean;
}

const CASE_ID: FieldDef = { key: 'case_id', label: 'Case ID (optional)', kind: 'text', placeholder: 'defaults to the run\'s case', help: 'e.g. {{ loop.item.id }}' };
const SEVERITIES = ['', 'critical', 'high', 'medium', 'low', 'info'];
const STATUSES = ['', 'new', 'open', 'in_progress', 'pending', 'resolved', 'closed'];

export const NODE_DEFS: NodeDef[] = [
    { type: 'trigger', label: 'Trigger', category: 'Trigger', icon: Zap, fields: [] },
    {
        type: 'condition', label: 'Condition', category: 'Logic', icon: GitBranch, branching: true,
        fields: [{ key: 'expression', label: 'Expression', kind: 'expression', required: true, placeholder: "'phishing' in case.tags", help: 'Jinja expression, no {{ }}' }],
    },
    {
        type: 'for_each', label: 'For each', category: 'Logic', icon: Repeat, container: true,
        fields: [
            { key: 'items', label: 'Items', kind: 'expression', required: true, placeholder: 'steps.lookup.output.body.users', help: 'Expression that evaluates to a list; drop nodes inside the box to run them per item' },
            { key: 'concurrency', label: 'Concurrency (1-20)', kind: 'number', placeholder: '5' },
            { key: 'max_items', label: 'Max items (≤ 500)', kind: 'number', placeholder: '100' },
        ],
    },
    {
        type: 'slack_ask_user', label: 'Ask user (Slack)', category: 'Slack', icon: MessageCircleQuestion, sideEffect: true,
        fields: [
            { key: 'email', label: 'User email', kind: 'text', required: true, placeholder: '{{ alert.payload.user_email }}' },
            { key: 'message', label: 'Question (mrkdwn)', kind: 'textarea', required: true, placeholder: 'Did you just sign in from {{ alert.payload.country }}?' },
            { key: 'buttons', label: 'Buttons (comma-separated, \u2264 5)', kind: 'text', placeholder: 'Yes, No', help: "Branch on steps.<id>.output.response == 'No' or steps.<id>.output.timed_out" },
            { key: 'timeout_hours', label: 'Timeout (hours, \u2264 168)', kind: 'number', placeholder: '24' },
        ],
    },
    {
        type: 'slack_post_message', label: 'Slack message', category: 'Slack', icon: MessageSquare, sideEffect: true,
        fields: [
            { key: 'channel', label: 'Channel', kind: 'text', placeholder: 'default channel' },
            { key: 'text', label: 'Message (mrkdwn)', kind: 'textarea', required: true, placeholder: ':rotating_light: *{{ case.title }}* ({{ case.severity }})' },
            { key: 'include_case_buttons', label: 'Case buttons', kind: 'select', options: ['no', 'yes'], help: 'Acknowledge / Assign to me / Close' },
            CASE_ID,
        ],
    },
    {
        type: 'http_request', label: 'HTTP request', category: 'HTTP', icon: Globe, sideEffect: true,
        fields: [
            { key: 'method', label: 'Method', kind: 'select', options: ['GET', 'POST', 'PUT', 'PATCH', 'DELETE'], required: true },
            { key: 'url', label: 'URL', kind: 'text', required: true, placeholder: 'https://hooks.example.com/{{ case.id }}' },
            { key: 'headers', label: 'Headers (JSON)', kind: 'json', placeholder: '{"Authorization": "Bearer ..."}' },
            { key: 'body', label: 'Body (JSON or text)', kind: 'json' },
            { key: 'auth', label: 'Auth (JSON, optional)', kind: 'json', placeholder: '{"type": "oauth2_client_credentials", "token_url": "https://login.example.com/oauth2/token", "client_id": "...", "client_secret": "{{ secrets.CLIENT_SECRET }}", "scope": "..."}', help: 'type: oauth2_client_credentials (token_url, client_id, client_secret, scope?), basic (username, password) aws_sigv4 (access_key_id, secret_access_key, session_token?, region, service), google_service_account (service_account_key, scopes, subject?) or oauth2_private_key_jwt (token_url, client_id, private_key, scope, key_id?, dpop?). The secret field must be {{ secrets.NAME }}; tokens, headers and signatures never reach output' },
        ],
    },
    {
        type: 'case_update', label: 'Update case', category: 'Cases', icon: PenLine, sideEffect: true,
        fields: [
            { key: 'severity', label: 'Severity', kind: 'select', options: SEVERITIES },
            { key: 'status', label: 'Status', kind: 'select', options: STATUSES },
            { key: 'owner_email', label: 'Assign to (email)', kind: 'text' },
            { key: 'add_tags', label: 'Add tags', kind: 'text', placeholder: 'escalated, vip' },
            { key: 'remove_tags', label: 'Remove tags', kind: 'text' },
            CASE_ID,
        ],
    },
    { type: 'case_add_note', label: 'Add note', category: 'Cases', icon: StickyNote, sideEffect: true,
      fields: [{ key: 'content', label: 'Note', kind: 'textarea', required: true }, CASE_ID] },
    {
        type: 'case_add_artifact', label: 'Add artifact', category: 'Cases', icon: Database, sideEffect: true,
        fields: [
            { key: 'artifact_type', label: 'Type', kind: 'select', options: ['ip', 'domain', 'url', 'file_hash', 'email', 'hostname', 'username', 'other'], required: true },
            { key: 'value', label: 'Value', kind: 'text', required: true },
            { key: 'description', label: 'Description', kind: 'text' },
            CASE_ID,
        ],
    },
    { type: 'case_apply_playbook', label: 'Apply playbook', category: 'Cases', icon: BookText, sideEffect: true,
      fields: [{ key: 'template_id', label: 'Playbook ID', kind: 'number', required: true }, CASE_ID] },
    {
        type: 'case_search', label: 'Search cases', category: 'Cases', icon: Search,
        fields: [
            { key: 'status', label: 'Status in', kind: 'text', placeholder: 'new, open, in_progress' },
            { key: 'tags_any', label: 'Has any tag', kind: 'text' },
            { key: 'title_contains', label: 'Title contains', kind: 'text' },
            { key: 'artifact_value', label: 'Has artifact value', kind: 'text', placeholder: '{{ alert.payload.src_ip }}' },
            { key: 'created_within_hours', label: 'Created within (hours)', kind: 'number' },
        ],
    },
    {
        type: 'alert_promote', label: 'Promote alert', category: 'Alerts', icon: ArrowUpCircle, alertOnly: true, sideEffect: true,
        fields: [
            { key: 'mode', label: 'Mode', kind: 'select', options: ['new', 'link', 'group'], required: true,
              help: 'new: create a case · link: attach to case_id · group: reuse an open case with the same group key' },
            { key: 'title', label: 'Case title (new/group)', kind: 'text', placeholder: '{{ alert.title }}' },
            { key: 'description', label: 'Description', kind: 'textarea' },
            { key: 'severity', label: 'Severity', kind: 'select', options: SEVERITIES },
            { key: 'tags', label: 'Tags', kind: 'text' },
            { key: 'group_key', label: 'Group key (group)', kind: 'text', placeholder: 'host:{{ alert.payload.host }}' },
            { key: 'window_hours', label: 'Group window (hours)', kind: 'number', placeholder: '24' },
            { key: 'case_id', label: 'Case ID (link)', kind: 'text' },
        ],
    },
    {
        type: 'containment_guard', label: 'Containment guard', category: 'Logic', icon: ShieldCheck, sideEffect: true,
        fields: [
            { key: 'action', label: 'Action', kind: 'text', required: true, placeholder: 'Contain host (CrowdStrike)' },
            { key: 'target_kind', label: 'Target kind', kind: 'select', options: ['host', 'user', 'ip'], required: true },
            { key: 'target', label: 'Target', kind: 'text', required: true, placeholder: "{{ case.attributes.get('hostname', '') }}",
              help: 'Put this before the step that contains. Protected targets and the daily / per-case caps (Settings → Integrations → Containment guardrails) stop the run; allowed actions are listed on the case' },
            { key: 'release_hint', label: 'How to undo', kind: 'text', placeholder: 'Lift containment in the Falcon console' },
        ],
    },
    {
        type: 'fail', label: 'Fail run', category: 'Logic', icon: OctagonX,
        fields: [{ key: 'message', label: 'Message', kind: 'text', required: true, placeholder: 'Pull failed: HTTP {{ steps.pull.output.status }}', help: 'Stops the run as failed, so the next schedule re-reads this window' }],
    },
    { type: 'alert_dismiss', label: 'Dismiss alert', category: 'Alerts', icon: XCircle, alertOnly: true, sideEffect: true,
      fields: [{ key: 'reason', label: 'Reason', kind: 'text' }] },
    {
        type: 'alert_create', label: 'Create alert', category: 'Alerts', icon: BellPlus, sideEffect: true,
        fields: [
            { key: 'source', label: 'Source', kind: 'text', required: true, placeholder: 'okta' },
            { key: 'external_id', label: 'External ID', kind: 'text', required: true, placeholder: '{{ loop.item.uuid }}', help: 'Dedup key: an existing (source, external ID) is not created again' },
            { key: 'title', label: 'Title', kind: 'text', required: true, placeholder: '{{ loop.item.displayMessage }}' },
            { key: 'severity', label: 'Severity', kind: 'text', placeholder: 'medium', help: 'critical, high, medium, low or info; anything else becomes medium' },
            { key: 'payload', label: 'Payload', kind: 'expression', placeholder: 'loop.item', help: 'Expression for an object or list (≤ 256 KB), no {{ }}' },
        ],
    },
];

export const NODE_DEF: Record<string, NodeDef> = Object.fromEntries(NODE_DEFS.map((d) => [d.type, d]));

export const TRIGGER_LABEL: Record<TriggerType, string> = {
    'case.created': 'Case created',
    'case.updated': 'Case updated',
    'alert.ingested': 'Alert ingested',
    manual: 'Manual',
    schedule: 'Schedule (cron, UTC)',
};
