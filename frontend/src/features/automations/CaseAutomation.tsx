import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Link, useNavigate } from 'react-router-dom';
import { Play } from 'lucide-react';
import { api } from '../../api/client';
import type { User } from '../../types';
import type { RunSummary, WorkflowSummary } from './types';
import RunsTable, { StatusBadge } from './RunsTable';

export default function CaseAutomation({ caseId }: { caseId: number }) {
    const qc = useQueryClient();
    const navigate = useNavigate();
    const [picked, setPicked] = useState('');
    const [error, setError] = useState<string | null>(null);
    const { data: me } = useQuery({ queryKey: ['currentUser'], queryFn: async () => (await api.get('/users/me')).data as User, staleTime: 300_000 });
    const canRun = !!me && (me.is_super_admin || me.role === 'admin' || me.role === 'analyst');

    const { data: workflows = [] } = useQuery({
        queryKey: ['workflows'],
        queryFn: async () => (await api.get('/workflows/')).data as WorkflowSummary[],
    });
    const manual = workflows.filter((w) => w.trigger_type === 'manual' && w.enabled);

    const run = useMutation({
        mutationFn: async () => (await api.post(`/workflows/${picked}/run`, { case_id: caseId })).data as { run_id: number },
        onSuccess: ({ run_id }) => { qc.invalidateQueries({ queryKey: ['workflow-runs'] }); navigate(`/automations/runs/${run_id}`); },
        onError: (err: { response?: { data?: { detail?: string } } }) => setError(err?.response?.data?.detail ?? 'Run failed'),
    });

    return (
        <div className="space-y-4">
            {canRun && (
                <div className="flex items-center gap-2">
                    <select className="border border-zinc-300 px-2 py-1.5 text-sm bg-white min-w-[240px]" value={picked} onChange={(e) => setPicked(e.target.value)}
                        aria-label="Manual workflow">
                        <option value="">{manual.length ? 'Run a workflow on this case…' : 'No manual workflows enabled'}</option>
                        {manual.map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}
                    </select>
                    <button disabled={!picked || run.isPending} onClick={() => { setError(null); run.mutate(); }}
                        className="inline-flex items-center gap-1.5 h-8 px-3 bg-accent-600 text-white text-sm disabled:opacity-50"><Play size={13} />Run</button>
                    {error && <span className="text-xs text-red-700">{error}</span>}
                </div>
            )}
            <RunsTable caseId={caseId} />
        </div>
    );
}

export function AlertRuns({ alertId }: { alertId: number }) {
    const { data: runs = [] } = useQuery({
        queryKey: ['workflow-runs', { alert_id: alertId }],
        queryFn: async () => (await api.get('/workflow-runs/', { params: { alert_id: alertId, dry_run: false } })).data as RunSummary[],
    });
    if (!runs.length) return null;
    return (
        <div className="flex items-center gap-3 flex-wrap text-xs mb-2">
            <span className="label-mono">automation</span>
            {runs.map((r) => (
                <Link key={r.id} to={`/automations/runs/${r.id}`} className="flex items-center gap-1.5 text-accent-600 hover:underline">
                    {r.workflow_name} <StatusBadge status={r.status} />
                </Link>
            ))}
        </div>
    );
}
