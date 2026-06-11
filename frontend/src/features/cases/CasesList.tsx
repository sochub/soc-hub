import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '../../api/client';
import { Link, useSearchParams } from 'react-router-dom';
import { Clock, AlertCircle, CheckCircle, XCircle, Plus, Search, ChevronDown } from 'lucide-react';
import { cn } from '../../lib/utils';
import { useState } from 'react';
import NewCaseModal from './NewCaseModal';

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
}

const severityConfig = {
    critical: { color: 'text-red-700', bg: 'bg-red-50', border: 'border-red-400/20' },
    high: { color: 'text-orange-700', bg: 'bg-orange-50', border: 'border-orange-400/20' },
    medium: { color: 'text-yellow-700', bg: 'bg-yellow-50', border: 'border-yellow-400/20' },
    low: { color: 'text-accent-600', bg: 'bg-blue-50', border: 'border-blue-400/20' },
    info: { color: 'text-zinc-500', bg: 'bg-zinc-100', border: 'border-zinc-200' },
};

const statusConfig: Record<string, { icon: any; color: string }> = {
    open: { icon: AlertCircle, color: 'text-red-700' },
    investigating: { icon: Clock, color: 'text-orange-700' },
    resolved: { icon: CheckCircle, color: 'text-emerald-700' },
    closed: { icon: XCircle, color: 'text-zinc-400' },
};

