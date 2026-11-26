import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Plus, Pencil, Play, Trash2, X, Search } from 'lucide-react';
import { api } from '../../api/client';
import { cn } from '../../lib/utils';
import { Button, Input, Skeleton, ErrorState, EmptyState, Badge, fieldClass, useConfirm } from '../../components/ui';
import type { User } from '../../types';
import { BASE_FIELDS, FIELD_RE, OPS, describe, type Condition } from './conditions';
import FieldPicker from './FieldPicker';

type Policy = {
    id: number; name: string; retain_days: number; statuses: string[]; conditions: Condition[]; enabled: boolean;
    last_run_at: string | null; last_deleted: number; total_deleted: number;
    last_run_trigger: 'scheduled' | 'manual' | null; last_error: string | null;
};
type Scope = Pick<Policy, 'retain_days' | 'statuses' | 'conditions'>;
type Preview = {
    matched: number; kept_linked_to_case: number; oldest: string | null; cutoff: string;
    sample: { id: number; title: string; source: string; status: string; created_at: string }[];
};

const STATUSES: [string, string][] = [['dismissed', 'Dismissed'], ['pending', 'Pending (never triaged)']];
const field = fieldClass();
const apiError = (err: unknown, fallback: string) => {
    const d = (err as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
    return typeof d === 'string' ? d : fallback;
};
const summary = (p: Scope) => `Deletes ${p.statuses.join(' and ')} alerts older than ${p.retain_days} days`;
const lastRun = (p: Policy) => !p.last_run_at ? 'Never run'
    : `Last run ${new Date(p.last_run_at).toLocaleString()} (${p.last_run_trigger === 'manual' ? 'run now' : 'scheduled'}): ${p.last_error ? 'failed' : `deleted ${p.last_deleted}`}`;
const fetchPreview = async (s: Scope) => (await api.post('/alert-retention/preview', s)).data as Preview;

function PreviewBox({ p }: { p: Preview }) {
    return (
        <div className="text-xs rounded-md border border-line bg-raised p-2.5 space-y-1">
            <p className="text-fg-2">
                Would delete <strong className="num">{p.matched}</strong> {p.matched === 1 ? 'alert' : 'alerts'} received before {new Date(p.cutoff).toLocaleDateString()}
                {p.oldest && <> (oldest {new Date(p.oldest).toLocaleDateString()})</>}.
                {p.kept_linked_to_case > 0 && <> <span className="num">{p.kept_linked_to_case}</span> more match but are kept because a case holds them.</>}
            </p>
            {p.sample.map((a) => (
                <p key={a.id} className="font-mono text-fg-3 truncate">#{a.id} · {new Date(a.created_at).toLocaleDateString()} · {a.status} · {a.source} · {a.title}</p>
            ))}
        </div>
    );
}

function PolicyForm({ policy, onDone }: { policy: Policy | null; onDone: () => void }) {
    const qc = useQueryClient();
    const [name, setName] = useState(policy?.name ?? 'Dismissed alerts after 90 days');
    const [days, setDays] = useState(policy?.retain_days ?? 90);
    const [statuses, setStatuses] = useState<string[]>(policy?.statuses ?? ['dismissed']);
    const [conds, setConds] = useState<Condition[]>(policy?.conditions ?? []);
    const { data: fields = [] } = useQuery({
        queryKey: ['alerts', 'fields'], staleTime: 60_000,
        queryFn: async () => (await api.get('/alerts/fields')).data as { field: string }[],
    });
    const groups: [string, string[]][] = [['Alert fields', BASE_FIELDS], ...(fields.length ? [['Payload (recent alerts)', fields.map((f) => f.field)] as [string, string[]]] : [])];
    const scope: Scope = { retain_days: days, statuses, conditions: conds };
    const valid = days >= 1 && days <= 3650 && statuses.length > 0 && conds.every((c) => c.value.trim() && FIELD_RE.test(c.field));

    const preview = useMutation({ mutationFn: () => fetchPreview(scope) });
    const save = useMutation({
        mutationFn: async () => policy ? api.put(`/alert-retention/${policy.id}`, { name, ...scope }) : api.post('/alert-retention/', { name, ...scope }),
        onSuccess: () => { qc.invalidateQueries({ queryKey: ['alert-retention'] }); onDone(); },
    });
    const change = (fn: () => void) => { fn(); preview.reset(); };
    const set = (i: number, patch: Partial<Condition>) => change(() => setConds(conds.map((c, j) => (j === i ? { ...c, ...patch } : c))));

    return (
        <div className="p-4 rounded-md border border-line bg-panel space-y-3">
            <div className="flex items-center justify-between">
                <h3 className="text-sm font-semibold text-fg">{policy ? 'Edit retention policy' : 'New retention policy'}</h3>
                <Button variant="ghost" size="icon" onClick={onDone} aria-label="Close"><X size={16} /></Button>
            </div>
            <div className="grid grid-cols-1 sm:grid-cols-[minmax(0,1fr)_12rem] gap-2">
                <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="Name" aria-label="Name" />
                <label className="flex items-center gap-2 text-xs text-fg-2">
                    Keep for
                    <input type="number" min={1} max={3650} value={days} onChange={(e) => change(() => setDays(Number(e.target.value)))}
                        aria-label="Days to keep alerts" className={cn(field, 'w-20 num')} /> days
                </label>
            </div>
            <fieldset className="flex flex-wrap items-center gap-4 text-xs text-fg-2">
                <legend className="sr-only">Alert statuses to delete</legend>
                <span className="text-muted">Delete alerts that are</span>
                {STATUSES.map(([k, label]) => (
                    <label key={k} className="flex items-center gap-1.5">
                        <input type="checkbox" checked={statuses.includes(k)}
                            onChange={(e) => change(() => setStatuses(e.target.checked ? [...statuses, k] : statuses.filter((s) => s !== k)))} /> {label}
                    </label>
                ))}
            </fieldset>
            <p className="text-xs text-muted">
                Optional conditions narrow the policy, for example <code className="font-mono">source equals wazuh</code>. Alerts must match <strong>every</strong> condition; matching ignores case.
                Alerts linked to a case are never deleted.
            </p>
            {conds.map((c, i) => (
                <div key={i} className="grid grid-cols-1 sm:grid-cols-[16rem_9rem_minmax(0,1fr)_1.5rem] items-center gap-2">
                    <FieldPicker value={c.field} groups={groups} onChange={(v) => set(i, { field: v })} />
                    <select value={c.op} onChange={(e) => set(i, { op: e.target.value })} aria-label="Operator" className={cn(field, 'text-xs w-full')}>
                        {OPS.map(([k, label]) => <option key={k} value={k}>{label}</option>)}
                    </select>
                    <input value={c.value} onChange={(e) => set(i, { value: e.target.value })} aria-label="Value" placeholder={c.op === 'in_cidr' ? '10.0.0.0/8' : 'value'}
                        className={cn(field, 'font-mono text-xs w-full')} />
                    <button onClick={() => change(() => setConds(conds.filter((_, j) => j !== i)))}
                        className="justify-self-center text-muted hover:text-severity-critical" aria-label="Remove condition"><X size={14} /></button>
                </div>
            ))}
            {conds.length < 10 && (
                <button onClick={() => change(() => setConds([...conds, { field: 'source', op: 'equals', value: '' }]))} className="text-xs text-accent hover:underline">+ Add condition</button>
            )}
            {preview.data && <PreviewBox p={preview.data} />}
            {(save.isError || preview.isError) && <p role="alert" className="text-xs text-severity-critical">{apiError(save.error ?? preview.error, 'Check the settings.')}</p>}
            <div className="flex flex-wrap items-center justify-end gap-2">
                {!policy && <p className="mr-auto text-xs text-muted">New policies start disabled. Check the preview, then enable it.</p>}
                <Button size="sm" variant="secondary" onClick={() => preview.mutate()} disabled={!valid || preview.isPending}>
                    <Search size={12} aria-hidden /> Preview
                </Button>
                <Button size="sm" onClick={() => save.mutate()} disabled={!valid || !name.trim() || save.isPending}>
                    {save.isPending ? 'Saving…' : 'Save policy'}
                </Button>
            </div>
        </div>
    );
}

export default function RetentionList() {
    const qc = useQueryClient();
    const [editing, setEditing] = useState<Policy | 'new' | null>(null);
    const { confirm, dialog } = useConfirm();
    const { data: me } = useQuery({ queryKey: ['currentUser'], queryFn: async () => (await api.get('/users/me')).data as User, staleTime: 300_000 });
    const isAdmin = me?.role === 'admin' || !!me?.is_super_admin;
    const { data: policies = [], isLoading, isError, refetch } = useQuery({
        queryKey: ['alert-retention'], enabled: isAdmin,
        queryFn: async () => (await api.get('/alert-retention/')).data as Policy[],
    });
    const refresh = () => qc.invalidateQueries({ queryKey: ['alert-retention'] });
    const toggle = useMutation({
        mutationFn: async (p: Policy) => {
            if (!p.enabled) {
                const pv = await fetchPreview(p);
                const ok = await confirm({
                    title: 'Enable this policy?', danger: true, confirmLabel: 'Enable',
                    body: <div className="space-y-2"><p>The daily run (02:30 UTC) will permanently delete matching alerts. Right now that is:</p><PreviewBox p={pv} /></div>,
                });
                if (!ok) return;
            }
            await api.put(`/alert-retention/${p.id}`, { enabled: !p.enabled });
        },
        onSuccess: refresh,
    });
    const runNow = useMutation({
        mutationFn: async (p: Policy) => {
            const pv = await fetchPreview(p);
            const ok = await confirm({
                title: `Run “${p.name}” now?`, danger: true, confirmLabel: 'Delete now',
                body: <div className="space-y-2"><p>This permanently deletes matching alerts now, whether or not the policy is enabled:</p><PreviewBox p={pv} /></div>,
            });
            if (!ok) return null;
            return (await api.post(`/alert-retention/${p.id}/run`)).data as Policy;
        },
        onSuccess: (p) => { if (p) qc.setQueryData<Policy[]>(['alert-retention'], (old) => old?.map((x) => (x.id === p.id ? p : x))); refresh(); },
    });
    const remove = useMutation({ mutationFn: async (id: number) => api.delete(`/alert-retention/${id}`), onSuccess: refresh });

    if (me && !isAdmin) return <div className="p-4"><EmptyState title="Admins only" body="Ask an admin of this organization to manage alert retention." /></div>;
    return (
        <div className="p-4 space-y-4">
            <div className="flex flex-wrap items-center gap-3">
                <p className="mr-auto text-sm text-fg-3 num">{isLoading ? ' ' : `${policies.length} ${policies.length === 1 ? 'policy' : 'policies'}`}</p>
                {!editing && <Button size="sm" onClick={() => setEditing('new')}><Plus size={12} aria-hidden /> New policy</Button>}
            </div>
            <p className="text-sm text-fg-3 max-w-[80ch]">
                Delete old alerts once a day (02:30 UTC) or with Run now, for example dismissed alerts after 90 days when your SIEM already keeps them.
                Alerts linked to a case are never deleted, and cases are never deleted automatically. Each run is recorded in the audit log.
            </p>
            {editing && <PolicyForm key={editing === 'new' ? 'new' : editing.id} policy={editing === 'new' ? null : editing} onDone={() => setEditing(null)} />}
            {(toggle.isError || remove.isError || runNow.isError) && <p role="alert" className="text-xs text-severity-critical">{apiError(toggle.error ?? remove.error ?? runNow.error, 'Could not change the policy.')}</p>}
            {isError ? <ErrorState message="Could not load retention policies." onRetry={() => refetch()} /> : isLoading ? <Skeleton rows={3} /> : !policies.length ? (
                <EmptyState title="No retention policies" body="Alerts are kept forever until you add one." />
            ) : (
                <div className="space-y-2">
                    {policies.map((p) => (
                        <article key={p.id} className={cn('rounded-md border border-line bg-panel p-3', !p.enabled && 'opacity-70')}>
                            <div className="flex flex-wrap items-center gap-2">
                                <span className="text-sm font-medium text-fg">{p.name}</span>
                                {!p.enabled && <Badge>Disabled</Badge>}
                                <span className="text-xs text-fg-3 num">{p.total_deleted} deleted in total</span>
                                <span className="ml-auto flex items-center gap-3">
                                    <label className="flex items-center gap-1.5 text-xs text-fg-2">
                                        <input type="checkbox" checked={p.enabled} disabled={toggle.isPending} onChange={() => toggle.mutate(p)} /> Enabled
                                    </label>
                                    <Button size="sm" variant="secondary" onClick={() => runNow.mutate(p)} disabled={runNow.isPending}>
                                        <Play size={12} aria-hidden /> {runNow.isPending && runNow.variables?.id === p.id ? 'Running…' : 'Run now'}
                                    </Button>
                                    <button onClick={() => setEditing(p)} className="text-muted hover:text-fg" aria-label="Edit policy" title="Edit"><Pencil size={13} /></button>
                                    <button onClick={async () => { if (await confirm({ title: 'Delete this policy?', danger: true, confirmLabel: 'Delete policy', body: 'Alerts it already deleted stay deleted; no more are deleted by it.' })) remove.mutate(p.id); }}
                                        className="text-muted hover:text-severity-critical" aria-label="Delete policy" title="Delete"><Trash2 size={13} /></button>
                                </span>
                            </div>
                            <p className="mt-1 text-xs text-fg-2">{summary(p)}{p.conditions.length > 0 && ' where'}</p>
                            {p.conditions.length > 0 && <p className="mt-0.5 text-xs font-mono text-fg-2">{p.conditions.map(describe).join('  AND  ')}</p>}
                            <p className={cn('mt-1 text-xs num', p.last_error ? 'text-severity-critical' : 'text-fg-3')}>
                                {lastRun(p)}{p.last_error && ` · ${p.last_error}`}
                            </p>
                        </article>
                    ))}
                </div>
            )}
            {dialog}
        </div>
    );
}
