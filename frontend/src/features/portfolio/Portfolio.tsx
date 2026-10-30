import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import { Copy, FileText, X } from 'lucide-react';
import { api } from '../../api/client';
import { switchTenant } from '../../api/auth';
import { cn } from '../../lib/utils';
import PageContainer from '../../components/layout/PageContainer';
import type { User } from '../../types';

type TenantRow = {
    id: number; name: string; role: string; open_cases: number; by_severity: Record<string, number>; unassigned: number;
    sla_breached: number; sla_at_risk: number; pending_alerts: number; mttr_hours_30d: number | null; oldest_open_hours: number | null;
};
type QueueRow = {
    tenant_id: number; tenant_name: string; case_id: number; title: string; severity: string; status: string;
    owner: string | null; created_at: string; sla_status: string; sla_due_at: string | null;
};
type Overview = { tenants: TenantRow[]; queue: QueueRow[]; queue_total: number; hidden_tenants: number };
type Digest = { text: string; recipients: string[]; last_sent_at: string | null; can_edit_recipients: boolean };

const SEV = 'text-[10px] font-bold uppercase px-1.5 py-0.5 rounded-sm border';
const SEV_COLOR: Record<string, string> = {
    critical: 'text-severity-critical border-red-500/30 bg-red-500/10', high: 'text-severity-high border-orange-500/30 bg-orange-500/10',
    medium: 'text-severity-medium border-amber-500/30 bg-amber-500/10', low: 'text-severity-low border-accent/40 bg-accent/10',
    info: 'text-fg-2 border-line bg-raised',
};
const SLA_COLOR: Record<string, string> = { breached: 'text-severity-critical', at_risk: 'text-severity-high' };
const hours = (h: number | null) => (h == null ? '—' : h < 48 ? `${Math.round(h)}h` : `${Math.round(h / 24)}d`);
const apiError = (err: unknown, fallback: string) => {
    const d = (err as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
    return typeof d === 'string' ? d : fallback;
};

function DigestPanel({ tenant, onClose }: { tenant: TenantRow; onClose: () => void }) {
    const qc = useQueryClient();
    const [days, setDays] = useState(7);
    const [recipients, setRecipients] = useState<string | null>(null);
    const [copied, setCopied] = useState(false);
    const key = ['portfolio-digest', tenant.id, days];
    const { data } = useQuery({ queryKey: key, queryFn: async () => (await api.get(`/portfolio/${tenant.id}/digest?days=${days}`)).data as Digest });
    const save = useMutation({
        mutationFn: async () => api.put(`/portfolio/${tenant.id}/digest-recipients`, {
            recipients: (recipients ?? '').split(/[\s,;]+/).map((s) => s.trim()).filter(Boolean),
        }),
        onSuccess: () => { setRecipients(null); qc.invalidateQueries({ queryKey: ['portfolio-digest', tenant.id] }); },
    });
    const copy = async () => {
        if (!data) return;
        try { await navigator.clipboard.writeText(data.text); setCopied(true); } catch { /* clipboard unavailable */ }
    };
    return (
        <div className="glass-panel rounded-xl border border-line bg-panel p-4 space-y-3">
            <div className="flex items-center gap-2">
                <h3 className="text-sm font-semibold text-fg">{tenant.name}: summary</h3>
                <select value={days} onChange={(e) => { setDays(Number(e.target.value)); setCopied(false); }} aria-label="Period"
                    className="ml-2 bg-panel border border-line rounded-sm px-2 py-1 text-xs text-fg-2">
                    <option value={7}>last 7 days</option><option value={30}>last 30 days</option><option value={90}>last 90 days</option>
                </select>
                <button onClick={copy} disabled={!data} className="ml-auto text-xs bg-raised hover:bg-raised-2 text-fg-2 px-2.5 py-1 rounded-sm flex items-center gap-1.5">
                    <Copy size={12} /> {copied ? 'Copied' : 'Copy'}
                </button>
                <button onClick={onClose} className="text-muted hover:text-fg" aria-label="Close"><X size={16} /></button>
            </div>
            <pre className="text-xs text-fg-2 bg-surface rounded-lg p-3 whitespace-pre-wrap">{data?.text ?? 'Loading…'}</pre>
            {data && (
                <div className="text-xs text-fg-2 space-y-1.5">
                    <p>Weekly email: {data.recipients.length ? data.recipients.join(', ') : 'off'}
                        {data.last_sent_at && <span className="text-muted"> · last sent {new Date(data.last_sent_at).toLocaleString()}</span>}</p>
                    {data.can_edit_recipients && (recipients === null ? (
                        <button onClick={() => setRecipients(data.recipients.join(', '))} className="text-accent hover:underline">Change recipients</button>
                    ) : (
                        <div className="flex gap-2">
                            <input value={recipients} onChange={(e) => setRecipients(e.target.value)} placeholder="ciso@example.com, it@example.com (empty = off)"
                                aria-label="Recipients" className="flex-1 bg-panel border border-line rounded-sm px-2 py-1 text-xs text-fg" />
                            <button onClick={() => save.mutate()} disabled={save.isPending} className="px-2.5 py-1 bg-accent text-on-accent rounded-sm">Save</button>
                            <button onClick={() => setRecipients(null)} className="px-2 py-1 text-fg-3">Cancel</button>
                        </div>
                    ))}
                    {save.isError && <p role="alert" className="text-severity-critical">{apiError(save.error, 'Could not save the recipients.')}</p>}
                    <p className="text-muted">Sent once a week to these addresses (the first one within the hour). Only this organization's admins can change them.</p>
                </div>
            )}
        </div>
    );
}

export default function Portfolio() {
    const qc = useQueryClient();
    const navigate = useNavigate();
    const [digestFor, setDigestFor] = useState<TenantRow | null>(null);
    const [tenantFilter, setTenantFilter] = useState<number | 'all'>('all');
    const [switchError, setSwitchError] = useState<string | null>(null);
    const { data: me } = useQuery({ queryKey: ['currentUser'], queryFn: async () => (await api.get('/users/me')).data as User, staleTime: 300_000 });
    const { data, isLoading, dataUpdatedAt } = useQuery({ queryKey: ['portfolio'], queryFn: async () => (await api.get('/portfolio/')).data as Overview, refetchInterval: 60_000 });

    // Opening a case of another organization switches the active tenant first (same checks as the tenant switcher).
    const open = async (tenantId: number, path: string) => {
        setSwitchError(null);
        try {
            if (me?.active_tenant_id !== tenantId) {
                await switchTenant(tenantId);
                await qc.invalidateQueries();
            }
            navigate(path);
        } catch (err) {
            setSwitchError(apiError(err, 'Could not switch to that organization.'));
        }
    };

    if (isLoading || !data) return <PageContainer><p className="font-mono text-xs text-muted">$ loading organizations…</p></PageContainer>;
    const queue = tenantFilter === 'all' ? data.queue : data.queue.filter((q) => q.tenant_id === tenantFilter);
    return (
        <PageContainer className="space-y-5">
            <div>
                <h1 className="text-2xl font-bold text-fg tracking-tight">Organizations</h1>
                <p className="text-fg-3 text-sm mt-0.5">
                    Every organization you work in, and one queue of their open cases: SLA breaches first, then severity, then age.
                    {data.hidden_tenants > 0 && ` ${data.hidden_tenants} organization${data.hidden_tenants > 1 ? 's' : ''} need a different sign-in (SSO or two-factor) and are not shown.`}
                </p>
            </div>
            {switchError && <p role="alert" className="text-sm text-severity-critical">{switchError}</p>}
            <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-3">
                {data.tenants.map((t) => (
                    <article key={t.id} className={cn('rounded-lg border bg-panel p-3 space-y-2', t.sla_breached ? 'border-red-500/40' : 'border-line')}>
                        <div className="flex items-center gap-2">
                            <button onClick={() => open(t.id, '/')} className="text-sm font-semibold text-fg hover:underline truncate">{t.name}</button>
                            <span className="text-[10px] font-mono text-muted">{t.role}</span>
                            <button onClick={() => setDigestFor(t)} title="Summary" className="ml-auto text-muted hover:text-fg"><FileText size={14} /></button>
                        </div>
                        <dl className="grid grid-cols-3 gap-2 text-xs">
                            <div><dt className="text-muted">Open</dt><dd className="text-fg font-mono">{t.open_cases}</dd></div>
                            <div><dt className="text-muted">SLA breached</dt><dd className={cn('font-mono', t.sla_breached ? 'text-severity-critical' : 'text-fg')}>{t.sla_breached}</dd></div>
                            <div><dt className="text-muted">At risk</dt><dd className={cn('font-mono', t.sla_at_risk ? 'text-severity-high' : 'text-fg')}>{t.sla_at_risk}</dd></div>
                            <div><dt className="text-muted">Unassigned</dt><dd className="text-fg font-mono">{t.unassigned}</dd></div>
                            <div><dt className="text-muted">Pending alerts</dt><dd className="text-fg font-mono">{t.pending_alerts}</dd></div>
                            <div><dt className="text-muted">MTTR 30d</dt><dd className="text-fg font-mono">{hours(t.mttr_hours_30d)}</dd></div>
                        </dl>
                        <p className="text-[11px] text-muted">
                            {(['critical', 'high', 'medium', 'low'] as const).filter((s) => t.by_severity[s]).map((s) => `${t.by_severity[s]} ${s}`).join(' · ') || 'no open cases'}
                            {t.oldest_open_hours != null && ` · oldest ${hours(t.oldest_open_hours)}`}
                        </p>
                    </article>
                ))}
            </div>
            {digestFor && <DigestPanel key={digestFor.id} tenant={digestFor} onClose={() => setDigestFor(null)} />}
            <div className="space-y-2">
                <div className="flex items-center gap-2">
                    <h2 className="text-sm font-semibold text-fg-2">Open cases <span className="font-mono text-xs text-muted">{queue.length}{data.queue_total > data.queue.length && ` of ${data.queue_total}`}</span></h2>
                    <select value={tenantFilter} onChange={(e) => setTenantFilter(e.target.value === 'all' ? 'all' : Number(e.target.value))} aria-label="Organization"
                        className="ml-auto bg-panel border border-line rounded-sm px-2 py-1 text-xs text-fg-2">
                        <option value="all">All organizations</option>
                        {data.tenants.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
                    </select>
                </div>
                <div className="overflow-x-auto rounded-lg border border-line">
                    <table className="w-full text-sm">
                        <thead className="text-[11px] uppercase text-muted bg-surface">
                            <tr><th className="px-3 py-2 text-left">SLA</th><th className="px-3 py-2 text-left">Severity</th><th className="px-3 py-2 text-left">Organization</th>
                                <th className="px-3 py-2 text-left">Case</th><th className="px-3 py-2 text-left">Status</th><th className="px-3 py-2 text-left">Owner</th><th className="px-3 py-2 text-left">Age</th></tr>
                        </thead>
                        <tbody className="divide-y divide-line">
                            {queue.length === 0 ? <tr><td colSpan={7} className="px-3 py-8 text-center text-muted">No open cases.</td></tr> : queue.map((q) => (
                                <tr key={`${q.tenant_id}-${q.case_id}`} className="hover:bg-raised">
                                    <td className={cn('px-3 py-2 text-xs whitespace-nowrap', SLA_COLOR[q.sla_status] ?? 'text-fg-3')}>{q.sla_status.replace('_', ' ')}</td>
                                    <td className="px-3 py-2"><span className={cn(SEV, SEV_COLOR[q.severity] ?? SEV_COLOR.info)}>{q.severity}</span></td>
                                    <td className="px-3 py-2 text-xs text-fg-2 whitespace-nowrap">{q.tenant_name}</td>
                                    <td className="px-3 py-2"><button onClick={() => open(q.tenant_id, `/cases/${q.case_id}`)} className="text-fg hover:underline text-left">#{q.case_id} {q.title}</button></td>
                                    <td className="px-3 py-2 text-xs text-fg-3">{q.status.replace('_', ' ')}</td>
                                    <td className="px-3 py-2 text-xs text-fg-3">{q.owner ?? <span className="text-severity-high">unassigned</span>}</td>
                                    <td className="px-3 py-2 text-xs font-mono text-fg-3">{hours((dataUpdatedAt - new Date(q.created_at).getTime()) / 3_600_000)}</td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            </div>
        </PageContainer>
    );
}
