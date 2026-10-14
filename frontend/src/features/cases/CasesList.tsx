import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '../../api/client';
import { Link, useSearchParams } from 'react-router-dom';
import { Clock, AlertCircle, CheckCircle, XCircle, Plus, Search, ChevronDown, LayoutList, Columns3 } from 'lucide-react';
import { cn } from '../../lib/utils';
import { useState } from 'react';
import NewCaseModal from './NewCaseModal';
import CasesBoard from './CasesBoard';
import { SLABadge } from './SLABadge';
import type { SLAStatus } from '../../types';
import PageContainer from '../../components/layout/PageContainer';

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
}

const severityConfig = {
    critical: { color: 'text-red-300', bg: 'bg-red-500/10', border: 'border-red-400/20' },
    high: { color: 'text-orange-300', bg: 'bg-orange-500/10', border: 'border-orange-400/20' },
    medium: { color: 'text-yellow-300', bg: 'bg-yellow-500/10', border: 'border-yellow-400/20' },
    low: { color: 'text-accent', bg: 'bg-accent/10', border: 'border-accent/20' },
    info: { color: 'text-fg-3', bg: 'bg-raised', border: 'border-line' },
};

const statusConfig: Record<string, { icon: any; color: string }> = {
    open: { icon: AlertCircle, color: 'text-red-300' },
    investigating: { icon: Clock, color: 'text-orange-300' },
    resolved: { icon: CheckCircle, color: 'text-emerald-300' },
    closed: { icon: XCircle, color: 'text-muted' },
};

