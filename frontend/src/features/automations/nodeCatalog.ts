import type { LucideIcon } from 'lucide-react';
import { Zap, GitBranch, Globe, PenLine, StickyNote, Database, BookText, Search, ArrowUpCircle, XCircle } from 'lucide-react';
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
        type: 'http_request', label: 'HTTP request', category: 'HTTP', icon: Globe, sideEffect: true,
        fields: [
            { key: 'method', label: 'Method', kind: 'select', options: ['GET', 'POST', 'PUT', 'PATCH', 'DELETE'], required: true },
            { key: 'url', label: 'URL', kind: 'text', required: true, placeholder: 'https://hooks.example.com/{{ case.id }}' },
            { key: 'headers', label: 'Headers (JSON)', kind: 'json', placeholder: '{"Authorization": "Bearer ..."}' },
            { key: 'body', label: 'Body (JSON or text)', kind: 'json' },
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
            { key: 'artifact_type', label: 'Type', kind: 'select', options: ['ip', 'domain', 'url', 'file_hash', 'email', 'other'], required: true },
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
    { type: 'alert_dismiss', label: 'Dismiss alert', category: 'Alerts', icon: XCircle, alertOnly: true, sideEffect: true,
      fields: [{ key: 'reason', label: 'Reason', kind: 'text' }] },
];

export const NODE_DEF: Record<string, NodeDef> = Object.fromEntries(NODE_DEFS.map((d) => [d.type, d]));

export const TRIGGER_LABEL: Record<TriggerType, string> = {
    'case.created': 'Case created',
    'case.updated': 'Case updated',
    'alert.ingested': 'Alert ingested',
    manual: 'Manual',
};
