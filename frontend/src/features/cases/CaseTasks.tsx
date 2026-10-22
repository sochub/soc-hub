import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Check, Plus, Trash2, BookText, ChevronDown } from 'lucide-react';
import { api } from '../../api/client';
import { cn } from '../../lib/utils';
import type { CaseTask, PlaybookSummary, User } from '../../types';
import { PHASES, PHASE_LABEL, PHASE_COLOR } from '../playbooks/phases';

export default function CaseTasks({ caseId }: { caseId: number }) {
    const qc = useQueryClient();
    const [adding, setAdding] = useState(false);
    const [newTitle, setNewTitle] = useState('');
    const [newPhase, setNewPhase] = useState('identification');
    const [applyId, setApplyId] = useState<string>('');

    const { data: me } = useQuery({
        queryKey: ['currentUser'],
        queryFn: async () => (await api.get('/users/me')).data as User,
        staleTime: 5 * 60 * 1000,
    });
    const canEdit = me?.role === 'admin' || me?.role === 'analyst' || me?.is_super_admin;

    const key = ['case-tasks', caseId];
    const { data: tasks = [], isLoading } = useQuery({
        queryKey: key,
        queryFn: async () => (await api.get(`/cases/${caseId}/tasks`)).data as CaseTask[],
    });
    const { data: playbooks = [] } = useQuery({
        queryKey: ['playbooks', 'mine'],
        queryFn: async () => (await api.get('/playbooks/')).data as PlaybookSummary[],
        enabled: canEdit,
    });

    const invalidate = () => {
        qc.invalidateQueries({ queryKey: key });
        // task changes write Activity entries + audit rows
        qc.invalidateQueries({ queryKey: ['case', String(caseId)] });
        qc.invalidateQueries({ queryKey: ['audit-logs', 'case', String(caseId)] });
    };
    const toggle = useMutation({
        mutationFn: async (t: CaseTask) => api.put(`/cases/${caseId}/tasks/${t.id}`, { status: t.status === 'done' ? 'todo' : 'done' }),
        onSuccess: invalidate,
    });
    const remove = useMutation({
        mutationFn: async (id: number) => api.delete(`/cases/${caseId}/tasks/${id}`),
        onSuccess: invalidate,
    });
    const add = useMutation({
        mutationFn: async () => api.post(`/cases/${caseId}/tasks`, { phase: newPhase, title: newTitle }),
        onSuccess: () => { setNewTitle(''); setAdding(false); invalidate(); },
    });
    const apply = useMutation({
        mutationFn: async (templateId: number) => api.post(`/cases/${caseId}/apply-playbook/${templateId}`),
        onSuccess: () => { setApplyId(''); invalidate(); },
    });

    if (isLoading) return <p className="font-mono text-xs text-muted">$ loading tasks…</p>;

    const done = tasks.filter((t) => t.status === 'done').length;
    const pct = tasks.length ? Math.round((done / tasks.length) * 100) : 0;
    const grouped = PHASES.map((p) => [p, tasks.filter((t) => t.phase === p)] as [string, CaseTask[]]).filter(([, l]) => l.length > 0);

    return (
        <div className="space-y-4">
            {/* Progress + apply playbook */}
            <div className="flex items-center justify-between gap-4 flex-wrap">
                <div className="flex items-center gap-3 flex-1 min-w-[200px]">
                    <span className="num text-sm text-fg">{done}/{tasks.length}</span>
                    <div className="flex-1 h-2 bg-raised max-w-xs">
                        <div className="h-full bg-accent transition-all" style={{ width: `${pct}%` }} />
                    </div>
                    <span className="num text-xs text-fg-3">{pct}%</span>
                </div>
                {canEdit && (
                    <div className="flex items-center gap-2">
                        <div className="relative">
                            <select value={applyId} onChange={(e) => setApplyId(e.target.value)}
                                className="appearance-none border border-line-strong bg-panel text-sm pl-2.5 pr-7 py-1.5 focus:outline-hidden focus:ring-1 focus:ring-accent">
                                <option value="">Apply playbook…</option>
                                {playbooks.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
                            </select>
                            <ChevronDown size={13} className="absolute right-2 top-2.5 text-muted pointer-events-none" />
                        </div>
                        <button onClick={() => applyId && apply.mutate(Number(applyId))} disabled={!applyId || apply.isPending}
                            className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-accent text-on-accent text-sm font-medium hover:bg-accent-hover disabled:opacity-50">
                            <BookText size={14} /> Apply
                        </button>
                    </div>
                )}
            </div>

            {playbooks.length === 0 && canEdit && (
                <p className="text-xs text-muted">No playbooks imported yet — import some from the Playbooks page to apply them here.</p>
            )}

            {tasks.length === 0 ? (
                <div className="border border-dashed border-line-strong bg-panel p-8 text-center">
                    <p className="text-sm text-fg-2">No tasks yet. Apply a playbook or add tasks manually.</p>
                </div>
            ) : (
                <div className="space-y-4">
                    {grouped.map(([phase, list]) => (
                        <div key={phase} className="bg-panel border border-line">
                            <div className="flex items-center gap-2 px-4 h-9 border-b border-line">
                                <span className="w-2 h-2" style={{ background: PHASE_COLOR[phase] }} />
                                <span className="label-mono">{PHASE_LABEL[phase]}</span>
                                <span className="num text-[10px] text-muted ml-auto">
                                    {list.filter((t) => t.status === 'done').length}/{list.length}
                                </span>
                            </div>
                            <ul className="divide-y divide-line">
                                {list.map((t) => (
                                    <li key={t.id} className="flex items-start gap-3 px-4 py-2.5 group hover:bg-surface">
                                        <button onClick={() => canEdit && toggle.mutate(t)} disabled={!canEdit}
                                            className={cn('mt-0.5 w-4 h-4 border flex items-center justify-center shrink-0',
                                                t.status === 'done' ? 'bg-accent border-accent text-on-accent' : 'border-line-strong hover:border-accent/40',
                                                !canEdit && 'cursor-default')}>
                                            {t.status === 'done' && <Check size={11} />}
                                        </button>
                                        <div className="flex-1 min-w-0">
                                            <p className={cn('text-sm', t.status === 'done' ? 'text-muted line-through' : 'text-fg')}>{t.title}</p>
                                            {t.description && <p className="text-xs text-fg-3">{t.description}</p>}
                                        </div>
                                        {canEdit && (
                                            <button onClick={() => remove.mutate(t.id)}
                                                className="opacity-0 group-hover:opacity-100 text-muted hover:text-severity-critical shrink-0">
                                                <Trash2 size={14} />
                                            </button>
                                        )}
                                    </li>
                                ))}
                            </ul>
                        </div>
                    ))}
                </div>
            )}

            {/* Add ad-hoc task */}
            {canEdit && (
                adding ? (
                    <div className="flex items-center gap-2 bg-panel border border-line p-2">
                        <select value={newPhase} onChange={(e) => setNewPhase(e.target.value)}
                            className="border border-line-strong text-xs px-1.5 py-1.5 font-mono w-32">
                            {PHASES.map((p) => <option key={p} value={p}>{PHASE_LABEL[p]}</option>)}
                        </select>
                        <input autoFocus value={newTitle} onChange={(e) => setNewTitle(e.target.value)}
                            onKeyDown={(e) => e.key === 'Enter' && newTitle && add.mutate()}
                            placeholder="Task title" className="flex-1 border border-line-strong px-2 py-1.5 text-sm focus:outline-hidden focus:ring-1 focus:ring-accent" />
                        <button onClick={() => newTitle && add.mutate()} disabled={!newTitle}
                            className="px-3 py-1.5 bg-accent text-on-accent text-sm hover:bg-accent-hover disabled:opacity-50">Add</button>
                        <button onClick={() => setAdding(false)} className="px-2 text-sm text-fg-3">Cancel</button>
                    </div>
                ) : (
                    <button onClick={() => setAdding(true)}
                        className="inline-flex items-center gap-1.5 text-sm text-accent font-medium hover:text-accent">
                        <Plus size={15} /> Add task
                    </button>
                )
            )}
        </div>
    );
}
