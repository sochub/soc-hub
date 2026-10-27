import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useSearchParams } from 'react-router-dom';
import { Plus, Trash2, X, Search } from 'lucide-react';
import { api } from '../../api/client';
import { cn } from '../../lib/utils';
import PageContainer from '../../components/layout/PageContainer';
import type { User } from '../../types';

type Condition = { field: string; op: string; value: string };
type Rule = {
    id: number; name: string; reason: string | null; conditions: Condition[]; enabled: boolean;
    expires_at: string | null; hit_count: number; last_hit_at: string | null; created_from_alert_id: number | null;
};
type Preview = { scanned: number; matched: number; sample: { id: number; title: string; source: string; status: string }[] };

const OPS: [string, string][] = [
    ['equals', 'equals'], ['not_equals', 'is not'], ['contains', 'contains'],
    ['starts_with', 'starts with'], ['ends_with', 'ends with'], ['in_cidr', 'in CIDR'],
];
const BASE_FIELDS = ['source', 'title', 'severity'];
const EXPIRY: [string, number | null][] = [['7 days', 7], ['30 days', 30], ['90 days', 90], ['Never', null]];
const field = 'bg-panel border border-line rounded-lg px-2.5 py-1.5 text-sm text-fg focus:outline-hidden focus:ring-1 focus:ring-white/50';
const apiError = (err: unknown, fallback: string) => {
    const d = (err as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
    return typeof d === 'string' ? d : fallback;
};
const describe = (c: Condition) => `${c.field} ${OPS.find(([k]) => k === c.op)?.[1] ?? c.op} "${c.value}"`;
const expired = (r: Rule) => !!r.expires_at && new Date(r.expires_at) < new Date();

/** Flatten an alert payload into payload.<path> = value pairs (scalars only) for the field picker. */
function payloadPaths(obj: unknown, prefix = 'payload', out: [string, string][] = []): [string, string][] {
    if (out.length >= 40 || obj === null || obj === undefined) return out;
    if (typeof obj !== 'object') { out.push([prefix, String(obj)]); return out; }
    for (const [k, v] of Object.entries(obj as Record<string, unknown>)) {
        if (/^[A-Za-z0-9_\-@$]{1,64}$/.test(k)) payloadPaths(v, `${prefix}.${k}`, out);
    }
    return out;
}

function RuleForm({ fromAlert, onDone }: { fromAlert: { id: number; source: string; title: string; payload: unknown } | null; onDone: () => void }) {
    const qc = useQueryClient();
    const [name, setName] = useState(fromAlert ? `${fromAlert.source}: ${fromAlert.title}`.slice(0, 200) : '');
    const [reason, setReason] = useState('');
    const [days, setDays] = useState<number | null>(30);
    const [conds, setConds] = useState<Condition[]>(fromAlert
        ? [{ field: 'source', op: 'equals', value: fromAlert.source }, { field: 'title', op: 'equals', value: fromAlert.title }]
        : [{ field: 'title', op: 'contains', value: '' }]);
    const paths = fromAlert ? payloadPaths(fromAlert.payload) : [];
    const valid = conds.length > 0 && conds.every((c) => c.value.trim() && /^(source|title|severity|payload(\.[A-Za-z0-9_\-@$]{1,64}){1,8})$/.test(c.field));

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
        <div className="glass-panel p-4 rounded-xl border border-line bg-panel space-y-3">
            <div className="flex items-center justify-between">
                <h3 className="text-sm font-semibold text-fg">New suppression rule{fromAlert && <span className="font-normal text-muted"> · from alert #{fromAlert.id}</span>}</h3>
                <button onClick={onDone} className="text-muted hover:text-fg" aria-label="Close"><X size={16} /></button>
            </div>
            <p className="text-xs text-fg-3">New alerts that match <strong>every</strong> condition are stored as dismissed and start no workflows. Matching ignores case.</p>
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
                <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Name" aria-label="Name" className={cn(field, 'sm:col-span-2')} />
                <select value={days ?? ''} onChange={(e) => setDays(e.target.value ? Number(e.target.value) : null)} aria-label="Expires after" className={field}>
                    {EXPIRY.map(([label, d]) => <option key={label} value={d ?? ''}>Expires: {label}</option>)}
                </select>
                <input value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Why is this noise? (e.g. VPN egress, approved scanner)" aria-label="Reason" className={cn(field, 'sm:col-span-3')} />
            </div>
            <datalist id="suppression-fields">
                {BASE_FIELDS.map((f) => <option key={f} value={f} />)}
                {paths.map(([p]) => <option key={p} value={p} />)}
            </datalist>
            {conds.map((c, i) => (
                <div key={i} className="flex flex-wrap items-center gap-2">
                    <input list="suppression-fields" value={c.field} aria-label="Field" placeholder="source, title, severity or payload.path"
                        onChange={(e) => {
                            const p = paths.find(([k]) => k === e.target.value);
                            set(i, { field: e.target.value, ...(p && !c.value ? { value: p[1] } : {}) });
                        }}
                        className={cn(field, 'font-mono text-xs w-56')} />
                    <select value={c.op} onChange={(e) => set(i, { op: e.target.value })} aria-label="Operator" className={field}>
                        {OPS.map(([k, label]) => <option key={k} value={k}>{label}</option>)}
                    </select>
                    <input value={c.value} onChange={(e) => set(i, { value: e.target.value })} aria-label="Value" placeholder={c.op === 'in_cidr' ? '10.0.0.0/8' : 'value'}
                        className={cn(field, 'font-mono text-xs flex-1 min-w-40')} />
                    <button onClick={() => { setConds(conds.filter((_, j) => j !== i)); preview.reset(); }} disabled={conds.length === 1}
                        className="text-muted hover:text-severity-critical disabled:opacity-30" aria-label="Remove condition"><X size={14} /></button>
                </div>
            ))}
            {conds.length < 10 && (
                <button onClick={() => setConds([...conds, { field: 'title', op: 'contains', value: '' }])} className="text-xs text-accent hover:underline">+ Add condition</button>
            )}
            {preview.data && (
                <div className="text-xs rounded-lg border border-line bg-raised/40 p-2.5 space-y-1">
                    <p className={preview.data.matched > preview.data.scanned / 2 ? 'text-severity-high' : 'text-fg-2'}>
                        Would have suppressed <strong>{preview.data.matched}</strong> of the last {preview.data.scanned} alerts.
                        {preview.data.matched > preview.data.scanned / 2 && ' That is most of your alerts: check the conditions are narrow enough.'}
                    </p>
                    {preview.data.sample.map((a) => <p key={a.id} className="font-mono text-fg-3 truncate">#{a.id} · {a.source} · {a.title}</p>)}
                </div>
            )}
            {(save.isError || preview.isError) && <p role="alert" className="text-xs text-severity-critical">{apiError(save.error ?? preview.error, 'Check the conditions.')}</p>}
            <div className="flex justify-end gap-2">
                <button onClick={() => preview.mutate()} disabled={!valid || preview.isPending} className="px-3 py-1.5 text-xs bg-raised text-fg-2 rounded-sm hover:bg-raised-2 disabled:opacity-50 flex items-center gap-1.5">
                    <Search size={12} /> Preview
                </button>
                <button onClick={() => save.mutate()} disabled={!valid || !name.trim() || save.isPending}
                    className="px-3 py-1.5 text-xs bg-accent text-on-accent rounded-sm font-medium hover:bg-accent-hover disabled:opacity-50">
                    {save.isPending ? 'Saving…' : 'Save rule'}
                </button>
            </div>
        </div>
    );
}

export default function SuppressionsList() {
    const qc = useQueryClient();
    const [params, setParams] = useSearchParams();
    const fromId = Number(params.get('from_alert')) || null;
    const [creating, setCreating] = useState(false);
    const { data: me } = useQuery({ queryKey: ['currentUser'], queryFn: async () => (await api.get('/users/me')).data as User, staleTime: 300_000 });
    const canEdit = me?.role === 'admin' || me?.role === 'analyst' || me?.is_super_admin;
    const { data: rules = [], isLoading } = useQuery({ queryKey: ['suppressions'], queryFn: async () => (await api.get('/suppressions/')).data as Rule[] });
    const { data: fromAlert } = useQuery({
        queryKey: ['alert', fromId], enabled: !!fromId,
        queryFn: async () => (await api.get(`/alerts/${fromId}`)).data as { id: number; source: string; title: string; payload: unknown },
    });
    const formOpen = creating || (!!fromId && !!fromAlert); // ?from_alert=<id> opens it pre-filled

    const refresh = () => qc.invalidateQueries({ queryKey: ['suppressions'] });
    const toggle = useMutation({ mutationFn: async (r: Rule) => api.put(`/suppressions/${r.id}`, { enabled: !r.enabled }), onSuccess: refresh });
    const remove = useMutation({ mutationFn: async (id: number) => api.delete(`/suppressions/${id}`), onSuccess: refresh });
    const close = () => { setCreating(false); if (fromId) setParams({}); };

    return (
        <PageContainer className="space-y-4">
            <div className="flex items-center justify-between gap-4">
                <div>
                    <h1 className="text-2xl font-bold text-fg tracking-tight">Suppressions</h1>
                    <p className="text-fg-3 text-sm mt-0.5">Known noise is auto-dismissed at ingest and kept for the record. A rule that has suppressed alerts can only be disabled.</p>
                </div>
                {canEdit && !formOpen && (
                    <button onClick={() => setCreating(true)} className="text-xs bg-accent text-on-accent hover:bg-accent-hover px-3 py-1.5 rounded-sm flex items-center gap-1.5 font-medium shrink-0">
                        <Plus size={12} /> New rule
                    </button>
                )}
            </div>
            {formOpen && <RuleForm key={fromAlert?.id ?? 'new'} fromAlert={fromAlert ?? null} onDone={close} />}
            {(toggle.isError || remove.isError) && <p role="alert" className="text-xs text-severity-critical">{apiError(toggle.error ?? remove.error, 'Could not change the rule.')}</p>}
            {isLoading ? <p className="font-mono text-xs text-muted">$ loading suppressions…</p> : !rules.length ? (
                <p className="text-sm text-muted py-8 text-center">No suppression rules yet. Use “Suppress similar” on an alert you dismissed as noise.</p>
            ) : (
                <div className="space-y-2">
                    {rules.map((r) => (
                        <article key={r.id} className={cn('rounded-lg border border-line bg-panel p-3', (!r.enabled || expired(r)) && 'opacity-60')}>
                            <div className="flex flex-wrap items-center gap-2">
                                <span className="text-sm font-medium text-fg">{r.name}</span>
                                {!r.enabled && <span className="text-[10px] px-1.5 py-0.5 rounded-sm border border-line text-muted">Disabled</span>}
                                {expired(r) && <span className="text-[10px] px-1.5 py-0.5 rounded-sm border border-line text-muted">Expired</span>}
                                <span className="text-xs font-mono text-fg-3">{r.hit_count} suppressed{r.last_hit_at && ` · last ${new Date(r.last_hit_at).toLocaleString()}`}</span>
                                <span className="text-xs text-muted">{r.expires_at ? `expires ${new Date(r.expires_at).toLocaleDateString()}` : 'never expires'}</span>
                                {canEdit && (
                                    <span className="ml-auto flex items-center gap-3">
                                        <label className="flex items-center gap-1.5 text-xs text-fg-2">
                                            <input type="checkbox" checked={r.enabled} onChange={() => toggle.mutate(r)} /> Enabled
                                        </label>
                                        <button onClick={() => { if (confirm('Delete this rule?')) remove.mutate(r.id); }} disabled={r.hit_count > 0}
                                            title={r.hit_count > 0 ? 'It has suppressed alerts: disable it instead' : 'Delete'}
                                            className="text-muted hover:text-severity-critical disabled:opacity-30"><Trash2 size={13} /></button>
                                    </span>
                                )}
                            </div>
                            <p className="mt-1 text-xs font-mono text-fg-2">{r.conditions.map(describe).join('  AND  ')}</p>
                            {r.reason && <p className="mt-1 text-xs text-fg-3">{r.reason}</p>}
                        </article>
                    ))}
                </div>
            )}
        </PageContainer>
    );
}
