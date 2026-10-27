import { Fragment, useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '../../api/client';
import { cn } from '../../lib/utils';
import { ChevronDown, ChevronRight, ChevronUp, Search, X, CheckCircle } from 'lucide-react';
import { Link } from 'react-router-dom';
import PromoteAlertModal from './PromoteAlertModal';
import { AlertRuns } from '../automations/CaseAutomation';
import PageContainer from '../../components/layout/PageContainer';

interface Alert {
    id: number;
    source: string;
    external_id: string;
    title: string;
    payload: unknown;
    status: string;
    severity: string;
    owner_id: number | null;
    case_id: number | null;
    suppression_rule_id?: number | null;
    created_at: string;
}

const statusColor: Record<string, string> = {
    pending: 'text-amber-300 bg-amber-500/10 border-amber-500/30',
    promoted: 'text-emerald-300 bg-emerald-500/10 border-emerald-500/30',
    dismissed: 'text-fg-3 bg-raised border-line',
};

const SEVERITIES = ['critical', 'high', 'medium', 'low', 'info'];
const severityColor: Record<string, string> = {
    critical: 'text-red-300 bg-red-500/15 border-red-500/40',
    high: 'text-orange-300 bg-orange-500/10 border-orange-500/30',
    medium: 'text-amber-300 bg-amber-500/10 border-amber-500/30',
    low: 'text-fg-3 bg-raised border-line',
    info: 'text-fg-3 bg-raised border-line',
};
const badge = 'inline-flex px-2 py-0.5 rounded-sm text-[10px] font-bold uppercase tracking-wider border';
type SortKey = 'severity' | 'created_at';

function SimilarAlerts({ alertId, onOpen }: { alertId: number; onOpen: (id: number) => void }) {
    const { data } = useQuery<Alert[]>({
        queryKey: ['alerts', alertId, 'similar'],
        queryFn: async () => (await api.get(`/alerts/${alertId}/similar`)).data,
    });
    if (!data?.length) return null;
    return (
        <div className="mb-3">
            <div className="text-xs font-semibold text-fg-3 uppercase tracking-wider mb-1">Similar alerts ({data.length})</div>
            <ul className="space-y-1">
                {data.map(s => (
                    <li key={s.id} className="text-xs flex items-center gap-2">
                        <span className={cn(badge, severityColor[s.severity] || severityColor.info)}>{s.severity}</span>
                        <button onClick={() => onOpen(s.id)} className="text-accent hover:underline">#{s.id} {s.title}</button>
                        {s.case_id && <Link to={`/cases/${s.case_id}`} className="text-accent hover:underline">Case #{s.case_id}</Link>}
                    </li>
                ))}
            </ul>
        </div>
    );
}

export default function AlertsList() {
    const qc = useQueryClient();
    const [statusFilter, setStatusFilter] = useState('pending');
    const [severityFilter, setSeverityFilter] = useState('all');
    const [sort, setSort] = useState<{ key: SortKey; dir: 1 | -1 }>({ key: 'created_at', dir: -1 });
    const [selected, setSelected] = useState<Set<number>>(new Set());
    const [bulkPromote, setBulkPromote] = useState(false);
    const [sourceFilter, setSourceFilter] = useState('all');
    const [expanded, setExpanded] = useState<number | null>(null);
    const [promoting, setPromoting] = useState<Alert | null>(null);

    const { data: alerts, isLoading } = useQuery<Alert[]>({
        queryKey: ['alerts'],
        queryFn: async () => (await api.get('/alerts/')).data,
    });

    const dismiss = useMutation({
        mutationFn: async (id: number) => (await api.post(`/alerts/${id}/dismiss`)).data,
        onSuccess: () => qc.invalidateQueries({ queryKey: ['alerts'] }),
    });

    const bulkDismiss = useMutation({
        mutationFn: async (ids: number[]) => (await api.post('/alerts/bulk', { ids, action: 'dismiss' })).data,
        onSuccess: () => { setSelected(new Set()); qc.invalidateQueries({ queryKey: ['alerts'] }); },
    });

    const sources = Array.from(new Set((alerts || []).map(a => a.source).filter(Boolean)));
    const filtered = (alerts || []).filter(a =>
        (statusFilter === 'all' || a.status === statusFilter) &&
        (sourceFilter === 'all' || a.source === sourceFilter) &&
        (severityFilter === 'all' || a.severity === severityFilter)
    ).sort((x, y) => sort.dir * (sort.key === 'severity'
        ? SEVERITIES.indexOf(y.severity) - SEVERITIES.indexOf(x.severity)
        : x.created_at.localeCompare(y.created_at)));
    const selectedIds = filtered.filter(a => selected.has(a.id)).map(a => a.id);
    const allSelected = filtered.length > 0 && selectedIds.length === filtered.length;
    const toggle = (id: number) => setSelected(prev => {
        const next = new Set(prev);
        if (next.has(id)) next.delete(id); else next.add(id);
        return next;
    });
    const sortHeader = (key: SortKey, label: string) => (
        <button onClick={() => setSort(s => ({ key, dir: s.key === key ? (-s.dir as 1 | -1) : -1 }))}
            className="inline-flex items-center gap-1 uppercase hover:text-fg">
            {label}{sort.key === key && (sort.dir === -1 ? <ChevronDown size={12} /> : <ChevronUp size={12} />)}
        </button>
    );
    const openAlert = (id: number) => { setStatusFilter('all'); setSeverityFilter('all'); setSourceFilter('all'); setExpanded(id); };

    if (isLoading) {
        return <PageContainer><div className="flex items-center justify-center h-[50vh]">
            <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-line-strong" /></div></PageContainer>;
    }

    return (
        <PageContainer className="space-y-4">
            <div className="flex flex-col lg:flex-row lg:items-center justify-between gap-4">
                <div>
                    <h1 className="text-2xl font-bold text-fg tracking-tight">Alerts</h1>
                    <p className="text-fg-3 text-sm mt-0.5">{filtered.length} alerts</p>
                </div>
                <div className="flex flex-wrap items-center gap-2">
                    <div className="relative">
                        <select value={sourceFilter} onChange={e => setSourceFilter(e.target.value)}
                            className="appearance-none bg-panel border border-line rounded-lg pl-3 pr-8 py-1.5 text-sm text-fg-2 cursor-pointer focus:outline-hidden focus:ring-1 focus:ring-accent">
                            <option value="all">All Sources</option>
                            {sources.map(s => <option key={s} value={s}>{s}</option>)}
                        </select>
                        <ChevronDown size={14} className="absolute right-2.5 top-1/2 -translate-y-1/2 text-muted pointer-events-none" />
                    </div>
                    <div className="relative">
                        <select value={severityFilter} onChange={e => setSeverityFilter(e.target.value)}
                            className="appearance-none bg-panel border border-line rounded-lg pl-3 pr-8 py-1.5 text-sm text-fg-2 cursor-pointer focus:outline-hidden focus:ring-1 focus:ring-accent">
                            <option value="all">All Severities</option>
                            {SEVERITIES.map(s => <option key={s} value={s}>{s[0].toUpperCase() + s.slice(1)}</option>)}
                        </select>
                        <ChevronDown size={14} className="absolute right-2.5 top-1/2 -translate-y-1/2 text-muted pointer-events-none" />
                    </div>
                    <div className="relative">
                        <select value={statusFilter} onChange={e => setStatusFilter(e.target.value)}
                            className="appearance-none bg-panel border border-line rounded-lg pl-3 pr-8 py-1.5 text-sm text-fg-2 cursor-pointer focus:outline-hidden focus:ring-1 focus:ring-accent">
                            <option value="all">All Statuses</option>
                            <option value="pending">Pending</option>
                            <option value="promoted">Promoted</option>
                            <option value="dismissed">Dismissed</option>
                        </select>
                        <ChevronDown size={14} className="absolute right-2.5 top-1/2 -translate-y-1/2 text-muted pointer-events-none" />
                    </div>
                </div>
            </div>

            {selectedIds.length > 0 && (
                <div className="flex items-center gap-2 px-3 py-2 rounded-lg border border-line bg-panel text-sm">
                    <span className="text-fg-2">{selectedIds.length} selected</span>
                    <button onClick={() => setBulkPromote(true)} className="px-2 py-1 text-xs font-semibold rounded-sm bg-accent text-on-accent hover:bg-accent-hover flex items-center gap-1"><CheckCircle size={12} />Promote selected…</button>
                    <button disabled={bulkDismiss.isPending} onClick={() => bulkDismiss.mutate(selectedIds)} className="px-2 py-1 text-xs font-semibold rounded-sm bg-raised text-fg-2 hover:bg-raised-2 flex items-center gap-1"><X size={12} />Dismiss selected</button>
                    <button onClick={() => setSelected(new Set())} className="ml-auto text-xs text-muted hover:text-fg-2">Clear</button>
                </div>
            )}

            <div className="glass-panel rounded-lg border border-line overflow-hidden">
                <table className="w-full text-sm text-left">
                    <thead className="bg-panel border-b border-line text-xs uppercase text-fg-3 font-semibold tracking-wider">
                        <tr>
                            <th className="px-4 py-3 w-8">
                                <input type="checkbox" aria-label="Select all" checked={allSelected}
                                    onChange={() => setSelected(allSelected ? new Set() : new Set(filtered.map(a => a.id)))} />
                            </th>
                            <th className="px-4 py-3 w-8"></th>
                            <th className="px-4 py-3 w-28">{sortHeader('severity', 'Severity')}</th>
                            <th className="px-4 py-3 w-40">Source</th>
                            <th className="px-4 py-3">Title</th>
                            <th className="px-4 py-3 w-28">Status</th>
                            <th className="px-4 py-3 w-40">{sortHeader('created_at', 'Received')}</th>
                            <th className="px-4 py-3 w-56">Actions</th>
                        </tr>
                    </thead>
                    <tbody className="divide-y divide-line">
                        {filtered.length === 0 ? (
                            <tr><td colSpan={8} className="px-6 py-12 text-center text-muted">
                                <Search size={32} className="mb-2 opacity-50 mx-auto" />No alerts</td></tr>
                        ) : filtered.map(a => (
                            <Fragment key={a.id}>
                                <tr className="group hover:bg-raised">
                                    <td className="px-4 py-2.5">
                                        <input type="checkbox" aria-label={`Select alert ${a.id}`} checked={selected.has(a.id)} onChange={() => toggle(a.id)} />
                                    </td>
                                    <td className="px-4 py-2.5">
                                        <button onClick={() => setExpanded(expanded === a.id ? null : a.id)} className="text-muted hover:text-fg-2">
                                            {expanded === a.id ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
                                        </button>
                                    </td>
                                    <td className="px-4 py-2.5"><span className={cn(badge, severityColor[a.severity] || severityColor.info)}>{a.severity}</span></td>
                                    <td className="px-4 py-2.5"><span className="font-mono text-xs text-fg-2">{a.source}</span></td>
                                    <td className="px-4 py-2.5 font-medium text-fg">{a.title}</td>
                                    <td className="px-4 py-2.5">
                                        <span className={cn(badge, statusColor[a.status] || statusColor.dismissed)}>{a.status}</span>
                                        {a.suppression_rule_id && (
                                            <Link to="/alerts/suppressions" title="Dismissed at ingest by a suppression rule"
                                                className="ml-1.5 text-[10px] text-muted hover:text-fg-2">rule #{a.suppression_rule_id}</Link>
                                        )}
                                    </td>
                                    <td className="px-4 py-2.5 text-fg-3 text-xs whitespace-nowrap">{new Date(a.created_at).toLocaleString()}</td>
                                    <td className="px-4 py-2.5">
                                        {a.status === 'pending' ? (
                                            <div className="flex items-center gap-2">
                                                <button onClick={() => setPromoting(a)} className="px-2 py-1 text-xs font-semibold rounded-sm bg-accent text-on-accent hover:bg-accent-hover flex items-center gap-1"><CheckCircle size={12} />Promote</button>
                                                <button onClick={() => dismiss.mutate(a.id)} className="px-2 py-1 text-xs font-semibold rounded-sm bg-raised text-fg-2 hover:bg-raised-2 flex items-center gap-1"><X size={12} />Dismiss</button>
                                                <Link to={`/alerts/suppressions?from_alert=${a.id}`} title="Create a suppression rule pre-filled from this alert"
                                                    className="text-xs text-fg-3 hover:text-fg hover:underline whitespace-nowrap">Suppress similar</Link>
                                            </div>
                                        ) : a.case_id ? (
                                            <Link to={`/cases/${a.case_id}`} className="text-xs text-accent hover:underline">Case #{a.case_id}</Link>
                                        ) : !a.suppression_rule_id ? (
                                            <Link to={`/alerts/suppressions?from_alert=${a.id}`} title="Create a suppression rule pre-filled from this alert"
                                                className="text-xs text-fg-3 hover:text-fg hover:underline">Suppress similar</Link>
                                        ) : <span className="text-xs text-muted">—</span>}
                                    </td>
                                </tr>
                                {expanded === a.id && (
                                    <tr><td colSpan={8} className="px-6 py-3 bg-surface">
                                        <SimilarAlerts alertId={a.id} onOpen={openAlert} />
                                        <AlertRuns alertId={a.id} />
                                        <pre className="text-xs text-fg-2 overflow-x-auto">{JSON.stringify(a.payload, null, 2)}</pre>
                                    </td></tr>
                                )}
                            </Fragment>
                        ))}
                    </tbody>
                </table>
            </div>

            {promoting && <PromoteAlertModal alert={promoting} onClose={() => setPromoting(null)} />}
            {bulkPromote && selectedIds.length > 0 && (
                <PromoteAlertModal alert={filtered.find(a => a.id === selectedIds[0])!} ids={selectedIds}
                    onClose={() => setBulkPromote(false)} />
            )}
        </PageContainer>
    );
}
