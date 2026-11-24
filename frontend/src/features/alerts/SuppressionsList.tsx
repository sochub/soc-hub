import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useSearchParams } from 'react-router-dom';
import { Plus, Trash2, X, Search } from 'lucide-react';
import { api } from '../../api/client';
import { cn } from '../../lib/utils';
import { Button, Input, Select, Skeleton, ErrorState, EmptyState, Badge, fieldClass, useConfirm } from '../../components/ui';
import type { User } from '../../types';
import { BASE_FIELDS, FIELD_RE, OPS, describe, payloadPaths, type Condition } from './conditions';
import FieldPicker from './FieldPicker';

type Rule = {
    id: number; name: string; reason: string | null; conditions: Condition[]; enabled: boolean;
    expires_at: string | null; hit_count: number; last_hit_at: string | null; created_from_alert_id: number | null;
};
type Preview = { scanned: number; matched: number; sample: { id: number; title: string; source: string; status: string }[] };

const EXPIRY: [string, number | null][] = [['7 days', 7], ['30 days', 30], ['90 days', 90], ['Never', null]];
const field = fieldClass();
const apiError = (err: unknown, fallback: string) => {
    const d = (err as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
    return typeof d === 'string' ? d : fallback;
};
const expired = (r: Rule) => !!r.expires_at && new Date(r.expires_at) < new Date();


function RuleForm({ fromAlert, onDone }: { fromAlert: { id: number; source: string; title: string; payload: unknown } | null; onDone: () => void }) {
    const qc = useQueryClient();
    const [name, setName] = useState(fromAlert ? `${fromAlert.source}: ${fromAlert.title}`.slice(0, 200) : '');
    const [reason, setReason] = useState('');
    const [days, setDays] = useState<number | null>(30);
    const [conds, setConds] = useState<Condition[]>(fromAlert
        ? [{ field: 'source', op: 'equals', value: fromAlert.source }, { field: 'title', op: 'equals', value: fromAlert.title }]
        : [{ field: 'title', op: 'contains', value: '' }]);
    // No source alert: suggest payload paths seen in recent alerts (#130), without pre-filling values.
    const { data: recent = [] } = useQuery({
        queryKey: ['alerts', 'recent-payloads'], enabled: !fromAlert, staleTime: 60_000,
        queryFn: async () => (await api.get('/alerts/', { params: { limit: 20 } })).data as { payload: unknown }[],
    });
    const paths = fromAlert ? payloadPaths(fromAlert.payload)
        : [...new Set(recent.flatMap((a) => payloadPaths(a.payload).map(([p]) => p)))].slice(0, 80).map((p): [string, string] => [p, '']);
    const groups: [string, string[]][] = [['Alert fields', BASE_FIELDS],
        ...(paths.length ? [[fromAlert ? `Payload of alert #${fromAlert.id}` : 'Payload (recent alerts)', paths.map(([p]) => p)] as [string, string[]]] : [])];
    const valid = conds.length > 0 && conds.every((c) => c.value.trim() && FIELD_RE.test(c.field));

    const preview = useMutation({ mutationFn: async () => (await api.post('/suppressions/preview', { conditions: conds })).data as Preview });
    const save = useMutation({
        mutationFn: async () => api.post('/suppressions/', {
            name, reason: reason || null, conditions: conds, created_from_alert_id: fromAlert?.id ?? null,
            expires_at: days ? new Date(Date.now() + days * 86_400_000).toISOString() : null,
        }),
        onSuccess: () => { qc.invalidateQueries({ queryKey: ['suppressions'] }); onDone(); },
    });
    const set = (i: number, patch: Partial<Condition>) => { setConds(conds.map((c, j) => (j === i ? { ...c, ...patch } : c))); preview.reset(); };

    return (
        <div className="p-4 rounded-md border border-line bg-panel space-y-3">
            <div className="flex items-center justify-between">
                <h3 className="text-sm font-semibold text-fg">New suppression rule{fromAlert && <span className="font-normal text-muted"> · from alert #{fromAlert.id}</span>}</h3>
                <Button variant="ghost" size="icon" onClick={onDone} aria-label="Close"><X size={16} /></Button>
            </div>
            <p className="text-xs text-fg-3">New alerts that match <strong>every</strong> condition are stored as dismissed and start no workflows. Matching ignores case.</p>
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
                <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="Name" aria-label="Name" className="sm:col-span-2" />
                <Select value={days ?? ''} onChange={(e) => setDays(e.target.value ? Number(e.target.value) : null)} aria-label="Expires after">
                    {EXPIRY.map(([label, d]) => <option key={label} value={d ?? ''}>Expires: {label}</option>)}
                </Select>
                <Input value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Why is this noise? (e.g. VPN egress, approved scanner)" aria-label="Reason" className="sm:col-span-3" />
            </div>
            <p className="text-xs text-muted">
                Fields: <code className="font-mono">source</code>, <code className="font-mono">title</code>, <code className="font-mono">severity</code>, or any payload path such as <code className="font-mono">payload.agent.name</code>.
                {fromAlert ? ` Suggestions come from alert #${fromAlert.id}.` : paths.length ? ` Suggestions come from the ${recent.length} most recent alerts.` : ''}
            </p>
            {conds.map((c, i) => (
                // Fixed columns so every condition row lines up, whatever field is picked.
                <div key={i} className="grid grid-cols-1 sm:grid-cols-[16rem_9rem_minmax(0,1fr)_1.5rem] items-center gap-2">
                    <FieldPicker value={c.field} groups={groups} onChange={(v) => {
                        const p = paths.find(([k]) => k === v);
                        set(i, { field: v, ...(p?.[1] && !c.value ? { value: p[1] } : {}) });
                    }} />
                    <select value={c.op} onChange={(e) => set(i, { op: e.target.value })} aria-label="Operator" className={cn(field, 'text-xs w-full')}>
                        {OPS.map(([k, label]) => <option key={k} value={k}>{label}</option>)}
                    </select>
                    <input value={c.value} onChange={(e) => set(i, { value: e.target.value })} aria-label="Value" placeholder={c.op === 'in_cidr' ? '10.0.0.0/8' : 'value'}
                        className={cn(field, 'font-mono text-xs w-full')} />
                    <button onClick={() => { setConds(conds.filter((_, j) => j !== i)); preview.reset(); }} disabled={conds.length === 1}
                        className="justify-self-center text-muted hover:text-severity-critical disabled:opacity-30" aria-label="Remove condition"><X size={14} /></button>
                </div>
            ))}
            {conds.length < 10 && (
                <button onClick={() => setConds([...conds, { field: 'title', op: 'contains', value: '' }])} className="text-xs text-accent hover:underline">+ Add condition</button>
            )}
            {preview.data && (
                <div className="text-xs rounded-md border border-line bg-raised p-2.5 space-y-1">
                    <p className={preview.data.matched > preview.data.scanned / 2 ? 'text-severity-high' : 'text-fg-2'}>
                        Would have suppressed <strong>{preview.data.matched}</strong> of the last {preview.data.scanned} alerts.
                        {preview.data.matched > preview.data.scanned / 2 && ' That is most of your alerts: check the conditions are narrow enough.'}
                    </p>
                    {preview.data.sample.map((a) => <p key={a.id} className="font-mono text-fg-3 truncate">#{a.id} · {a.source} · {a.title}</p>)}
                </div>
            )}
            {(save.isError || preview.isError) && <p role="alert" className="text-xs text-severity-critical">{apiError(save.error ?? preview.error, 'Check the conditions.')}</p>}
            <div className="flex justify-end gap-2">
                <Button size="sm" variant="secondary" onClick={() => preview.mutate()} disabled={!valid || preview.isPending}>
                    <Search size={12} aria-hidden /> Preview
                </Button>
                <Button size="sm" onClick={() => save.mutate()} disabled={!valid || !name.trim() || save.isPending}>
                    {save.isPending ? 'Saving…' : 'Save rule'}
                </Button>
            </div>
        </div>
    );
}

export default function SuppressionsList() {
    const qc = useQueryClient();
    const [params, setParams] = useSearchParams();
    const fromId = Number(params.get('from_alert')) || null;
    const [creating, setCreating] = useState(false);
    const [showInactive, setShowInactive] = useState(false);
    const { data: me } = useQuery({ queryKey: ['currentUser'], queryFn: async () => (await api.get('/users/me')).data as User, staleTime: 300_000 });
    const canEdit = me?.role === 'admin' || me?.role === 'analyst' || me?.is_super_admin;
    const { confirm, dialog } = useConfirm();
    const { data: rules = [], isLoading, isError, refetch } = useQuery({ queryKey: ['suppressions'], queryFn: async () => (await api.get('/suppressions/')).data as Rule[] });
    const { data: fromAlert } = useQuery({
        queryKey: ['alert', fromId], enabled: !!fromId,
        queryFn: async () => (await api.get(`/alerts/${fromId}`)).data as { id: number; source: string; title: string; payload: unknown },
    });
    const inactive = rules.filter((r) => !r.enabled || expired(r)).length;
    const shown = showInactive ? rules : rules.filter((r) => r.enabled && !expired(r));
    const formOpen = creating || (!!fromId && !!fromAlert); // ?from_alert=<id> opens it pre-filled

    const refresh = () => qc.invalidateQueries({ queryKey: ['suppressions'] });
    const toggle = useMutation({ mutationFn: async (r: Rule) => api.put(`/suppressions/${r.id}`, { enabled: !r.enabled }), onSuccess: refresh });
    const remove = useMutation({ mutationFn: async (id: number) => api.delete(`/suppressions/${id}`), onSuccess: refresh });
    const close = () => { setCreating(false); if (fromId) setParams({}); };

    return (
        <div className="p-4 space-y-4">
            <div className="flex flex-wrap items-center gap-3">
                <p className="mr-auto text-sm text-fg-3 num">{isLoading ? '\u00a0' : `${shown.length} ${shown.length === 1 ? 'rule' : 'rules'}`}</p>
                {inactive > 0 && (
                    <label className="flex items-center gap-1.5 text-xs text-fg-2">
                        <input type="checkbox" checked={showInactive} onChange={(e) => setShowInactive(e.target.checked)} /> Show disabled and expired rules <span className="num">({inactive})</span>
                    </label>
                )}
                {canEdit && !formOpen && (
                    <Button size="sm" onClick={() => setCreating(true)}><Plus size={12} aria-hidden /> New rule</Button>
                )}
            </div>
            <p className="text-sm text-fg-3 max-w-[80ch]">Known noise is auto-dismissed at ingest and kept for the record. A rule that has suppressed alerts can only be disabled; disabled rules are hidden unless you choose to show them.</p>
            {formOpen && <RuleForm key={fromAlert?.id ?? 'new'} fromAlert={fromAlert ?? null} onDone={close} />}
            {(toggle.isError || remove.isError) && <p role="alert" className="text-xs text-severity-critical">{apiError(toggle.error ?? remove.error, 'Could not change the rule.')}</p>}
            {isError ? <ErrorState message="Could not load suppression rules." onRetry={() => refetch()} /> : isLoading ? <Skeleton rows={4} /> : !rules.length ? (
                <EmptyState title="No suppression rules yet" body="Use “Suppress similar” on an alert you dismissed as noise." />
            ) : (
                <div className="space-y-2">
                    {!shown.length && <EmptyState title="No active rules" />}
                    {shown.map((r) => (
                        <article key={r.id} className={cn('rounded-md border border-line bg-panel p-3', (!r.enabled || expired(r)) && 'opacity-60')}>
                            <div className="flex flex-wrap items-center gap-2">
                                <span className="text-sm font-medium text-fg">{r.name}</span>
                                {!r.enabled && <Badge>Disabled</Badge>}
                                {expired(r) && <Badge>Expired</Badge>}
                                <span className="text-xs text-fg-3 num">{r.hit_count} suppressed{r.last_hit_at && ` · last ${new Date(r.last_hit_at).toLocaleString()}`}</span>
                                <span className="text-xs text-muted">{r.expires_at ? `expires ${new Date(r.expires_at).toLocaleDateString()}` : 'never expires'}</span>
                                {canEdit && (
                                    <span className="ml-auto flex items-center gap-3">
                                        <label className="flex items-center gap-1.5 text-xs text-fg-2">
                                            <input type="checkbox" checked={r.enabled} onChange={() => toggle.mutate(r)} /> Enabled
                                        </label>
                                        {/* aria-disabled, not disabled: disabled buttons swallow hover, so the reason tooltip never shows */}
                                        <button onClick={async () => { if (r.hit_count === 0 && await confirm({ title: 'Delete this rule?', danger: true, confirmLabel: 'Delete rule' })) remove.mutate(r.id); }} aria-disabled={r.hit_count > 0}
                                            title={r.hit_count > 0 ? 'Can’t delete: this rule has suppressed alerts and is kept for their history. Disable it instead; disabled rules are hidden from this list.' : 'Delete'}
                                            aria-label={r.hit_count > 0 ? 'Delete unavailable: rule has suppressed alerts' : 'Delete rule'}
                                            className={cn('text-muted', r.hit_count > 0 ? 'opacity-30 cursor-not-allowed' : 'hover:text-severity-critical')}><Trash2 size={13} /></button>
                                    </span>
                                )}
                            </div>
                            <p className="mt-1 text-xs font-mono text-fg-2">{r.conditions.map(describe).join('  AND  ')}</p>
                            {r.reason && <p className="mt-1 text-xs text-fg-3">{r.reason}</p>}
                        </article>
                    ))}
                </div>
            )}
            {dialog}
        </div>
    );
}
