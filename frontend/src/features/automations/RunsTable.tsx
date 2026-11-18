import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { api } from '../../api/client';
import { Badge, ErrorState, Skeleton, Stamp, type Tone } from '../../components/ui';
import type { RunSummary } from './types';

const STATUS_TONE: Record<string, Tone> = {
    succeeded: 'ok',
    failed: 'critical',
    running: 'accent',
    waiting: 'medium',
};

export function StatusBadge({ status }: { status: string }) {
    return <Stamp tone={STATUS_TONE[status] ?? 'neutral'}>{status}</Stamp>;
}

function ago(iso: string) {
    const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
    if (s < 60) return 'just now';
    if (s < 3600) return `${Math.floor(s / 60)}m ago`;
    if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
    if (s < 30 * 86400) return `${Math.floor(s / 86400)}d ago`;
    return new Date(iso).toLocaleDateString();
}

/** Relative time ("5m ago") with the absolute local time on hover. */
export function RelTime({ iso }: { iso: string }) {
    const abs = new Date(iso).toLocaleString();
    return <time dateTime={iso} title={abs}>{ago(iso)}</time>;
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

    const { data: runs = [], isLoading, isError, refetch } = useQuery({
        queryKey: ['workflow-runs', params],
        queryFn: async () => (await api.get('/workflow-runs/', { params })).data as RunSummary[],
        refetchInterval: (q) => ((q.state.data as RunSummary[] | undefined)?.some((r) => ['running', 'waiting'].includes(r.status)) ? 3000 : false),
    });

    return (
        <div className="rounded-md bg-panel border border-line">
            <div className="flex items-center justify-between px-4 py-2 border-b border-line">
                <h2 className="text-sm font-semibold text-fg">Runs</h2>
                <label className="flex items-center gap-2 text-xs text-fg-2">
                    <input type="checkbox" checked={showDry} onChange={(e) => setShowDry(e.target.checked)} /> Include dry runs
                </label>
            </div>
            {isLoading ? <Skeleton rows={4} className="p-4" /> : isError ? (
                <ErrorState className="m-4" message="Could not load runs." onRetry={() => refetch()} />
            ) : (
            <div className="overflow-x-auto">
            <table className="w-full text-sm">
                <thead className="text-left label-caps">
                    <tr><th className="px-3 py-2">#</th><th className="px-3 py-2">Workflow</th><th className="px-3 py-2">Status</th>
                        <th className="px-3 py-2">Case / alert</th><th className="px-3 py-2">Started</th><th className="px-3 py-2">Duration</th></tr>
                </thead>
                <tbody className="divide-y divide-line">
                    {runs.length === 0 && <tr><td colSpan={6} className="px-3 py-8 text-center text-fg-3">No runs yet</td></tr>}
                    {runs.map((r) => (
                        <tr key={r.id} className="hover:bg-raised">
                            <td className="px-3 py-2.5 num"><Link to={`/workflows/runs/${r.id}`} className="text-accent hover:underline">{r.id}</Link></td>
                            <td className="px-3 py-2.5">{r.workflow_name ?? r.workflow_id} <span className="num text-xs text-muted">v{r.workflow_version}</span></td>
                            <td className="px-3 py-2.5">
                                <span className="flex items-center gap-1.5">
                                    <StatusBadge status={r.status} />
                                    {r.is_dry_run && <Badge>Dry run</Badge>}
                                </span>
                            </td>
                            <td className="px-3 py-2.5 num text-xs">
                                {r.case_id && <Link to={`/cases/${r.case_id}`} className="text-accent hover:underline mr-2">case {r.case_id}</Link>}
                                {r.alert_id && <span className="text-fg-3">alert {r.alert_id}</span>}
                            </td>
                            <td className="px-3 py-2.5 text-xs text-fg-3">{r.started_at ? <RelTime iso={r.started_at} /> : '—'}</td>
                            <td className="px-3 py-2.5 num text-xs">{duration(r)}</td>
                        </tr>
                    ))}
                </tbody>
            </table>
            </div>
            )}
        </div>
    );
}
