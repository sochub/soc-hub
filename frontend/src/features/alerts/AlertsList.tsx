import { Fragment, useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '../../api/client';
import { cn } from '../../lib/utils';
import { ChevronDown, ChevronRight, Search, X, CheckCircle } from 'lucide-react';
import PromoteAlertModal from './PromoteAlertModal';

interface Alert {
    id: number;
    source: string;
    external_id: string;
    title: string;
    payload: any;
    status: string;
    case_id: number | null;
    created_at: string;
}

const statusColor: Record<string, string> = {
    pending: 'text-amber-700 bg-amber-50 border-amber-200',
    promoted: 'text-emerald-700 bg-emerald-50 border-emerald-200',
    dismissed: 'text-zinc-500 bg-zinc-100 border-zinc-200',
};

export default function AlertsList() {
    const qc = useQueryClient();
    const [statusFilter, setStatusFilter] = useState('all');
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

    const sources = Array.from(new Set((alerts || []).map(a => a.source).filter(Boolean)));
    const filtered = (alerts || []).filter(a =>
        (statusFilter === 'all' || a.status === statusFilter) &&
        (sourceFilter === 'all' || a.source === sourceFilter)
    );

    if (isLoading) {
        return <div className="flex items-center justify-center h-[50vh]">
            <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-zinc-300" /></div>;
    }

    return (
        <div className="space-y-4">
            <div className="flex flex-col lg:flex-row lg:items-center justify-between gap-4">
                <div>
                    <h1 className="text-2xl font-bold text-zinc-900 tracking-tight">Alerts</h1>
                    <p className="text-zinc-500 text-sm mt-0.5">{filtered.length} alerts</p>
                </div>
                <div className="flex flex-wrap items-center gap-2">
                    <div className="relative">
                        <select value={sourceFilter} onChange={e => setSourceFilter(e.target.value)}
                            className="appearance-none bg-white border border-zinc-200 rounded-lg pl-3 pr-8 py-1.5 text-sm text-zinc-700 cursor-pointer focus:outline-none focus:ring-1 focus:ring-accent-500">
                            <option value="all">All Sources</option>
                            {sources.map(s => <option key={s} value={s}>{s}</option>)}
                        </select>
                        <ChevronDown size={14} className="absolute right-2.5 top-1/2 -translate-y-1/2 text-zinc-400 pointer-events-none" />
                    </div>
                    <div className="relative">
                        <select value={statusFilter} onChange={e => setStatusFilter(e.target.value)}
                            className="appearance-none bg-white border border-zinc-200 rounded-lg pl-3 pr-8 py-1.5 text-sm text-zinc-700 cursor-pointer focus:outline-none focus:ring-1 focus:ring-accent-500">
                            <option value="all">All Statuses</option>
                            <option value="pending">Pending</option>
                            <option value="promoted">Promoted</option>
                            <option value="dismissed">Dismissed</option>
                        </select>
                        <ChevronDown size={14} className="absolute right-2.5 top-1/2 -translate-y-1/2 text-zinc-400 pointer-events-none" />
                    </div>
                </div>
            </div>

            <div className="glass-panel rounded-lg border border-zinc-200 overflow-hidden">
                <table className="w-full text-sm text-left">
                    <thead className="bg-white border-b border-zinc-200 text-xs uppercase text-zinc-500 font-semibold tracking-wider">
                        <tr>
                            <th className="px-4 py-3 w-8"></th>
                            <th className="px-4 py-3 w-40">Source</th>
                            <th className="px-4 py-3">Title</th>
                            <th className="px-4 py-3 w-28">Status</th>
                            <th className="px-4 py-3 w-40">Received</th>
                            <th className="px-4 py-3 w-56">Actions</th>
                        </tr>
                    </thead>
                    <tbody className="divide-y divide-zinc-200">
                        {filtered.length === 0 ? (
                            <tr><td colSpan={6} className="px-6 py-12 text-center text-zinc-400">
                                <Search size={32} className="mb-2 opacity-50 mx-auto" />No alerts</td></tr>
                        ) : filtered.map(a => (
                            <Fragment key={a.id}>
                                <tr className="group hover:bg-zinc-100">
                                    <td className="px-4 py-2.5">
                                        <button onClick={() => setExpanded(expanded === a.id ? null : a.id)} className="text-zinc-400 hover:text-zinc-700">
                                            {expanded === a.id ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
                                        </button>
                                    </td>
                                    <td className="px-4 py-2.5"><span className="font-mono text-xs text-zinc-600">{a.source}</span></td>
                                    <td className="px-4 py-2.5 font-medium text-zinc-800">{a.title}</td>
                                    <td className="px-4 py-2.5">
                                        <span className={cn("inline-flex px-2 py-0.5 rounded text-[10px] font-bold uppercase tracking-wider border", statusColor[a.status] || statusColor.dismissed)}>{a.status}</span>
                                    </td>
                                    <td className="px-4 py-2.5 text-zinc-500 text-xs whitespace-nowrap">{new Date(a.created_at).toLocaleString()}</td>
                                    <td className="px-4 py-2.5">
                                        {a.status === 'pending' ? (
                                            <div className="flex items-center gap-2">
                                                <button onClick={() => setPromoting(a)} className="px-2 py-1 text-xs font-semibold rounded bg-accent-600 text-white hover:bg-accent-700 flex items-center gap-1"><CheckCircle size={12} />Promote</button>
                                                <button onClick={() => dismiss.mutate(a.id)} className="px-2 py-1 text-xs font-semibold rounded bg-zinc-100 text-zinc-600 hover:bg-zinc-200 flex items-center gap-1"><X size={12} />Dismiss</button>
                                            </div>
                                        ) : a.case_id ? (
                                            <a href={`/cases/${a.case_id}`} className="text-xs text-accent-600 hover:underline">Case #{a.case_id}</a>
                                        ) : <span className="text-xs text-zinc-400">—</span>}
                                    </td>
                                </tr>
                                {expanded === a.id && (
                                    <tr><td colSpan={6} className="px-6 py-3 bg-zinc-50">
                                        <pre className="text-xs text-zinc-600 overflow-x-auto">{JSON.stringify(a.payload, null, 2)}</pre>
                                    </td></tr>
                                )}
                            </Fragment>
                        ))}
                    </tbody>
                </table>
            </div>

            {promoting && <PromoteAlertModal alert={promoting} onClose={() => setPromoting(null)} />}
        </div>
    );
}
