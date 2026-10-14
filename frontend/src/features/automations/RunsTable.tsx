import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { api } from '../../api/client';
import { cn } from '../../lib/utils';
import type { RunSummary } from './types';

// eslint-disable-next-line react-refresh/only-export-components
export const STATUS_STYLE: Record<string, string> = {
    succeeded: 'bg-emerald-500/10 text-emerald-300 border-emerald-500/30',
    failed: 'bg-red-500/10 text-red-300 border-red-500/30',
    running: 'bg-accent/10 text-accent border-accent/40',
    queued: 'bg-surface text-fg-2 border-line',
    waiting: 'bg-amber-500/10 text-amber-300 border-amber-500/30',
    cancelled: 'bg-raised text-fg-3 border-line',
    skipped: 'bg-surface text-muted border-line',
    pending: 'bg-surface text-fg-2 border-line',
};

export function StatusBadge({ status }: { status: string }) {
    return <span className={cn('inline-flex px-2 py-0.5 text-[10px] font-bold uppercase tracking-wider border', STATUS_STYLE[status] ?? STATUS_STYLE.pending)}>{status}</span>;
}

function duration(r: RunSummary) {
    if (!r.started_at) return '—';
    const end = r.finished_at ? new Date(r.finished_at).getTime() : Date.now();
    const s = Math.round((end - new Date(r.started_at).getTime()) / 1000);
    return s < 60 ? `${s}s` : `${Math.floor(s / 60)}m ${s % 60}s`;
}

export default function RunsTable({ workflowId, caseId }: { workflowId?: number; caseId?: number }) {
    const [showDry, setShowDry] = useState(true);
    const params: Record<string, string | number | boolean> = { limit: 100 };
    if (workflowId) params.workflow_id = workflowId;
    if (caseId) params.case_id = caseId;
    if (!showDry) params.dry_run = false;

    const { data: runs = [], isLoading } = useQuery({
        queryKey: ['workflow-runs', params],
        queryFn: async () => (await api.get('/workflow-runs/', { params })).data as RunSummary[],
        refetchInterval: (q) => ((q.state.data as RunSummary[] | undefined)?.some((r) => ['running', 'waiting'].includes(r.status)) ? 3000 : false),
    });

    return (
        <div className="bg-panel border border-line">
            <div className="flex items-center justify-between px-4 py-2 border-b border-line">
                <span className="label-mono">runs</span>
                <label className="flex items-center gap-2 text-xs text-fg-2">
                    <input type="checkbox" checked={showDry} onChange={(e) => setShowDry(e.target.checked)} /> include dry runs
                </label>
            </div>
            <table className="w-full text-sm">
                <thead className="bg-surface text-left label-mono">
                    <tr><th className="px-4 py-2">#</th><th className="px-4 py-2">Workflow</th><th className="px-4 py-2">Status</th>
                        <th className="px-4 py-2">Case / alert</th><th className="px-4 py-2">Started</th><th className="px-4 py-2">Duration</th></tr>
                </thead>
                <tbody className="divide-y divide-line">
                    {isLoading && <tr><td colSpan={6} className="px-4 py-8 text-center text-muted">Loading…</td></tr>}
                    {!isLoading && runs.length === 0 && <tr><td colSpan={6} className="px-4 py-8 text-center text-muted">No runs yet</td></tr>}
                    {runs.map((r) => (
                        <tr key={r.id} className="hover:bg-surface">
                            <td className="px-4 py-2 num"><Link to={`/automations/runs/${r.id}`} className="text-accent hover:underline">{r.id}</Link></td>
                            <td className="px-4 py-2">{r.workflow_name ?? r.workflow_id} <span className="num text-xs text-muted">v{r.workflow_version}</span></td>
                            <td className="px-4 py-2 flex items-center gap-1.5">
                                <StatusBadge status={r.status} />
                                {r.is_dry_run && <span className="px-1.5 py-0.5 text-[10px] font-bold border border-violet-500/30 bg-violet-500/10 text-violet-300">DRY RUN</span>}
                            </td>
                            <td className="px-4 py-2 num text-xs">
                                {r.case_id && <Link to={`/cases/${r.case_id}`} className="text-accent hover:underline mr-2">case {r.case_id}</Link>}
                                {r.alert_id && <span className="text-fg-3">alert {r.alert_id}</span>}
                            </td>
                            <td className="px-4 py-2 text-xs text-fg-3">{r.started_at ? new Date(r.started_at).toLocaleString() : '—'}</td>
                            <td className="px-4 py-2 num text-xs">{duration(r)}</td>
                        </tr>
                    ))}
                </tbody>
            </table>
        </div>
    );
}
