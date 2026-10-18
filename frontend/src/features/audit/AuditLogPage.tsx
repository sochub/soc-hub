import { useEffect, useMemo, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { useInfiniteQuery, useQuery } from '@tanstack/react-query';
import { ChevronDown, ChevronRight, Download } from 'lucide-react';
import { api } from '../../api/client';
import PageContainer from '../../components/layout/PageContainer';
import { Badge, Button, Input, Label } from '../../components/ui';
import type { AuditFacets, AuditItem, AuditPage, User } from '../../types';

const CATEGORIES = [
    { key: '', label: 'All' }, { key: 'change', label: 'Changes' }, { key: 'security', label: 'Security' },
    { key: 'read', label: 'Reads' }, { key: 'write', label: 'Writes' },
] as const;
const PRESETS = [{ key: '1', label: '24h' }, { key: '7', label: '7d' }, { key: '30', label: '30d' }] as const;

const SEARCH_DEBOUNCE_MS = 300;

function sinceDays(days: string): string | undefined {
    if (!days || days === 'custom') return undefined;
    return new Date(Date.now() - Number(days) * 86_400_000).toISOString();
}

/** A `<input type="date">` value (YYYY-MM-DD) as an ISO instant: start or end of that day in local time. */
function localDayIso(day: string, end: boolean): string | undefined {
    if (!day) return undefined;
    const d = new Date(`${day}T${end ? '23:59:59.999' : '00:00:00'}`);
    return Number.isNaN(d.getTime()) ? undefined : d.toISOString();
}

function actorLabel(i: AuditItem): string {
    if (i.user) return i.user.full_name || i.user.email;
    if (i.user_email) return `${i.user_email} (deleted)`;
    return i.actor || 'anonymous';
}

export default function AuditLogPage() {
    const { data: me } = useQuery({
        queryKey: ['currentUser'], queryFn: async () => (await api.get('/users/me')).data as User, staleTime: 300_000,
    });
    const [category, setCategory] = useState('');
    const [userId, setUserId] = useState('');
    const [actionInput, setActionInput] = useState('');
    const [action, setAction] = useState('');  // debounced copy of actionInput, used in the query
    const actionTimer = useRef<number | undefined>(undefined);
    useEffect(() => () => window.clearTimeout(actionTimer.current), []);
    const onActionChange = (v: string) => {
        setActionInput(v);
        window.clearTimeout(actionTimer.current);
        actionTimer.current = window.setTimeout(() => setAction(v), SEARCH_DEBOUNCE_MS);
    };
    const [outcome, setOutcome] = useState('');
    const [days, setDays] = useState('7');
    const [customFrom, setCustomFrom] = useState('');
    const [customTo, setCustomTo] = useState('');
    const [platform, setPlatform] = useState(false);
    const [open, setOpen] = useState<number | null>(null);
    const [exportError, setExportError] = useState<string | null>(null);
    const [exporting, setExporting] = useState(false);

    const params = useMemo(() => {
        const p: Record<string, string> = {};
        if (category) p.category = category;
        if (userId) p.user_id = userId;
        if (action.trim()) p.action = action.trim();
        if (outcome) p.outcome = outcome;
        const from = days === 'custom' ? localDayIso(customFrom, false) : sinceDays(days);
        if (from) p.from = from;
        const to = days === 'custom' ? localDayIso(customTo, true) : undefined;
        if (to) p.to = to;
        if (platform) p.scope = 'platform';
        return p;
    }, [category, userId, action, outcome, days, customFrom, customTo, platform]);

    const facets = useQuery({
        queryKey: ['audit-facets', platform],
        queryFn: async () => (await api.get('/audit-logs/facets', { params: platform ? { scope: 'platform' } : {} })).data as AuditFacets,
        refetchOnWindowFocus: false,
    });
    const log = useInfiniteQuery({
        queryKey: ['audit-log', params],
        initialPageParam: undefined as number | undefined,
        queryFn: async ({ pageParam }) => (await api.get('/audit-logs', {
            params: { ...params, ...(pageParam ? { before: pageParam } : {}) },
        })).data as AuditPage,
        getNextPageParam: (last) => last.next_before ?? undefined,
        refetchOnWindowFocus: false,
    });
    const items = log.data?.pages.flatMap((p) => p.items) ?? [];

    const exportCsv = async () => {
        setExportError(null);
        setExporting(true);
        try {
            const resp = await api.get('/audit-logs/export', { params: { ...params, format: 'csv' }, responseType: 'blob' });
            const name = /filename="([^"]+)"/.exec(resp.headers['content-disposition'] ?? '')?.[1] ?? 'audit.csv';
            const url = URL.createObjectURL(resp.data as Blob);
            const a = document.createElement('a');
            a.href = url; a.download = name; a.click();
            URL.revokeObjectURL(url);
        } catch {
            setExportError('Could not export the audit log.');
        } finally {
            setExporting(false);
        }
    };

    return (
        <PageContainer width="wide" className="space-y-4">
            <div className="flex flex-wrap items-end gap-3">
                <div className="flex gap-1" role="group" aria-label="Category">
                    {CATEGORIES.map((c) => (
                        <button key={c.key} type="button" onClick={() => setCategory(c.key)} aria-pressed={category === c.key}
                            className={`px-3 h-8 rounded-md text-xs border ${category === c.key ? 'border-accent text-accent' : 'border-line text-fg-2 hover:text-fg'}`}>
                            {c.label}
                        </button>
                    ))}
                </div>
                <div>
                    <Label htmlFor="audit-user">User</Label>
                    <select id="audit-user" value={userId} onChange={(e) => setUserId(e.target.value)}
                        className="h-8 bg-raised border border-line rounded-md px-2 text-xs text-fg">
                        <option value="">Anyone</option>
                        {facets.data?.users.map((u) => <option key={u.id} value={u.id}>{u.full_name || u.email}</option>)}
                    </select>
                </div>
                <div className="min-w-[14rem] flex-1">
                    <Label htmlFor="audit-action">Action</Label>
                    <Input id="audit-action" list="audit-actions" value={actionInput} onChange={(e) => onActionChange(e.target.value)}
                        placeholder="e.g. login., PUT /api/v1/cases" />
                    <datalist id="audit-actions">{facets.data?.actions.map((a) => <option key={a} value={a} />)}</datalist>
                </div>
                <div>
                    <Label htmlFor="audit-outcome">Outcome</Label>
                    <select id="audit-outcome" value={outcome} onChange={(e) => setOutcome(e.target.value)}
                        className="h-8 bg-raised border border-line rounded-md px-2 text-xs text-fg">
                        <option value="">Any</option><option value="success">Success</option>
                        <option value="denied">Denied</option><option value="error">Error</option>
                    </select>
                </div>
                <div className="flex gap-1" role="group" aria-label="Time range">
                    {PRESETS.map((p) => (
                        <button key={p.key} type="button" onClick={() => setDays(p.key)} aria-pressed={days === p.key}
                            className={`px-2 h-8 rounded-md text-xs border ${days === p.key ? 'border-accent text-accent' : 'border-line text-fg-2'}`}>
                            {p.label}
                        </button>
                    ))}
                    <button type="button" onClick={() => setDays('')} aria-pressed={days === ''}
                        className={`px-2 h-8 rounded-md text-xs border ${days === '' ? 'border-accent text-accent' : 'border-line text-fg-2'}`}>All</button>
                    <button type="button" onClick={() => setDays('custom')} aria-pressed={days === 'custom'}
                        className={`px-2 h-8 rounded-md text-xs border ${days === 'custom' ? 'border-accent text-accent' : 'border-line text-fg-2'}`}>Custom</button>
                </div>
                {days === 'custom' && (
                    <div className="flex items-end gap-2">
                        <div>
                            <Label htmlFor="audit-from">From</Label>
                            <input id="audit-from" type="date" value={customFrom} max={customTo || undefined}
                                onChange={(e) => setCustomFrom(e.target.value)}
                                className="h-8 bg-raised border border-line rounded-md px-2 text-xs text-fg" />
                        </div>
                        <div>
                            <Label htmlFor="audit-to">To</Label>
                            <input id="audit-to" type="date" value={customTo} min={customFrom || undefined}
                                onChange={(e) => setCustomTo(e.target.value)}
                                className="h-8 bg-raised border border-line rounded-md px-2 text-xs text-fg" />
                        </div>
                    </div>
                )}
                {me?.is_super_admin && (
                    <label className="flex items-center gap-2 text-xs text-fg-2">
                        <input type="checkbox" checked={platform} onChange={(e) => setPlatform(e.target.checked)} /> Platform events
                    </label>
                )}
                <Button size="sm" variant="secondary" onClick={exportCsv} disabled={exporting}><Download size={13} /> Export CSV</Button>
            </div>
            {exportError && <p role="alert" className="text-sm text-severity-critical">{exportError}</p>}
            {log.isError && <p role="alert" className="text-sm text-severity-critical">Could not load the audit log.</p>}

            <div className="border border-line rounded-lg overflow-x-auto">
                <table className="w-full text-xs">
                    <thead className="text-fg-3 text-left">
                        <tr className="border-b border-line">
                            <th className="w-6" /><th className="px-3 py-2">Time</th><th className="px-3 py-2">Actor</th>
                            <th className="px-3 py-2">Category</th><th className="px-3 py-2">Action</th>
                            <th className="px-3 py-2">Entity</th><th className="px-3 py-2">Outcome</th><th className="px-3 py-2">IP</th>
                        </tr>
                    </thead>
                    <tbody>
                        {items.map((i) => (
                            <AuditRow key={i.id} item={i} platform={platform} open={open === i.id} onToggle={() => setOpen(open === i.id ? null : i.id)} />
                        ))}
                    </tbody>
                </table>
                {!log.isLoading && items.length === 0 && <p className="p-4 text-sm text-fg-3">No activity matches these filters.</p>}
            </div>
            {log.hasNextPage && (
                <Button size="sm" variant="ghost" onClick={() => log.fetchNextPage()} disabled={log.isFetchingNextPage}>Load more</Button>
            )}
        </PageContainer>
    );
}