export default function CasesList() {
    const [searchParams] = useSearchParams();
    const [showNewCaseModal, setShowNewCaseModal] = useState(false);
    const [searchQuery, setSearchQuery] = useState('');
    const [severityFilter, setSeverityFilter] = useState<string>(searchParams.get('severity') || 'all');
    const [statusFilter, setStatusFilter] = useState<string>(searchParams.get('status') || 'all');
    const [tagFilter, setTagFilter] = useState<string>('all');
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
            <div className="flex items-center justify-center h-[50vh]">
                <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-zinc-300"></div>
            </div>
        );
    }

    return (
        <div className="space-y-4">
            {/* Improved Header with Filters */}
            <div className="flex flex-col lg:flex-row lg:items-center justify-between gap-4">
                <div>
                    <h1 className="text-2xl font-bold text-zinc-900 tracking-tight">Engineering Cases</h1>
                    <p className="text-zinc-500 text-sm mt-0.5">
                        {filteredCases.length} records found
                    </p>
                </div>

                <div className="flex flex-wrap items-center gap-2">
                    {/* Search */}
                    <div className="relative group">
                        <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-zinc-400 group-focus-within:text-zinc-700 transition-colors" />
                        <input
                            type="text"
                            value={searchQuery}
                            onChange={(e) => setSearchQuery(e.target.value)}
                            placeholder="Search (ID, Title)..."
                            className="bg-white border border-zinc-200 rounded-lg pl-9 pr-3 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-accent-500 focus:border-accent-500 transition-all w-48 lg:w-64 placeholder:text-zinc-400"
                        />
                    </div>

                    {/* Filters */}
                    <div className="relative">
                        <select
                            value={severityFilter}
                            onChange={(e) => setSeverityFilter(e.target.value)}
                            className="appearance-none bg-white border border-zinc-200 rounded-lg pl-3 pr-8 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-accent-500 focus:border-accent-500 transition-all text-zinc-700 cursor-pointer"
                        >
                            <option value="all">All Severities</option>
                            <option value="critical">Critical</option>
                            <option value="high">High</option>
                            <option value="medium">Medium</option>
                            <option value="low">Low</option>
                            <option value="info">Info</option>
                        </select>
                        <ChevronDown size={14} className="absolute right-2.5 top-1/2 -translate-y-1/2 text-zinc-400 pointer-events-none" />
                    </div>

                    <div className="relative">
                        <select
                            value={tagFilter}
                            onChange={(e) => setTagFilter(e.target.value)}
                            className="appearance-none bg-white border border-zinc-200 rounded-lg pl-3 pr-8 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-accent-500 focus:border-accent-500 transition-all text-zinc-700 cursor-pointer"
                        >
                            <option value="all">All Tags</option>
                            {availableTags?.map(tag => (
                                <option key={tag} value={tag}>{tag}</option>
                            ))}
                        </select>
                        <ChevronDown size={14} className="absolute right-2.5 top-1/2 -translate-y-1/2 text-zinc-400 pointer-events-none" />
                    </div>

                    <div className="relative">
                        <select
                            value={statusFilter}
                            onChange={(e) => setStatusFilter(e.target.value)}
                            className="appearance-none bg-white border border-zinc-200 rounded-lg pl-3 pr-8 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-accent-500 focus:border-accent-500 transition-all text-zinc-700 cursor-pointer"
                        >
                            <option value="all">All Statuses</option>
                            <option value="open">Open</option>
                            <option value="investigating">Investigating</option>
                            <option value="resolved">Resolved</option>
                            <option value="closed">Closed</option>
                        </select>
                        <ChevronDown size={14} className="absolute right-2.5 top-1/2 -translate-y-1/2 text-zinc-400 pointer-events-none" />
                    </div>

                    <button
                        onClick={() => setShowNewCaseModal(true)}
                        className="px-3 py-1.5 bg-zinc-100 hover:bg-white text-slate-900 rounded-lg text-sm font-semibold transition-colors flex items-center gap-1.5 shadow-sm ml-2"
                    >
                        <Plus size={16} />
                        New Case
                    </button>
                </div>
            </div>

            {/* Dense Table View */}
            <div className="glass-panel rounded-lg border border-zinc-200 overflow-hidden">
                <div className="overflow-x-auto">
                    <table className="w-full text-sm text-left">
                        <thead className="bg-white border-b border-zinc-200 text-xs uppercase text-zinc-500 font-semibold tracking-wider">
                            <tr>
                                <th className="px-4 py-3 w-20">ID</th>
                                <th className="px-4 py-3">Case Title</th>
                                <th className="px-4 py-3 w-32">Severity</th>
                                <th className="px-4 py-3 w-36">Status</th>
                                <th className="px-4 py-3 w-40">Created</th>
                                <th className="px-4 py-3 w-40">Updated</th>
                            </tr>
                        </thead>
                        <tbody className="divide-y divide-zinc-200">
                            {filteredCases.length === 0 ? (
                                <tr>
                                    <td colSpan={6} className="px-6 py-12 text-center">
                                        <div className="flex flex-col items-center justify-center text-zinc-400">
                                            <Search size={32} className="mb-2 opacity-50" />
                                            <p>No matching cases found</p>
                                        </div>
                                    </td>
                                </tr>
                            ) : (
                                filteredCases.map((c) => {
                                    const config = severityConfig[c.severity] || severityConfig.info;
                                    const StatusIcon = statusConfig[c.status]?.icon || AlertCircle;
                                    const statusColor = statusConfig[c.status]?.color || 'text-zinc-500';

                                    return (
                                        <tr
                                            key={c.id}
                                            className="group hover:bg-zinc-100 transition-colors"
                                        >
                                            <td className="px-4 py-2.5 font-mono text-zinc-400 group-hover:text-zinc-500">
                                                #{c.id}
                                            </td>
                                            <td className="px-4 py-2.5">
                                                <Link to={`/cases/${c.id}`} className="block">
                                                    <div className="font-medium text-zinc-800 group-hover:text-accent-600 transition-colors truncate max-w-md">
                                                        {c.title}
                                                    </div>
                                                    {c.description && (
                                                        <div className="text-xs text-zinc-400 mt-0.5 truncate max-w-md hidden md:block">
                                                            {c.description}
                                                        </div>
                                                    )}
                                                </Link>
                                            </td>
                                            <td className="px-4 py-2.5">
                                                <span className={cn(
                                                    "inline-flex items-center px-2 py-0.5 rounded text-[10px] font-bold uppercase tracking-wider border",
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
                                            <td className="px-4 py-2.5 text-zinc-500 text-xs whitespace-nowrap">
                                                {new Date(c.created_at).toLocaleDateString()}
                                                <span className="text-zinc-400 ml-1">
                                                    {new Date(c.created_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
                                                </span>
                                            </td>
                                            <td className="px-4 py-2.5 text-zinc-500 text-xs whitespace-nowrap">
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

            {/* New Case Modal */}
            <NewCaseModal
                show={showNewCaseModal}
                onClose={closeModal}
                onSubmit={(data) => createCaseMutation.mutate(data)}
                isSubmitting={createCaseMutation.isPending}
            />
        </div>
    );
}
