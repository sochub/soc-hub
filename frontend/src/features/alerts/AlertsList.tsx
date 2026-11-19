import { Fragment, useEffect, useState } from 'react';
import { useQuery, useMutation, useQueryClient, keepPreviousData } from '@tanstack/react-query';
import { api } from '../../api/client';
import { ChevronDown, ChevronLeft, ChevronRight, ChevronUp, X, CheckCircle, BellOff } from 'lucide-react';
import { Link, useSearchParams } from 'react-router-dom';
import PromoteAlertModal from './PromoteAlertModal';
import { AlertRuns } from '../automations/CaseAutomation';
import { Button, Select, SeverityStamp, Stamp, Skeleton, ErrorState, EmptyState, buttonClass, type Tone } from '../../components/ui';
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

const SEVERITIES = ['critical', 'high', 'medium', 'low', 'info'];
const alertStatusTone = (s: string): Tone => (s === 'pending' ? 'medium' : s === 'promoted' ? 'ok' : 'neutral');
type SortKey = 'severity' | 'created_at';
const PAGE_SIZES = [10, 40, 100] as const;

function SimilarAlerts({ alertId, onOpen }: { alertId: number; onOpen: (id: number) => void }) {
    const { data } = useQuery<Alert[]>({
        queryKey: ['alerts', alertId, 'similar'],
        queryFn: async () => (await api.get(`/alerts/${alertId}/similar`)).data,
    });
    if (!data?.length) return null;
    return (
        <div className="mb-3">
            <h3 className="label-caps mb-1">Similar alerts <span className="num">({data.length})</span></h3>
            <ul className="space-y-1">
                {data.map(s => (
                    <li key={s.id} className="text-xs flex items-center gap-2">
                        <SeverityStamp severity={s.severity} />
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
                <Skeleton rows={4} />
            ) : isError || !a ? (
                <p className="text-sm text-severity-critical">{apiErrorMessage(error, 'Could not load this alert.')}</p>
            ) : (
                <div className="space-y-3">
                    <div className="flex flex-wrap items-center gap-2 text-sm">
                        <SeverityStamp severity={a.severity} />
                        <Stamp tone={alertStatusTone(a.status)}>{a.status}</Stamp>
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
    // Filters, page and page size live in the URL so dashboard widgets can deep-link a pre-filtered view.
    const [sp, setSp] = useSearchParams();
    const statusFilter = sp.get('status') ?? 'pending';
    const severityFilter = sp.get('severity') ?? 'all';
    const sourceFilter = sp.get('source') ?? 'all';
    const sizeParam = Number(sp.get('size'));
    const PAGE_SIZE = (PAGE_SIZES as readonly number[]).includes(sizeParam) ? sizeParam : 40;
    const page = Math.max(0, Number(sp.get('page') ?? 1) - 1 || 0);
    // Writes one or more params; 'all' (and the defaults) drop the key so URLs stay short. Any change but page returns to page 1.
    const setParams = (next: Record<string, string | number | null>) => setSp(prev => {
        const p = new URLSearchParams(prev);
        if (!('page' in next)) p.delete('page');
        for (const [k, v] of Object.entries(next)) {
            const dflt = k === 'status' ? 'pending' : k === 'size' ? '40' : k === 'page' ? '1' : 'all';
            if (v === null || String(v) === dflt) p.delete(k); else p.set(k, String(v));
        }
        return p;
    }, { replace: true });
    const setPage = (n: number) => setParams({ page: n + 1 });
    const [sort, setSort] = useState<{ key: SortKey; dir: 1 | -1 }>({ key: 'created_at', dir: -1 });
    const [selected, setSelected] = useState<Set<number>>(new Set());
    const [bulkPromote, setBulkPromote] = useState(false);
    const [expanded, setExpanded] = useState<number | null>(null);
    const [promoting, setPromoting] = useState<Alert | null>(null);
    const [viewing, setViewing] = useState<number | null>(null);
    const [actionError, setActionError] = useState<string | null>(null);
    const canAct = useCanRun();  // promote/dismiss/suppress need analyst or admin
    const cols = useAlertColumns();
    const colSpan = FIXED_COLUMN_COUNT + cols.visibleCount;

    // Server sort keys: 'severity' = most severe first, '-severity' = least severe first.
    const sortParam = sort.key === 'severity' ? (sort.dir === -1 ? 'severity' : '-severity') : (sort.dir === -1 ? '-created_at' : 'created_at');
    const params = {
        skip: page * PAGE_SIZE, limit: PAGE_SIZE, sort: sortParam,
        ...(statusFilter !== 'all' && { status: statusFilter }),
        ...(severityFilter !== 'all' && { severity: severityFilter }),
        ...(sourceFilter !== 'all' && { source: sourceFilter }),
    };
    const { data: result, isLoading, isError, refetch } = useQuery({
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
    const resetView = () => setSelected(new Set());
    // If the current page emptied (e.g. last alert dismissed), step back to the last page that has rows.
    const lastPage = result ? Math.max(0, Math.ceil(result.total / PAGE_SIZE) - 1) : 0;
    const overshot = !!result && result.items.length === 0 && result.total > 0 && page > lastPage;
    useEffect(() => { if (overshot) setPage(lastPage); });

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
        <button onClick={() => { setSort(s => ({ key, dir: s.key === key ? (-s.dir as 1 | -1) : -1 })); setParams({}); resetView(); }}
            // uppercase: preflight resets text-transform on buttons, so sortable headers would lose the caps.
            className="inline-flex items-center gap-1 uppercase tracking-[inherit] hover:text-fg">
            {label}{sort.key === key && (sort.dir === -1 ? <ChevronDown size={12} /> : <ChevronUp size={12} />)}
        </button>
    );
    const openAlert = (id: number) => setViewing(id);

    const filtersActive = statusFilter !== 'pending' || severityFilter !== 'all' || sourceFilter !== 'all';
    const clearFilters = () => { setParams({ status: null, severity: null, source: null }); resetView(); };
    const suppressLink = (id: number) => (
        <Link to={`/alerts/suppressions?from_alert=${id}`} title="Create a suppression rule pre-filled from this alert"
            className={buttonClass('ghost', 'sm')}><BellOff size={12} aria-hidden />Suppress similar</Link>
    );

    // Viewers see the linked case only; promote, dismiss and suppress need analyst or admin.
    const rowActions = (a: Alert) => !canAct ? (
        a.case_id ? <Link to={`/cases/${a.case_id}`} className="text-xs text-accent hover:underline">Case #{a.case_id}</Link>
            : <span className="text-xs text-fg-3">—</span>
    ) : a.status === 'pending' ? (
        <div className="flex flex-wrap sm:flex-nowrap items-center gap-1">
            <Button size="sm" onClick={() => setPromoting(a)}><CheckCircle size={12} aria-hidden />Promote</Button>
            <Button size="sm" variant="ghost" disabled={dismiss.isPending && dismiss.variables === a.id} onClick={() => dismiss.mutate(a.id)}><X size={12} aria-hidden />Dismiss</Button>
            {suppressLink(a.id)}
        </div>
    ) : a.case_id ? (
        <Link to={`/cases/${a.case_id}`} className="text-xs text-accent hover:underline">Case #{a.case_id}</Link>
    ) : !a.suppression_rule_id ? suppressLink(a.id) : <span className="text-xs text-muted">—</span>;

    return (
        <div className="p-4 space-y-4">
            <div className="flex flex-wrap items-center gap-2">
                <p className="mr-auto text-sm text-fg-3 num">{isLoading ? '\u00a0' : total === 0 ? '0 alerts' : `Showing ${(page * PAGE_SIZE + 1).toLocaleString()}–${(page * PAGE_SIZE + filtered.length).toLocaleString()} of ${total.toLocaleString()} alerts`}</p>
                <Select inputSize="sm" className="w-auto" aria-label="Source" value={sourceFilter} onChange={e => { setParams({ source: e.target.value }); resetView(); }}>
                    <option value="all">All sources</option>
                    {sources.map(s => <option key={s} value={s}>{s}</option>)}
                </Select>
                <Select inputSize="sm" className="w-auto" aria-label="Severity" value={severityFilter} onChange={e => { setParams({ severity: e.target.value }); resetView(); }}>
                    <option value="all">All severities</option>
                    {SEVERITIES.map(s => <option key={s} value={s}>{s[0].toUpperCase() + s.slice(1)}</option>)}
                </Select>
                <Select inputSize="sm" className="w-auto" aria-label="Status" value={statusFilter} onChange={e => { setParams({ status: e.target.value }); resetView(); }}>
                    <option value="all">All statuses</option>
                    <option value="pending">Pending</option>
                    <option value="promoted">Promoted</option>
                    <option value="dismissed">Dismissed</option>
                </Select>
                <ColumnsMenu cols={cols} />
            </div>

            {canAct && selectedIds.length > 0 && (
                <div className="flex flex-wrap items-center gap-2 px-3 py-2 rounded-md border border-line bg-raised text-sm">
                    <span className="text-fg-2 num">{selectedIds.length} selected</span>
                    <Button size="sm" onClick={() => setBulkPromote(true)}><CheckCircle size={12} aria-hidden />Promote selected…</Button>
                    <Button size="sm" variant="secondary" disabled={bulkDismiss.isPending} onClick={() => bulkDismiss.mutate(selectedIds)}><X size={12} aria-hidden />Dismiss selected</Button>
                    <Button size="sm" variant="ghost" className="ml-auto" onClick={() => setSelected(new Set())}>Clear</Button>
                </div>
            )}

            {actionError && (
                <p role="alert" className="flex items-center gap-2 text-sm text-severity-critical">
                    {actionError}
                    <button type="button" onClick={() => setActionError(null)} aria-label="Dismiss error" className="text-fg-3 hover:text-fg"><X size={14} /></button>
                </p>
            )}

            {isError ? <ErrorState message="Could not load alerts." onRetry={() => refetch()} /> : isLoading ? <Skeleton rows={8} /> : (
            <div className="-mx-4 overflow-x-auto border-y border-line">
                <table className="w-full text-sm text-left">
                    <thead className="border-b border-line bg-surface">
                        <tr className="label-caps">
                            <th className="px-3 py-2 w-8">
                                {canAct && <input type="checkbox" aria-label="Select all" checked={allSelected}
                                    onChange={() => setSelected(allSelected ? new Set() : new Set(filtered.map(a => a.id)))} />}
                            </th>
                            <th className="px-3 py-2 w-8"><span className="sr-only">Details</span></th>
                            {cols.isVisible('severity') && <th className="hidden sm:table-cell px-3 py-2 w-28">{sortHeader('severity', 'Severity')}</th>}
                            {/* Below sm the title needs the width: source hides here and shows in the expanded details instead. */}
                            {cols.isVisible('source') && <th className="hidden sm:table-cell px-3 py-2 w-40">Source</th>}
                            <th className="px-3 py-2 sm:min-w-56">Title</th>
                            {cols.isVisible('status') && <th className="hidden sm:table-cell px-3 py-2 w-28">Status</th>}
                            {cols.isVisible('created_at') && <th className="hidden sm:table-cell px-3 py-2 w-40">{sortHeader('created_at', 'Received')}</th>}
                            <th className="hidden sm:table-cell px-3 py-2 w-72">Actions</th>
                        </tr>
                    </thead>
                    <tbody className="divide-y divide-line">
                        {filtered.length === 0 ? (
                            <tr><td colSpan={colSpan}>
                                {filtersActive
                                    ? <EmptyState title="No alerts match these filters" action={<Button size="sm" variant="secondary" onClick={clearFilters}>Clear filters</Button>} />
                                    : <EmptyState title="No pending alerts" body="Alerts sent to your webhooks land here for triage." />}
                            </td></tr>
                        ) : filtered.map(a => (
                            <Fragment key={a.id}>
                                <tr className="group hover:bg-raised">
                                    <td className="px-3 py-2.5">
                                        {canAct && <input type="checkbox" aria-label={`Select alert ${a.id}`} checked={selected.has(a.id)} onChange={() => toggle(a.id)} />}
                                    </td>
                                    <td className="px-3 py-2.5">
                                        <button onClick={() => setExpanded(expanded === a.id ? null : a.id)} className="text-muted hover:text-fg-2"
                                            aria-expanded={expanded === a.id} aria-label={`${expanded === a.id ? 'Hide' : 'Show'} details for alert ${a.id}`}>
                                            {expanded === a.id ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
                                        </button>
                                    </td>
                                    {cols.isVisible('severity') && <td className="hidden sm:table-cell px-3 py-2.5"><SeverityStamp severity={a.severity} /></td>}
                                    {cols.isVisible('source') && <td className="hidden sm:table-cell px-3 py-2.5"><span className="text-xs text-fg-2">{a.source}</span></td>}
                                    <td className="px-3 py-2.5 font-medium text-fg">
                                        {/* Phones: severity rides above the title instead of taking a column. */}
                                        <SeverityStamp severity={a.severity} className="sm:hidden mb-1.5" />
                                        <span className="block sm:inline">{a.title}</span>
                                        {a.suppression_rule_id && (
                                            <Link to="/alerts/suppressions" title="Dismissed at ingest by a suppression rule"
                                                className="ml-1.5 text-xs font-normal text-muted hover:text-fg-2">rule #{a.suppression_rule_id}</Link>
                                        )}
                                        {/* Phones: the actions column would scroll off-screen, so actions sit under the title. */}
                                        <div className="sm:hidden mt-2 font-normal">{rowActions(a)}</div>
                                    </td>
                                    {cols.isVisible('status') && <td className="hidden sm:table-cell px-3 py-2.5"><Stamp tone={alertStatusTone(a.status)}>{a.status}</Stamp></td>}
                                    {cols.isVisible('created_at') && <td className="hidden sm:table-cell px-3 py-2.5 text-fg-3 text-xs whitespace-nowrap num">{new Date(a.created_at).toLocaleString()}</td>}
                                    <td className="hidden sm:table-cell px-3 py-2.5">{rowActions(a)}</td>
                                </tr>
                                {expanded === a.id && (
                                    <tr><td colSpan={colSpan} className="px-4 py-3 bg-surface">
                                        <SimilarAlerts alertId={a.id} onOpen={openAlert} />
                                        <AlertRuns alertId={a.id} />
                                        <p className="sm:hidden mb-2 text-xs text-fg-3">Source: <span className="text-fg-2">{a.source}</span></p>
                                        <pre className="text-xs text-fg-2 overflow-x-auto">{JSON.stringify(a.payload, null, 2)}</pre>
                                    </td></tr>
                                )}
                            </Fragment>
                        ))}
                    </tbody>
                </table>
            </div>
            )}

            {total > PAGE_SIZES[0] && (
                <nav aria-label="Pagination" className="flex flex-wrap items-center justify-end gap-2 text-sm text-fg-3">
                    <label className="mr-auto inline-flex items-center gap-2">Per page
                        <Select inputSize="sm" className="w-auto" value={PAGE_SIZE} onChange={e => { setParams({ size: e.target.value }); resetView(); }}>
                            {PAGE_SIZES.map(n => <option key={n} value={n}>{n}</option>)}
                        </Select>
                    </label>
                    <span className="num">Page {page + 1} of {Math.max(1, Math.ceil(total / PAGE_SIZE))}</span>
                    <Button size="sm" variant="secondary" disabled={page === 0} onClick={() => { setPage(page - 1); resetView(); }}><ChevronLeft size={12} aria-hidden />Prev</Button>
                    <Button size="sm" variant="secondary" disabled={(page + 1) * PAGE_SIZE >= total} onClick={() => { setPage(page + 1); resetView(); }}>Next<ChevronRight size={12} aria-hidden /></Button>
                </nav>
            )}

            {viewing !== null && <AlertDetailModal alertId={viewing} onOpen={setViewing} onClose={() => setViewing(null)} />}
            {promoting && <PromoteAlertModal alert={promoting} onClose={() => setPromoting(null)} />}
            {bulkPromote && selectedIds.length > 0 && (
                <PromoteAlertModal alert={filtered.find(a => a.id === selectedIds[0])!} ids={selectedIds}
                    onClose={() => setBulkPromote(false)} />
            )}
        </div>
    );
}
