import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '../../api/client';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import { Plus, Search, LayoutList, Columns3, X } from 'lucide-react';
import { cn } from '../../lib/utils';
import { useState } from 'react';
import NewCaseModal from './NewCaseModal';
import CasesBoard from './CasesBoard';
import { SLABadge } from './SLABadge';
import type { SLAStatus } from '../../types';
import PageContainer from '../../components/layout/PageContainer';
import { Button, EmptyState, ErrorState, Input, PageHeader, Select, SeverityStamp, Skeleton, StatusStamp, SEVERITY_ORDER } from '../../components/ui';
import { CASE_STATUSES } from './statuses';
import { outcomeLabel } from './outcomes';

interface Case {
    id: number;
    title: string;
    description: string;
    severity: 'info' | 'low' | 'medium' | 'high' | 'critical';
    status: string;
    tags: string[];
    source: string;
    created_at: string;
    updated_at: string;
    sla_overall_status?: SLAStatus | null;
    owner_id?: number | null;
    outcome?: string | null;
}

const CLOSED = ['resolved', 'closed'];
// Filters live in the URL so they survive back navigation and can be linked to (Dashboard tiles do).
const FILTER_KEYS = ['q', 'status', 'severity', 'tag', 'sla', 'owner', 'age', 'outcome', 'from', 'to'] as const;
// Age buckets match the dashboards' "open case aging" (backend stats._AGING): label -> [min, max) days.
const AGE_BUCKETS: Record<string, [number, number]> = { '<1d': [0, 1], '1-3d': [1, 3], '3-7d': [3, 7], '7-30d': [7, 30], '>30d': [30, Infinity] };
type FilterKey = typeof FILTER_KEYS[number];

