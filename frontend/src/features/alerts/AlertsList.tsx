import { Fragment, useState } from 'react';
import { useQuery, useMutation, useQueryClient, keepPreviousData } from '@tanstack/react-query';
import { api } from '../../api/client';
import { cn } from '../../lib/utils';
import { ChevronDown, ChevronLeft, ChevronRight, ChevronUp, Search, X, CheckCircle } from 'lucide-react';
import { Link } from 'react-router-dom';
import PromoteAlertModal from './PromoteAlertModal';
import { AlertRuns } from '../automations/CaseAutomation';
import PageContainer from '../../components/layout/PageContainer';
import { FIXED_COLUMN_COUNT, useAlertColumns } from './alertColumns';
import ColumnsMenu from './ColumnsMenu';
import Modal from '../../components/layout/Modal';
import { useCanRun } from '../enrichment/useCanRun';
import { apiErrorMessage } from '../../api/errors';

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
const PAGE_SIZE = 100;

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

/** One alert by id (GET /alerts/{id}), wherever it sits in the list: similar-alert links open here. */
function AlertDetailModal({ alertId, onOpen, onClose }: { alertId: number; onOpen: (id: number) => void; onClose: () => void }) {
    const { data: a, isLoading, isError, error } = useQuery<Alert>({
        queryKey: ['alerts', alertId, 'detail'],
        queryFn: async () => (await api.get(`/alerts/${alertId}`)).data,
    });
    return (
        <Modal open onClose={onClose} size="xl" title={a ? `Alert #${a.id}` : `Alert #${alertId}`}>
            {isLoading ? (
                <div className="py-10 flex justify-center"><div className="animate-spin rounded-full h-6 w-6 border-b-2 border-line-strong" /></div>
            ) : isError || !a ? (
                <p className="text-sm text-severity-critical">{apiErrorMessage(error, 'Could not load this alert.')}</p>
            ) : (
                <div className="space-y-3">
                    <div className="flex flex-wrap items-center gap-2 text-sm">
                        <span className={cn(badge, severityColor[a.severity] || severityColor.info)}>{a.severity}</span>
                        <span className={cn(badge, statusColor[a.status] || statusColor.dismissed)}>{a.status}</span>
                        <span className="font-mono text-xs text-fg-2">{a.source}</span>
                        <span className="text-xs text-fg-3">{new Date(a.created_at).toLocaleString()}</span>
                        {a.case_id && <Link to={`/cases/${a.case_id}`} className="text-xs text-accent hover:underline">Case #{a.case_id}</Link>}
                    </div>
                    <p className="font-medium text-fg">{a.title}</p>
                    <SimilarAlerts alertId={a.id} onOpen={onOpen} />
                    <AlertRuns alertId={a.id} />
                    <pre className="text-xs text-fg-2 overflow-x-auto bg-surface p-3 rounded-sm">{JSON.stringify(a.payload, null, 2)}</pre>
                </div>
            )}
        </Modal>
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
    const [viewing, setViewing] = useState<number | null>(null);
    const [actionError, setActionError] = useState<string | null>(null);
    const canAct = useCanRun();  // promote/dismiss/suppress need analyst or admin
    const cols = useAlertColumns();
    const colSpan = FIXED_COLUMN_COUNT + cols.visibleCount;

    const [page, setPage] = useState(0);
    // Server sort keys: 'severity' = most severe first, '-severity' = least severe first.
    const sortParam = sort.key === 'severity' ? (sort.dir === -1 ? 'severity' : '-severity') : (sort.dir === -1 ? '-created_at' : 'created_at');
    const params = {
        skip: page * PAGE_SIZE, limit: PAGE_SIZE, sort: sortParam,
        ...(statusFilter !== 'all' && { status: statusFilter }),
        ...(severityFilter !== 'all' && { severity: severityFilter }),
        ...(sourceFilter !== 'all' && { source: sourceFilter }),
    };
    const { data: result, isLoading } = useQuery({
        queryKey: ['alerts', 'list', params],
        queryFn: async () => {
            const r = await api.get<Alert[]>('/alerts/', { params });
            return { items: r.data, total: Number(r.headers['x-total-count'] ?? r.data.length) };
        },
        placeholderData: keepPreviousData,
    });
    const filtered = result?.items ?? [];
    const total = result?.total ?? 0;
    const { data: sources = [] } = useQuery<string[]>({
        queryKey: ['alerts', 'sources'],
        queryFn: async () => (await api.get('/alerts/sources')).data,
    });
    // Changing a filter or the sort order returns to page 1 and drops the selection.
    const resetView = () => { setPage(0); setSelected(new Set()); };
    // If the current page emptied (e.g. last alert dismissed), step back to the last page that has rows.
    if (result && result.items.length === 0 && result.total > 0 && page > 0) {
        setPage(Math.max(0, Math.ceil(result.total / PAGE_SIZE) - 1));
    }

    const dismiss = useMutation({
        mutationFn: async (id: number) => (await api.post(`/alerts/${id}/dismiss`)).data,
        onMutate: () => setActionError(null),
        onSuccess: () => qc.invalidateQueries({ queryKey: ['alerts'] }),
        onError: (e) => setActionError(apiErrorMessage(e, 'Could not dismiss the alert.')),
    });

    const bulkDismiss = useMutation({
        mutationFn: async (ids: number[]) => (await api.post('/alerts/bulk', { ids, action: 'dismiss' })).data,
        onMutate: () => setActionError(null),
        onSuccess: () => { setSelected(new Set()); qc.invalidateQueries({ queryKey: ['alerts'] }); },
        onError: (e) => setActionError(apiErrorMessage(e, 'Could not dismiss the selected alerts.')),
    });

    const selectedIds = filtered.filter(a => selected.has(a.id)).map(a => a.id);
    const allSelected = filtered.length > 0 && selectedIds.length === filtered.length;
    const toggle = (id: number) => setSelected(prev => {
        const next = new Set(prev);
        if (next.has(id)) next.delete(id); else next.add(id);
        return next;
    });
    const sortHeader = (key: SortKey, label: string) => (
        <button onClick={() => { setSort(s => ({ key, dir: s.key === key ? (-s.dir as 1 | -1) : -1 })); resetView(); }}
            className="inline-flex items-center gap-1 uppercase hover:text-fg">
            {label}{sort.key === key && (sort.dir === -1 ? <ChevronDown size={12} /> : <ChevronUp size={12} />)}
        </button>
    );
    const openAlert = (id: number) => setViewing(id);

    if (isLoading) {
        return <PageContainer><div className="flex items-center justify-center h-[50vh]">
            <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-line-strong" /></div></PageContainer>;
    }

    return (
        <PageContainer className="space-y-4">
            <div className="flex flex-col lg:flex-row lg:items-center justify-between gap-4">
                <div>
                    <h1 className="text-2xl font-bold text-fg tracking-tight">Alerts</h1>
                    <p className="text-fg-3 text-sm mt-0.5">{total === 0 ? '0 alerts' : `Showing ${(page * PAGE_SIZE + 1).toLocaleString()}–${(page * PAGE_SIZE + filtered.length).toLocaleString()} of ${total.toLocaleString()} alerts`}</p>
                </div>
                <div className="flex flex-wrap items-center gap-2">
                    <div className="relative">
                        <select value={sourceFilter} onChange={e => { setSourceFilter(e.target.value); resetView(); }}
                            className="appearance-none bg-panel border border-line rounded-lg pl-3 pr-8 py-1.5 text-sm text-fg-2 cursor-pointer focus:outline-hidden focus:ring-1 focus:ring-accent">
                            <option value="all">All Sources</option>
                            {sources.map(s => <option key={s} value={s}>{s}</option>)}
                        </select>
                        <ChevronDown size={14} className="absolute right-2.5 top-1/2 -translate-y-1/2 text-muted pointer-events-none" />
                    </div>
                    <div className="relative">
                        <select value={severityFilter} onChange={e => { setSeverityFilter(e.target.value); resetView(); }}
                            className="appearance-none bg-panel border border-line rounded-lg pl-3 pr-8 py-1.5 text-sm text-fg-2 cursor-pointer focus:outline-hidden focus:ring-1 focus:ring-accent">
                            <option value="all">All Severities</option>
                            {SEVERITIES.map(s => <option key={s} value={s}>{s[0].toUpperCase() + s.slice(1)}</option>)}
                        </select>
                        <ChevronDown size={14} className="absolute right-2.5 top-1/2 -translate-y-1/2 text-muted pointer-events-none" />
                    </div>
                    <div className="relative">
                        <select value={statusFilter} onChange={e => { setStatusFilter(e.target.value); resetView(); }}
                            className="appearance-none bg-panel border border-line rounded-lg pl-3 pr-8 py-1.5 text-sm text-fg-2 cursor-pointer focus:outline-hidden focus:ring-1 focus:ring-accent">
                            <option value="all">All Statuses</option>
                            <option value="pending">Pending</option>
                            <option value="promoted">Promoted</option>
                            <option value="dismissed">Dismissed</option>
                        </select>
                        <ChevronDown size={14} className="absolute right-2.5 top-1/2 -translate-y-1/2 text-muted pointer-events-none" />
                    </div>
                    <ColumnsMenu cols={cols} />
                </div>
            </div>

            {actionError && (
                <div role="alert" className="flex items-center justify-between gap-3 px-3 py-2 rounded-lg border border-severity-critical/40 bg-severity-critical/10 text-sm text-severity-critical">
                    <span>{actionError}</span>
                    <button onClick={() => setActionError(null)} className="text-xs text-fg-2 hover:text-fg">Dismiss</button>
                </div>
            )}

            {canAct && selectedIds.length > 0 && (
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
                                {canAct && <input type="checkbox" aria-label="Select all" checked={allSelected}
                                    onChange={() => setSelected(allSelected ? new Set() : new Set(filtered.map(a => a.id)))} />}
                            </th>
                            <th className="px-4 py-3 w-8"></th>
                            {cols.isVisible('severity') && <th className="px-4 py-3 w-28">{sortHeader('severity', 'Severity')}</th>}
                            {cols.isVisible('source') && <th className="px-4 py-3 w-40">Source</th>}
                            <th className="px-4 py-3">Title</th>
                            {cols.isVisible('status') && <th className="px-4 py-3 w-28">Status</th>}
                            {cols.isVisible('created_at') && <th className="px-4 py-3 w-40">{sortHeader('created_at', 'Received')}</th>}
                            <th className="px-4 py-3 w-56">Actions</th>
                        </tr>
                    </thead>
                    <tbody className="divide-y divide-line">
                        {filtered.length === 0 ? (
                            <tr><td colSpan={colSpan} className="px-6 py-12 text-center text-muted">
                                <Search size={32} className="mb-2 opacity-50 mx-auto" />No alerts</td></tr>
                        ) : filtered.map(a => (
                            <Fragment key={a.id}>
                                <tr className="group hover:bg-raised">
                                    <td className="px-4 py-2.5">
                                        {canAct && <input type="checkbox" aria-label={`Select alert ${a.id}`} checked={selected.has(a.id)} onChange={() => toggle(a.id)} />}
                                    </td>
                                    <td className="px-4 py-2.5">
                                        <button onClick={() => setExpanded(expanded === a.id ? null : a.id)} className="text-muted hover:text-fg-2">
                                            {expanded === a.id ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
                                        </button>
                                    </td>
                                    {cols.isVisible('severity') && <td className="px-4 py-2.5"><span className={cn(badge, severityColor[a.severity] || severityColor.info)}>{a.severity}</span></td>}
                                    {cols.isVisible('source') && <td className="px-4 py-2.5"><span className="font-mono text-xs text-fg-2">{a.source}</span></td>}
                                    <td className="px-4 py-2.5 font-medium text-fg">
                                        {a.title}
                                        {a.suppression_rule_id && (
                                            <Link to="/alerts/suppressions" title="Dismissed at ingest by a suppression rule"
                                                className="ml-1.5 text-[10px] font-normal text-muted hover:text-fg-2">rule #{a.suppression_rule_id}</Link>
                                        )}
                                    </td>
                                    {cols.isVisible('status') && <td className="px-4 py-2.5"><span className={cn(badge, statusColor[a.status] || statusColor.dismissed)}>{a.status}</span></td>}
                                    {cols.isVisible('created_at') && <td className="px-4 py-2.5 text-fg-3 text-xs whitespace-nowrap">{new Date(a.created_at).toLocaleString()}</td>}
                                    <td className="px-4 py-2.5">
                                        {!canAct ? (
                                            a.case_id ? <Link to={`/cases/${a.case_id}`} className="text-xs text-accent hover:underline">Case #{a.case_id}</Link>
                                                : <span className="text-xs text-muted">—</span>
                                        ) : a.status === 'pending' ? (
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
                                    <tr><td colSpan={colSpan} className="px-6 py-3 bg-surface">
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

            {total > PAGE_SIZE && (
                <div className="flex items-center justify-end gap-2 text-sm text-fg-3">
                    <span>Page {page + 1} of {Math.ceil(total / PAGE_SIZE)}</span>
                    <button disabled={page === 0} onClick={() => { setPage(p => p - 1); setSelected(new Set()); }}
                        className="px-2 py-1 text-xs font-semibold rounded-sm bg-raised text-fg-2 hover:bg-raised-2 disabled:opacity-50 flex items-center gap-1"><ChevronLeft size={12} />Prev</button>
                    <button disabled={(page + 1) * PAGE_SIZE >= total} onClick={() => { setPage(p => p + 1); setSelected(new Set()); }}
                        className="px-2 py-1 text-xs font-semibold rounded-sm bg-raised text-fg-2 hover:bg-raised-2 disabled:opacity-50 flex items-center gap-1">Next<ChevronRight size={12} /></button>
                </div>
            )}

            {viewing !== null && <AlertDetailModal alertId={viewing} onOpen={setViewing} onClose={() => setViewing(null)} />}
            {promoting && <PromoteAlertModal alert={promoting} onClose={() => setPromoting(null)} />}
            {bulkPromote && selectedIds.length > 0 && (
                <PromoteAlertModal alert={filtered.find(a => a.id === selectedIds[0])!} ids={selectedIds}
                    onClose={() => setBulkPromote(false)} />
            )}
        </PageContainer>
    );
}
