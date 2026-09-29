import { useState } from 'react';
import { isAxiosError } from 'axios';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Link, useNavigate } from 'react-router-dom';
import { Plus, Workflow as WorkflowIcon } from 'lucide-react';
import { api } from '../../api/client';
import { cn } from '../../lib/utils';
import type { User } from '../../types';
import type { WorkflowSummary } from './types';
import { TRIGGER_LABEL } from './nodeCatalog';
import RunsTable, { StatusBadge } from './RunsTable';

export default function AutomationsList() {
    const qc = useQueryClient();
    const navigate = useNavigate();
    const [tab, setTab] = useState<'workflows' | 'runs'>('workflows');
    const [toggleError, setToggleError] = useState<string | null>(null);

    const { data: me } = useQuery({ queryKey: ['currentUser'], queryFn: async () => (await api.get('/users/me')).data as User, staleTime: 300_000 });
    const isAdmin = me?.role === 'admin' || !!me?.is_super_admin;

    const { data: workflows = [], isLoading } = useQuery({
        queryKey: ['workflows'],
        queryFn: async () => (await api.get('/workflows/')).data as WorkflowSummary[],
    });

    const toggle = useMutation({
        mutationFn: async (w: WorkflowSummary) => api.post(`/workflows/${w.id}/${w.enabled ? 'disable' : 'enable'}`),
        onMutate: () => setToggleError(null),
        onSuccess: () => { qc.invalidateQueries({ queryKey: ['workflows'] }); },
        onError: (err, w) => {
            const detail = isAxiosError(err) ? err.response?.data?.detail : undefined;
            const errors = err && isAxiosError(err) && err.response?.status === 422 ? detail?.errors : undefined;
            if (Array.isArray(errors)) {
                setToggleError(`Can't enable "${w.name}": ${errors.map((e: { message: string }) => e.message).join('; ')}`);
            } else {
                setToggleError(typeof detail === 'string' ? detail : `Could not update "${w.name}"`);
            }
        },
    });

    return (
        <div className="p-4 sm:p-6 max-w-[1400px] mx-auto">
            <div className="flex items-end justify-between mb-5 flex-wrap gap-3">
                <div>
                    <h1 className="text-xl font-semibold tracking-tight text-zinc-900">Automations</h1>
                    <p className="label-mono mt-1">workflows for cases and alerts</p>
                </div>
                {isAdmin && (
                    <button onClick={() => navigate('/automations/new')}
                        className="inline-flex items-center gap-1.5 h-9 px-3.5 bg-accent-600 text-white text-sm font-medium hover:bg-accent-700">
                        <Plus size={15} /> New workflow
                    </button>
                )}
            </div>

            <div className="flex gap-4 border-b border-zinc-200 mb-4">
                {(['workflows', 'runs'] as const).map((t) => (
                    <button key={t} onClick={() => setTab(t)}
                        className={cn('pb-2 text-sm capitalize', tab === t ? 'text-accent-700 border-b-2 border-accent-600' : 'text-zinc-500 hover:text-zinc-800')}>{t}</button>
                ))}
            </div>

            {toggleError && <div role="alert" className="mb-3 px-3 py-2 text-sm border border-red-200 bg-red-50 text-red-700">{toggleError}</div>}

            {tab === 'runs' ? <RunsTable /> : (
                <div className="bg-white border border-zinc-200">
                    <table className="w-full text-sm">
                        <thead className="bg-zinc-50 text-left label-mono">
                            <tr><th className="px-4 py-2">Name</th><th className="px-4 py-2">Trigger</th><th className="px-4 py-2">Enabled</th>
                                <th className="px-4 py-2">Last run</th><th className="px-4 py-2">Runs</th></tr>
                        </thead>
                        <tbody className="divide-y divide-zinc-100">
                            {isLoading && <tr><td colSpan={5} className="px-4 py-8 text-center text-zinc-400">Loading…</td></tr>}
                            {!isLoading && workflows.length === 0 && (
                                <tr><td colSpan={5} className="px-4 py-12 text-center text-zinc-400">
                                    <WorkflowIcon className="mx-auto mb-2 opacity-50" />No workflows yet
                                </td></tr>
                            )}
                            {workflows.map((w) => (
                                <tr key={w.id} className="hover:bg-zinc-50">
                                    <td className="px-4 py-2.5">
                                        <Link to={`/automations/${w.id}`} className="font-medium text-zinc-900 hover:text-accent-700">{w.name}</Link>
                                        {w.description && <div className="text-xs text-zinc-500">{w.description}</div>}
                                    </td>
                                    <td className="px-4 py-2.5 font-mono text-xs text-zinc-600">{TRIGGER_LABEL[w.trigger_type]}</td>
                                    <td className="px-4 py-2.5">
                                        <button type="button" role="switch" aria-checked={w.enabled} aria-label={`${w.name} enabled`}
                                            title={isAdmin ? undefined : 'Only admins can enable workflows'}
                                            disabled={!isAdmin || toggle.isPending} onClick={() => toggle.mutate(w)}
                                            className={cn('w-9 h-5 relative transition-colors disabled:opacity-50', w.enabled ? 'bg-accent-600' : 'bg-zinc-300')}>
                                            <span className={cn('absolute top-0.5 w-4 h-4 bg-white transition-all', w.enabled ? 'left-[18px]' : 'left-0.5')} />
                                        </button>
                                    </td>
                                    <td className="px-4 py-2.5 text-xs text-zinc-500">
                                        {w.last_run_status ? <span className="flex items-center gap-2"><StatusBadge status={w.last_run_status} />
                                            {w.last_run_at && new Date(w.last_run_at).toLocaleString()}</span> : '—'}
                                    </td>
                                    <td className="px-4 py-2.5 num">{w.run_count}</td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            )}
        </div>
    );
}