function AuditRow({ item: i, open, platform, onToggle }: { item: AuditItem; open: boolean; platform: boolean; onToggle: () => void }) {
    const related = useQuery({
        queryKey: ['audit-related', platform, i.request_id],
        enabled: open && !!i.request_id,
        refetchOnWindowFocus: false,
        queryFn: async () => (await api.get('/audit-logs', { params: { request_id: i.request_id, ...(platform ? { scope: 'platform' } : {}) } })).data as AuditPage,
    });
    const others = related.data?.items.filter((r) => r.id !== i.id) ?? [];
    const outcomeVariant = i.outcome === 'denied' || i.outcome === 'error' ? 'critical' : 'default';
    return (
        <>
            <tr className="border-b border-line hover:bg-raised cursor-pointer" onClick={onToggle}>
                <td className="pl-2 text-fg-3">
                    <button type="button" aria-expanded={open} aria-label="Show details"
                        onClick={(e) => { e.stopPropagation(); onToggle(); }}>
                        {open ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
                    </button>
                </td>
                <td className="px-3 py-2 whitespace-nowrap" title={i.created_at}>{new Date(i.created_at).toLocaleString()}</td>
                <td className="px-3 py-2">
                    {i.user || i.user_email ? actorLabel(i)
                        : <span className="px-1.5 py-0.5 rounded bg-amber-500/15 text-amber-300">{actorLabel(i)}</span>}
                </td>
                <td className="px-3 py-2"><Badge>{i.category}</Badge></td>
                <td className="px-3 py-2 font-mono">{i.action}</td>
                <td className="px-3 py-2">
                    {i.entity_type === 'case' && i.entity_id
                        ? <Link to={`/cases/${i.entity_id}`} onClick={(e) => e.stopPropagation()} className="text-accent">case #{i.entity_id}</Link>
                        : i.entity_type ? `${i.entity_type}${i.entity_id ? ` #${i.entity_id}` : ''}` : '—'}
                </td>
                <td className="px-3 py-2">{i.outcome ? <Badge variant={outcomeVariant}>{i.outcome}</Badge> : '—'}</td>
                <td className="px-3 py-2 font-mono">{i.ip ?? '—'}</td>
            </tr>
            {open && (
                <tr className="border-b border-line bg-panel">
                    <td />
                    <td colSpan={7} className="px-3 py-3 space-y-2">
                        <div className="text-fg-3 space-x-3">
                            {i.method && <span>{i.method} {i.route ?? '(unmatched)'}</span>}
                            {i.status_code != null && <span>status {i.status_code}</span>}
                            {i.duration_ms != null && <span>{i.duration_ms} ms</span>}
                            {i.request_id && <span className="font-mono">request {i.request_id}</span>}
                        </div>
                        {i.user_agent && <div className="text-fg-3 break-all">{i.user_agent}</div>}
                        {i.changes && <pre className="bg-raised border border-line rounded p-2 overflow-x-auto text-fg-2">{JSON.stringify(i.changes, null, 2)}</pre>}
                        {others.length > 0 && (
                            <div>
                                <div className="text-fg-3 mb-1">Related in the same request</div>
                                <ul className="space-y-1">
                                    {others.map((r) => (
                                        <li key={r.id} className="font-mono text-fg-2">{r.category} · {r.action}</li>
                                    ))}
                                </ul>
                            </div>
                        )}
                    </td>
                </tr>
            )}
        </>
    );
}
