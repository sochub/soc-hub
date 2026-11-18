import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import { Copy, FileText, X } from 'lucide-react';
import { api } from '../../api/client';
import { switchTenant } from '../../api/auth';
import { cn } from '../../lib/utils';
import PageContainer from '../../components/layout/PageContainer';
import { Button, Card, ErrorState, Input, Label, PageHeader, Select, SeverityStamp, Skeleton, StatusStamp } from '../../components/ui';
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
        <Card className="p-4 space-y-3">
            <div className="flex flex-wrap items-center gap-2">
                <h2 className="text-sm font-semibold text-fg">{tenant.name}: summary</h2>
                <Select inputSize="sm" value={days} onChange={(e) => { setDays(Number(e.target.value)); setCopied(false); }} aria-label="Period" className="ml-2 w-auto">
                    <option value={7}>Last 7 days</option><option value={30}>Last 30 days</option><option value={90}>Last 90 days</option>
                </Select>
                <Button size="sm" variant="secondary" onClick={copy} disabled={!data} className="ml-auto">
                    <Copy size={12} aria-hidden /> {copied ? 'Copied' : 'Copy'}
                </Button>
                <Button size="icon" variant="ghost" onClick={onClose} aria-label="Close summary"><X size={16} /></Button>
            </div>
            {data ? <pre className="text-xs text-fg-2 bg-surface border border-line rounded-md p-3 whitespace-pre-wrap">{data.text}</pre> : <Skeleton rows={3} />}
            {data && (
                <div className="text-xs text-fg-2 space-y-1.5">
                    <p>Weekly email: {data.recipients.length ? data.recipients.join(', ') : 'off'}
                        {data.last_sent_at && <span className="text-muted"> · last sent {new Date(data.last_sent_at).toLocaleString()}</span>}</p>
                    {data.can_edit_recipients && (recipients === null ? (
                        <button type="button" onClick={() => setRecipients(data.recipients.join(', '))} className="text-accent hover:underline">Change recipients</button>
                    ) : (
                        <div className="flex flex-wrap gap-2">
                            <Input inputSize="sm" value={recipients} onChange={(e) => setRecipients(e.target.value)} placeholder="ciso@example.com, it@example.com (empty = off)"
                                aria-label="Recipients" className="flex-1 min-w-48" />
                            <Button size="sm" onClick={() => save.mutate()} disabled={save.isPending}>Save</Button>
                            <Button size="sm" variant="ghost" onClick={() => setRecipients(null)}>Cancel</Button>
                        </div>
                    ))}
                    {save.isError && <p role="alert" className="text-severity-critical">{apiError(save.error, 'Could not save the recipients.')}</p>}
                    <p className="text-fg-3">Sent once a week to these addresses (the first one within the hour). Only this organization's admins can change them.</p>
                </div>
            )}
        </Card>
    );
}