export default function CasesList() {
    const [searchParams, setSearchParams] = useSearchParams();
    const [modalOpen, setShowNewCaseModal] = useState(false);
    const showNewCaseModal = modalOpen || searchParams.get('new') === '1'; // ⌘K "New case" deep link
    const [searchQuery, setSearchQuery] = useState('');
    const [severityFilter, setSeverityFilter] = useState<string>(searchParams.get('severity') || 'all');
    const [statusFilter, setStatusFilter] = useState<string>(searchParams.get('status') || 'all');
    const [tagFilter, setTagFilter] = useState<string>('all');
    // URL wins (shareable links), else the last view this browser used
    const storedView = (() => { try { return localStorage.getItem('cases:view'); } catch { return null; } })();
    const view: 'table' | 'board' = (searchParams.get('view') ?? storedView) === 'board' ? 'board' : 'table';
    const changeView = (v: 'table' | 'board') => {
        setSearchParams(p => { p.set('view', v); return p; }, { replace: true });
        try { localStorage.setItem('cases:view', v); } catch { /* storage unavailable */ }
    };
    const queryClient = useQueryClient();

    const { data: cases, isLoading } = useQuery<Case[]>({
        queryKey: ['cases'],
        queryFn: async () => {
            const response = await api.get('/cases/');
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
        mutationFn: async (data: { title: string; description: string; severity: string; status: string; tags: string[]; source: string; artifacts: any[]; playbook_template_id?: string }) => {
            const response = await api.post('/cases/', {
                title: data.title,
                description: data.description,
                severity: data.severity,
                status: data.status,
                tags: data.tags,
                source: data.source
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

    const filteredCases = cases?.filter(c => {
        const matchesSearch = !searchQuery || (
            c.title.toLowerCase().includes(searchQuery.toLowerCase()) ||
            c.description.toLowerCase().includes(searchQuery.toLowerCase()) ||
            String(c.id).includes(searchQuery)
        );
        const matchesSeverity = severityFilter === 'all' || c.severity === severityFilter;
        const matchesStatus = statusFilter === 'all' || c.status === statusFilter;
        const matchesTag = tagFilter === 'all' || (c.tags && c.tags.includes(tagFilter));

        return matchesSearch && matchesSeverity && matchesStatus && matchesTag;
    }) || [];

    if (isLoading) {
        return (
            <PageContainer>
                <div className="flex items-center justify-center h-[50vh]">
                    <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-line-strong"></div>
                </div>
            </PageContainer>
        );
    }

    return (
        <PageContainer className="space-y-4">
            {/* Improved Header with Filters */}
            <div className="flex flex-col lg:flex-row lg:items-center justify-between gap-4">
                <div>
                    <h1 className="text-2xl font-bold text-fg tracking-tight">Cases</h1>
                    <p className="text-fg-3 text-sm mt-0.5">
                        {filteredCases.length} records found
                    </p>
                </div>

                <div className="flex flex-wrap items-center gap-2">
                    {/* Search */}
                    <div className="relative group">
                        <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-muted group-focus-within:text-fg-2 transition-colors" />
                        <input
                            type="text"
                            value={searchQuery}
                            onChange={(e) => setSearchQuery(e.target.value)}
                            placeholder="Search (ID, Title)..."
                            className="bg-panel border border-line rounded-lg pl-9 pr-3 py-1.5 text-sm focus:outline-hidden focus:ring-1 focus:ring-accent focus:border-accent transition-all w-48 lg:w-64 placeholder:text-muted"
                        />
                    </div>

                    {/* Filters */}
                    <div className="relative">
                        <select
                            value={severityFilter}
                            onChange={(e) => setSeverityFilter(e.target.value)}
                            className="appearance-none bg-panel border border-line rounded-lg pl-3 pr-8 py-1.5 text-sm focus:outline-hidden focus:ring-1 focus:ring-accent focus:border-accent transition-all text-fg-2 cursor-pointer"
                        >
                            <option value="all">All Severities</option>
                            <option value="critical">Critical</option>
                            <option value="high">High</option>
                            <option value="medium">Medium</option>
                            <option value="low">Low</option>
                            <option value="info">Info</option>
                        </select>
                        <ChevronDown size={14} className="absolute right-2.5 top-1/2 -translate-y-1/2 text-muted pointer-events-none" />
                    </div>

                    <div className="relative">
                        <select
                            value={tagFilter}
                            onChange={(e) => setTagFilter(e.target.value)}
                            className="appearance-none bg-panel border border-line rounded-lg pl-3 pr-8 py-1.5 text-sm focus:outline-hidden focus:ring-1 focus:ring-accent focus:border-accent transition-all text-fg-2 cursor-pointer"
                        >
                            <option value="all">All Tags</option>
                            {availableTags?.map(tag => (
                                <option key={tag} value={tag}>{tag}</option>
                            ))}
                        </select>
                        <ChevronDown size={14} className="absolute right-2.5 top-1/2 -translate-y-1/2 text-muted pointer-events-none" />
                    </div>

                    <div className="relative">
                        <select
                            value={statusFilter}
                            onChange={(e) => setStatusFilter(e.target.value)}
                            className="appearance-none bg-panel border border-line rounded-lg pl-3 pr-8 py-1.5 text-sm focus:outline-hidden focus:ring-1 focus:ring-accent focus:border-accent transition-all text-fg-2 cursor-pointer"
                        >
                            <option value="all">All Statuses</option>
                            <option value="new">New</option>
                            <option value="open">Open</option>
                            <option value="in_progress">In Progress</option>
                            <option value="pending">Pending</option>
                            <option value="resolved">Resolved</option>
                            <option value="closed">Closed</option>
                        </select>
                        <ChevronDown size={14} className="absolute right-2.5 top-1/2 -translate-y-1/2 text-muted pointer-events-none" />
                    </div>

                    <div className="flex border border-line rounded-lg overflow-hidden">
                        {([['table', LayoutList], ['board', Columns3]] as const).map(([v, Icon]) => (
                            <button
                                key={v}
                                onClick={() => changeView(v)}
                                title={v === 'table' ? 'Table view' : 'Board view'}
                                aria-pressed={view === v}
                                className={cn("px-2 py-1.5", view === v ? "bg-fg text-on-accent" : "bg-panel text-fg-3 hover:text-fg")}
                            >
                                <Icon size={14} />
                            </button>
                        ))}
                    </div>

                    <button
                        onClick={() => setShowNewCaseModal(true)}
                        className="px-3.5 py-1.5 bg-brand-gradient hover:opacity-90 rounded-full text-sm font-semibold transition-opacity flex items-center gap-1.5 ml-2"
                    >
                        <Plus size={16} />
                        New Case
                    </button>
                </div>
            </div>

            {view === 'board' ? (
                <CasesBoard cases={filteredCases} severityConfig={severityConfig} />
            ) : (
            <div className="glass-panel rounded-lg border border-line overflow-hidden">
                <div className="overflow-x-auto">
                    <table className="w-full text-sm text-left">
                        <thead className="bg-panel border-b border-line text-xs uppercase text-fg-3 font-semibold tracking-wider">
                            <tr>
                                <th className="px-4 py-3 w-20">ID</th>
                                <th className="px-4 py-3">Case Title</th>
                                <th className="px-4 py-3 w-32">Severity</th>
                                <th className="px-4 py-3 w-36">Status</th>
                                <th className="px-4 py-3 w-28">SLA</th>
                                <th className="px-4 py-3 w-40">Created</th>
                                <th className="px-4 py-3 w-40">Updated</th>
                            </tr>
                        </thead>
                        <tbody className="divide-y divide-line">
                            {filteredCases.length === 0 ? (
                                <tr>
                                    <td colSpan={7} className="px-6 py-12 text-center">
                                        <div className="flex flex-col items-center justify-center text-muted">
                                            <Search size={32} className="mb-2 opacity-50" />
                                            <p>No matching cases found</p>
                                        </div>
                                    </td>
                                </tr>
                            ) : (
                                filteredCases.map((c) => {
                                    const config = severityConfig[c.severity] || severityConfig.info;
                                    const StatusIcon = statusConfig[c.status]?.icon || AlertCircle;
                                    const statusColor = statusConfig[c.status]?.color || 'text-fg-3';

                                    return (
                                        <tr
                                            key={c.id}
                                            className="group hover:bg-raised transition-colors"
                                        >
                                            <td className="px-4 py-2.5 font-mono text-muted group-hover:text-fg-3">
                                                #{c.id}
                                            </td>
                                            <td className="px-4 py-2.5">
                                                <Link to={`/cases/${c.id}`} className="block">
                                                    <div className="font-medium text-fg group-hover:text-accent transition-colors truncate max-w-md">
                                                        {c.title}
                                                    </div>
                                                    {c.description && (
                                                        <div className="text-xs text-muted mt-0.5 truncate max-w-md hidden md:block">
                                                            {c.description}
                                                        </div>
                                                    )}
                                                </Link>
                                            </td>
                                            <td className="px-4 py-2.5">
                                                <span className={cn(
                                                    "inline-flex items-center px-2 py-0.5 rounded-sm text-[10px] font-bold uppercase tracking-wider border",
                                                    config.color, config.bg, config.border
                                                )}>
                                                    {c.severity}
                                                </span>
                                            </td>
                                            <td className="px-4 py-2.5">
                                                <div className="flex items-center gap-1.5">
                                                    <StatusIcon size={14} className={statusColor} />
                                                    <span className={cn("text-xs font-medium capitalize", statusColor)}>
                                                        {c.status}
                                                    </span>
                                                </div>
                                            </td>
                                            <td className="px-4 py-2.5">
                                                <SLABadge status={c.sla_overall_status} />
                                            </td>
                                            <td className="px-4 py-2.5 text-fg-3 text-xs whitespace-nowrap">
                                                {new Date(c.created_at).toLocaleDateString()}
                                                <span className="text-muted ml-1">
                                                    {new Date(c.created_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
                                                </span>
                                            </td>
                                            <td className="px-4 py-2.5 text-fg-3 text-xs whitespace-nowrap">
                                                {new Date(c.updated_at).toLocaleDateString()}
                                            </td>
                                        </tr>
                                    );
                                })
                            )}
                        </tbody>
                    </table>
                </div>
            </div>
            )}

            {/* New Case Modal */}
            <NewCaseModal
                show={showNewCaseModal}
                onClose={closeModal}
                onSubmit={(data) => createCaseMutation.mutate(data)}
                isSubmitting={createCaseMutation.isPending}
            />
        </PageContainer>
    );
}