export default function CasesList() {
    const navigate = useNavigate();
    const [searchParams, setSearchParams] = useSearchParams();
    const [modalOpen, setShowNewCaseModal] = useState(false);
    const showNewCaseModal = modalOpen || searchParams.get('new') === '1'; // top bar / ⌘K "New case" deep link
    const searchQuery = searchParams.get('q') ?? '';
    const severityFilter = searchParams.get('severity') || 'all';
    const statusFilter = searchParams.get('status') || 'all';
    const tagFilter = searchParams.get('tag') || 'all';
    const slaFilter = searchParams.get('sla') || 'all';
    // Drill-down filters set by dashboard widgets; shown as removable chips, not dropdowns.
    const [now] = useState(Date.now); // age filter reference time, fixed per visit
    const ownerFilter = searchParams.get('owner');
    const ageFilter = searchParams.get('age');
    const outcomeFilter = searchParams.get('outcome');
    const fromFilter = searchParams.get('from');
    const toFilter = searchParams.get('to');
    const setFilter = (key: FilterKey, value: string) =>
        setSearchParams(p => { if (!value || value === 'all') p.delete(key); else p.set(key, value); return p; }, { replace: true });
    const hasFilters = FILTER_KEYS.some(k => searchParams.has(k));
    const clearFilters = () => setSearchParams(p => { FILTER_KEYS.forEach(k => p.delete(k)); return p; }, { replace: true });
    // URL wins (shareable links), else the last view this browser used
    const storedView = (() => { try { return localStorage.getItem('cases:view'); } catch { return null; } })();
    const view: 'table' | 'board' = (searchParams.get('view') ?? storedView) === 'board' ? 'board' : 'table';
    const changeView = (v: 'table' | 'board') => {
        setSearchParams(p => { p.set('view', v); return p; }, { replace: true });
        try { localStorage.setItem('cases:view', v); } catch { /* storage unavailable */ }
    };
    const queryClient = useQueryClient();

    const { data: cases, isLoading, isError, refetch } = useQuery<Case[]>({
        queryKey: ['cases'],
        queryFn: async () => {
            const response = await api.get('/cases/', { params: { limit: 500 } });
            return response.data;
        }
    });

    const { data: availableTags } = useQuery<string[]>({
        queryKey: ['cases', 'tags'],
        queryFn: async () => {
            const response = await api.get('/cases/tags');
            return response.data;
        }
    });

    const createCaseMutation = useMutation({
        mutationFn: async (data: { title: string; description: string; severity: string; status: string; tags: string[]; source: string; artifacts: any[]; playbook_template_id?: string; first_seen_at?: string }) => {
            const response = await api.post('/cases/', {
                title: data.title,
                description: data.description,
                severity: data.severity,
                status: data.status,
                tags: data.tags,
                source: data.source,
                ...(data.first_seen_at ? { first_seen_at: data.first_seen_at } : {}),
            });

            if (data.artifacts && data.artifacts.length > 0) {
                await Promise.all(
                    data.artifacts.map(artifact =>
                        api.post('/artifacts/', {
                            case_id: response.data.id,
                            artifact_type: artifact.type,
                            value: artifact.value,
                            metadata: artifact.enrichment ? { enrichment: artifact.enrichment } : {}
                        })
                    )
                );
            }

            if (data.playbook_template_id) {
                await api.post(`/cases/${response.data.id}/apply-playbook/${data.playbook_template_id}`);
            }

            return response.data;
        },
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['cases'] });
            closeModal();
        },
    });

    const closeModal = () => {
        setShowNewCaseModal(false);
        if (searchParams.has('new')) setSearchParams(p => { p.delete('new'); return p; }, { replace: true });
    };

    const { data: people } = useQuery<{ id: number; name: string; email: string }[]>({
        queryKey: ['assignable'],
        queryFn: async () => (await api.get('/users/assignable')).data,
        enabled: !!ownerFilter && ownerFilter !== 'none',
    });
    const ownerName = (id: string) => { const u = people?.find(p => String(p.id) === id); return u ? (u.name || u.email) : `#${id}`; };
    const drillChips = ([
        ['owner', ownerFilter && (ownerFilter === 'none' ? 'Unassigned' : `Owner: ${ownerName(ownerFilter)}`)],
        ['age', ageFilter && `Open for ${ageFilter}`],
        ['outcome', outcomeFilter && `Outcome: ${outcomeFilter === 'not_set' ? 'Not set' : outcomeLabel(outcomeFilter)}`],
        ['from', (fromFilter || toFilter) && `Created ${fromFilter ?? '…'} – ${toFilter ?? '…'}`],
    ] as const).filter((c): c is readonly [typeof c[0], string] => !!c[1]);

    const q = searchQuery.trim().toLowerCase().replace(/^#/, '');
    const slaValues = slaFilter.split(',');
    const filteredCases = cases?.filter(c => {
        const matchesSearch = !q || (
            c.title.toLowerCase().includes(q) ||
            (c.description ?? '').toLowerCase().includes(q) ||
            String(c.id).includes(q)
        );
        const matchesSeverity = severityFilter === 'all' || c.severity === severityFilter;
        const matchesStatus = statusFilter === 'all'
            || (statusFilter === 'active' ? !CLOSED.includes(c.status) : c.status === statusFilter);
        const matchesTag = tagFilter === 'all' || (c.tags && c.tags.includes(tagFilter));
        const matchesSla = slaFilter === 'all' || slaValues.includes(c.sla_overall_status ?? '');
        const matchesOwner = !ownerFilter || (ownerFilter === 'none' ? c.owner_id == null : String(c.owner_id) === ownerFilter);
        const ageDays = (now - +new Date(c.created_at)) / 86_400_000;
        const bucket = ageFilter ? AGE_BUCKETS[ageFilter] : undefined;
        const matchesAge = !bucket || (ageDays >= bucket[0] && ageDays < bucket[1]);
        const matchesOutcome = !outcomeFilter || (c.outcome ?? 'not_set') === outcomeFilter;
        const day = c.created_at.slice(0, 10); // UTC day, same as the dashboards
        const matchesRange = (!fromFilter || day >= fromFilter) && (!toFilter || day <= toFilter);

        return matchesSearch && matchesSeverity && matchesStatus && matchesTag && matchesSla
            && matchesOwner && matchesAge && matchesOutcome && matchesRange;
    }) || [];

    const fmtDate = (d: string) => new Date(d).toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' });

    return (
        <PageContainer>
            <PageHeader
                title="Cases"
                count={cases ? filteredCases.length : undefined}
                className="mb-0"
            />

            <div className="flex flex-wrap items-center gap-2" role="search">
                <div className="relative">
                    <Search size={14} aria-hidden className="absolute left-2.5 top-1/2 -translate-y-1/2 text-fg-3 pointer-events-none" />
                    <Input
                        type="search"
                        inputSize="sm"
                        aria-label="Search cases"
                        value={searchQuery}
                        onChange={(e) => setFilter('q', e.target.value)}
                        placeholder="Search by #ID or title"
                        className="pl-8 w-52 lg:w-64"
                    />
                </div>
                <Select inputSize="sm" aria-label="Severity" value={severityFilter} onChange={(e) => setFilter('severity', e.target.value)} className="w-auto">
                    <option value="all">All severities</option>
                    {SEVERITY_ORDER.map(s => <option key={s} value={s}>{s[0].toUpperCase() + s.slice(1)}</option>)}
                </Select>
                <Select inputSize="sm" aria-label="Status" value={statusFilter} onChange={(e) => setFilter('status', e.target.value)} className="w-auto">
                    <option value="all">All statuses</option>
                    <option value="active">Active (not resolved or closed)</option>
                    {CASE_STATUSES.map(s => <option key={s.value} value={s.value}>{s.label}</option>)}
                </Select>
                <Select inputSize="sm" aria-label="SLA" value={slaFilter} onChange={(e) => setFilter('sla', e.target.value)} className="w-auto">
                    <option value="all">Any SLA</option>
                    <option value="at_risk,breached">At risk or breached</option>
                    <option value="breached">Breached</option>
                    <option value="at_risk">At risk</option>
                </Select>
                <Select inputSize="sm" aria-label="Tag" value={tagFilter} onChange={(e) => setFilter('tag', e.target.value)} className="w-auto">
                    <option value="all">All tags</option>
                    {availableTags?.map(tag => (
                        <option key={tag} value={tag}>{tag}</option>
                    ))}
                </Select>
                {hasFilters && <Button size="sm" variant="ghost" onClick={clearFilters}>Clear filters</Button>}
                {drillChips.map(([key, label]) => (
                    <button key={key} type="button" onClick={() => setSearchParams(p => { p.delete(key); if (key === 'from') p.delete('to'); return p; }, { replace: true })}
                        className="inline-flex items-center gap-1 h-7 px-2 rounded-sm border border-accent/40 bg-accent/10 text-xs text-fg hover:border-accent"
                        aria-label={`Remove filter: ${label}`}>
                        {label}<X size={12} aria-hidden />
                    </button>
                ))}

                <div className="ml-auto flex rounded-md border border-line-strong overflow-hidden" role="group" aria-label="View">
                    {([['table', LayoutList, 'Table view'], ['board', Columns3, 'Board view']] as const).map(([v, Icon, label]) => (
                        <button
                            key={v}
                            type="button"
                            onClick={() => changeView(v)}
                            title={label}
                            aria-label={label}
                            aria-pressed={view === v}
                            className={cn('h-8 px-2.5', view === v ? 'bg-raised text-fg' : 'bg-panel text-fg-3 hover:text-fg hover:bg-raised')}
                        >
                            <Icon size={14} aria-hidden />
                        </button>
                    ))}
                </div>
            </div>

            {isLoading ? (
                <Skeleton rows={8} />
            ) : isError ? (
                <ErrorState message="Could not load cases." onRetry={() => refetch()} />
            ) : filteredCases.length === 0 ? (
                <div className="rounded-md border border-line bg-panel">
                    {hasFilters ? (
                        <EmptyState title="No cases match these filters" body="Try a different search or clear the filters."
                            action={<Button variant="secondary" onClick={clearFilters}>Clear filters</Button>} />
                    ) : (
                        <EmptyState title="No cases yet" body="Open a case when something needs investigating."
                            action={<Button onClick={() => setShowNewCaseModal(true)}><Plus size={16} aria-hidden /> New case</Button>} />
                    )}
                </div>
            ) : view === 'board' ? (
                <CasesBoard cases={filteredCases} />
            ) : (
                <div className="rounded-md border border-line bg-panel overflow-x-auto">
                    <table className="w-full text-sm text-left">
                        <thead className="border-b border-line bg-surface">
                            <tr className="label-caps">
                                <th scope="col" className="px-3 py-2">Case</th>
                                <th scope="col" className="px-3 py-2 w-28">Severity</th>
                                <th scope="col" className="px-3 py-2 w-32">Status</th>
                                <th scope="col" className="px-3 py-2 w-28">SLA</th>
                                <th scope="col" className="px-3 py-2 w-36">Created</th>
                                <th scope="col" className="px-3 py-2 w-32">Updated</th>
                            </tr>
                        </thead>
                        <tbody className="divide-y divide-line">
                            {filteredCases.map((c) => (
                                <tr
                                    key={c.id}
                                    onClick={(e) => {
                                        // The title cell holds the real link (keyboard, middle-click); the rest of the row is a mouse shortcut.
                                        if ((e.target as HTMLElement).closest('a,button')) return;
                                        navigate(`/cases/${c.id}`);
                                    }}
                                    className="group cursor-pointer hover:bg-raised transition-colors"
                                >
                                    <td className="px-3 py-2.5">
                                        <Link to={`/cases/${c.id}`} className="flex items-baseline gap-2 min-w-0">
                                            <span className="num text-xs text-fg-3 shrink-0">#{c.id}</span>
                                            <span className="font-medium text-fg group-hover:text-accent transition-colors truncate max-w-md">{c.title}</span>
                                        </Link>
                                        {c.description && (
                                            <div className="text-xs text-fg-3 mt-0.5 truncate max-w-md hidden md:block">
                                                {c.description}
                                            </div>
                                        )}
                                    </td>
                                    <td className="px-3 py-2.5"><SeverityStamp severity={c.severity} /></td>
                                    <td className="px-3 py-2.5"><StatusStamp status={c.status} /></td>
                                    <td className="px-3 py-2.5"><SLABadge status={c.sla_overall_status} /></td>
                                    <td className="px-3 py-2.5 text-fg-3 text-xs whitespace-nowrap num">
                                        {fmtDate(c.created_at)}
                                        <span className="ml-1">
                                            {new Date(c.created_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
                                        </span>
                                    </td>
                                    <td className="px-3 py-2.5 text-fg-3 text-xs whitespace-nowrap num">{fmtDate(c.updated_at)}</td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            )}

            <NewCaseModal
                show={showNewCaseModal}
                onClose={closeModal}
                onSubmit={(data) => createCaseMutation.mutate(data)}
                isSubmitting={createCaseMutation.isPending}
            />
        </PageContainer>
    );
}