export default function Portfolio() {
    const qc = useQueryClient();
    const navigate = useNavigate();
    const [digestFor, setDigestFor] = useState<TenantRow | null>(null);
    const [tenantFilter, setTenantFilter] = useState<number | 'all'>('all');
    const [switchError, setSwitchError] = useState<string | null>(null);
    const { data: me } = useQuery({ queryKey: ['currentUser'], queryFn: async () => (await api.get('/users/me')).data as User, staleTime: 300_000 });
    const { data, isLoading, isError, refetch, dataUpdatedAt } = useQuery({ queryKey: ['portfolio'], queryFn: async () => (await api.get('/portfolio/')).data as Overview, refetchInterval: 60_000 });

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

    if (isLoading) return <PageContainer><PageHeader title="Organizations" className="mb-0" /><Skeleton rows={8} /></PageContainer>;
    if (isError || !data) return <PageContainer><PageHeader title="Organizations" className="mb-0" /><ErrorState message="Could not load your organizations." onRetry={() => refetch()} /></PageContainer>;
    const queue = tenantFilter === 'all' ? data.queue : data.queue.filter((q) => q.tenant_id === tenantFilter);
    return (
        <PageContainer>
            <PageHeader title="Organizations" count={data.tenants.length} className="mb-0" description={<>
                Every organization you work in, and one queue of their open cases: SLA breaches first, then severity, then age.
                {data.hidden_tenants > 0 && ` ${data.hidden_tenants} organization${data.hidden_tenants > 1 ? 's' : ''} need a different sign-in (SSO or two-factor) and are not shown.`}
            </>} />
            {switchError && <p role="alert" className="text-sm text-severity-critical">{switchError}</p>}
            <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-3">
                {data.tenants.map((t) => (
                    <article key={t.id} className={cn('rounded-md border bg-panel p-4 space-y-3', t.sla_breached ? 'border-severity-critical/50' : 'border-line')}>
                        <div className="flex items-center gap-2">
                            <button type="button" onClick={() => open(t.id, '/')} className="text-sm font-semibold text-fg hover:underline truncate">{t.name}</button>
                            <span className="text-xs text-fg-3 capitalize">{t.role}</span>
                            <Button size="icon" variant="ghost" onClick={() => setDigestFor(t)} title="Summary" aria-label={`Summary for ${t.name}`} className="ml-auto h-8 w-8"><FileText size={14} /></Button>
                        </div>
                        <dl className="grid grid-cols-3 gap-2 text-xs">
                            <div><dt className="text-fg-3">Open</dt><dd className="num text-sm text-fg">{t.open_cases}</dd></div>
                            <div><dt className="text-fg-3">SLA breached</dt><dd className={cn('num text-sm', t.sla_breached ? 'text-severity-critical font-semibold' : 'text-fg')}>{t.sla_breached}</dd></div>
                            <div><dt className="text-fg-3">At risk</dt><dd className={cn('num text-sm', t.sla_at_risk ? 'text-severity-high font-semibold' : 'text-fg')}>{t.sla_at_risk}</dd></div>
                            <div><dt className="text-fg-3">Unassigned</dt><dd className="num text-sm text-fg">{t.unassigned}</dd></div>
                            <div><dt className="text-fg-3">Pending alerts</dt><dd className="num text-sm text-fg">{t.pending_alerts}</dd></div>
                            <div><dt className="text-fg-3">MTTR 30d</dt><dd className="num text-sm text-fg">{hours(t.mttr_hours_30d)}</dd></div>
                        </dl>
                        <div className="flex flex-wrap items-center gap-1.5 text-xs text-fg-3">
                            {(['critical', 'high', 'medium', 'low'] as const).filter((s) => t.by_severity[s]).map((s) => (
                                <span key={s} className="inline-flex items-center gap-1"><span className="num text-fg-2">{t.by_severity[s]}</span><SeverityStamp severity={s} /></span>
                            ))}
                            {!(['critical', 'high', 'medium', 'low'] as const).some((s) => t.by_severity[s]) && <span>No open cases</span>}
                            {t.oldest_open_hours != null && <span className="num">· oldest {hours(t.oldest_open_hours)}</span>}
                        </div>
                    </article>
                ))}
            </div>
            {digestFor && <DigestPanel key={digestFor.id} tenant={digestFor} onClose={() => setDigestFor(null)} />}
            <section className="space-y-2">
                <div className="flex flex-wrap items-center gap-2">
                    <h2 className="text-sm font-semibold text-fg">Open cases <span className="num ml-1 font-normal text-fg-3">{queue.length}{data.queue_total > data.queue.length && ` of ${data.queue_total}`}</span></h2>
                    <Label htmlFor="portfolio-org-filter" className="sr-only">Organization</Label>
                    <Select id="portfolio-org-filter" inputSize="sm" value={tenantFilter} onChange={(e) => setTenantFilter(e.target.value === 'all' ? 'all' : Number(e.target.value))}
                        className="ml-auto w-auto">
                        <option value="all">All organizations</option>
                        {data.tenants.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
                    </Select>
                </div>
                <div className="overflow-x-auto rounded-md border border-line bg-panel">
                    <table className="w-full text-sm">
                        <thead className="bg-surface border-b border-line">
                            <tr className="label-caps text-left"><th className="px-3 py-2">SLA</th><th className="px-3 py-2">Severity</th><th className="px-3 py-2">Organization</th>
                                <th className="px-3 py-2">Case</th><th className="px-3 py-2">Status</th><th className="px-3 py-2">Owner</th><th className="px-3 py-2">Age</th></tr>
                        </thead>
                        <tbody className="divide-y divide-line">
                            {queue.length === 0 ? <tr><td colSpan={7} className="px-3 py-8 text-center text-fg-3">No open cases.</td></tr> : queue.map((q) => (
                                <tr key={`${q.tenant_id}-${q.case_id}`} className="hover:bg-raised cursor-pointer" onClick={() => open(q.tenant_id, `/cases/${q.case_id}`)}>
                                    <td className={cn('px-3 py-2.5 text-xs whitespace-nowrap', SLA_COLOR[q.sla_status] ?? 'text-fg-3')}>{q.sla_status.replace('_', ' ')}</td>
                                    <td className="px-3 py-2.5"><SeverityStamp severity={q.severity} /></td>
                                    <td className="px-3 py-2.5 text-xs text-fg-2 whitespace-nowrap">{q.tenant_name}</td>
                                    <td className="px-3 py-2.5"><button type="button" onClick={(e) => { e.stopPropagation(); open(q.tenant_id, `/cases/${q.case_id}`); }} className="text-fg hover:underline text-left"><span className="num text-fg-3">#{q.case_id}</span> {q.title}</button></td>
                                    <td className="px-3 py-2.5"><StatusStamp status={q.status} /></td>
                                    <td className="px-3 py-2.5 text-xs text-fg-3">{q.owner ?? <span className="text-severity-high">Unassigned</span>}</td>
                                    <td className="px-3 py-2.5 text-xs num text-fg-3">{hours((dataUpdatedAt - new Date(q.created_at).getTime()) / 3_600_000)}</td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            </section>
        </PageContainer>
    );
}
