import { useMutation, useQueryClient } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { useState } from 'react';
import { api } from '../../api/client';
import { cn } from '../../lib/utils';
import { SLABadge } from './SLABadge';
import type { SLAStatus } from '../../types';

export interface BoardCase {
    id: number;
    title: string;
    severity: string;
    status: string;
    sla_overall_status?: SLAStatus | null;
}

// Mirrors backend CaseStatus enum
const COLUMNS = ['new', 'open', 'in_progress', 'pending', 'resolved', 'closed'];

export default function CasesBoard({ cases, severityConfig }: {
    cases: BoardCase[];
    severityConfig: Record<string, { color: string; bg: string; border: string }>;
}) {
    const queryClient = useQueryClient();
    const [dragOver, setDragOver] = useState<string | null>(null);

    const move = useMutation({
        mutationFn: ({ id, status }: { id: number; status: string }) => api.put(`/cases/${id}`, { status }),
        // Optimistic: move the card now, roll back if the API refuses (e.g. viewer role)
        onMutate: async ({ id, status }) => {
            await queryClient.cancelQueries({ queryKey: ['cases'], exact: true });
            const prev = queryClient.getQueryData<BoardCase[]>(['cases']);
            queryClient.setQueryData<BoardCase[]>(['cases'], old => old?.map(c => c.id === id ? { ...c, status } : c));
            return { prev };
        },
        onError: (_e, _v, ctx) => queryClient.setQueryData(['cases'], ctx?.prev),
        onSettled: () => queryClient.invalidateQueries({ queryKey: ['cases'], exact: true }),
    });

    const onDrop = (e: React.DragEvent, status: string) => {
        e.preventDefault();
        setDragOver(null);
        const id = Number(e.dataTransfer.getData('text/plain'));
        const c = cases.find(x => x.id === id);
        if (c && c.status !== status) move.mutate({ id, status });
    };

    return (
        <div className="flex gap-3 overflow-x-auto pb-2">
            {COLUMNS.map(status => {
                const col = cases.filter(c => c.status === status);
                return (
                    <div
                        key={status}
                        onDragOver={e => { e.preventDefault(); setDragOver(status); }}
                        onDragLeave={() => setDragOver(null)}
                        onDrop={e => onDrop(e, status)}
                        className={cn(
                            "flex-1 min-w-56 bg-zinc-50 border border-zinc-200 rounded-lg flex flex-col max-h-[calc(100vh-12rem)]",
                            dragOver === status && "border-accent-500 bg-blue-50/50"
                        )}
                    >
                        <div className="px-3 py-2 border-b border-zinc-200 flex items-center justify-between">
                            <span className="label-mono text-xs uppercase tracking-wider text-zinc-600">{status.replace('_', ' ')}</span>
                            <span className="num text-xs text-zinc-400">{col.length}</span>
                        </div>
                        <div className="p-2 space-y-2 overflow-y-auto">
                            {col.map(c => {
                                const sev = severityConfig[c.severity] || severityConfig.info;
                                return (
                                    <Link
                                        key={c.id}
                                        to={`/cases/${c.id}`}
                                        draggable
                                        onDragStart={e => e.dataTransfer.setData('text/plain', String(c.id))}
                                        className="block bg-white border border-zinc-200 rounded-md p-2.5 hover:border-accent-500 cursor-grab active:cursor-grabbing"
                                    >
                                        <div className="flex items-center justify-between gap-2 mb-1">
                                            <span className="font-mono text-xs text-zinc-400">#{c.id}</span>
                                            <span className={cn("px-1.5 py-0.5 rounded-sm text-[10px] font-bold uppercase border", sev.color, sev.bg, sev.border)}>
                                                {c.severity}
                                            </span>
                                        </div>
                                        <div className="text-sm font-medium text-zinc-800 line-clamp-2">{c.title}</div>
                                        {c.sla_overall_status && <div className="mt-1.5"><SLABadge status={c.sla_overall_status} /></div>}
                                    </Link>
                                );
                            })}
                        </div>
                    </div>
                );
            })}
        </div>
    );
}
