import { useMutation, useQueries, useQueryClient } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { useState } from 'react';
import { api } from '../../api/client';
import { cn } from '../../lib/utils';
import { SLABadge } from './SLABadge';
import Modal, { modalInput, modalLabel, btnPrimary, btnSecondary } from '../../components/layout/Modal';
import { CASE_OUTCOMES } from './outcomes';
import type { SLAStatus } from '../../types';
import { getPage, type Page } from '../../api/paging';

export interface BoardCase {
    id: number;
    title: string;
    severity: string;
    status: string;
    tags?: string[];
    sla_overall_status?: SLAStatus | null;
    outcome?: string | null;
    closing_note?: string | null;
}

type Move = { id: number; status: string; outcome?: string; closing_note?: string };

const errorDetail = (e: unknown) => {
    const d = (e as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
    return typeof d === 'string' ? d : 'Could not move the case.';
};

// Mirrors backend CaseStatus enum
const MAX_TAGS = 3;
const COLUMNS = ['new', 'open', 'in_progress', 'pending', 'resolved', 'closed'];
// Cards loaded per column; a column with more says so instead of silently dropping the rest.
const COLUMN_CAP = 100;

export default function CasesBoard({ filters, status: statusFilter, severityConfig }: {
    filters: Record<string, string>;
    status: string; // 'all' or one status: then only that column loads
    severityConfig: Record<string, { color: string; bg: string; border: string }>;
}) {
    const queryClient = useQueryClient();
    const columnKey = (status: string) => ['cases', 'board', status, filters];
    const columns = useQueries({
        queries: COLUMNS.map(status => ({
            queryKey: columnKey(status),
            queryFn: () => getPage<BoardCase>('/cases/', { ...filters, status, limit: COLUMN_CAP }),
            enabled: statusFilter === 'all' || statusFilter === status,
        })),
    });
    const columnOf = (status: string) => columns[COLUMNS.indexOf(status)]?.data ?? { items: [], total: 0 };
    const cases = COLUMNS.flatMap(s => columnOf(s).items);
    const [dragOver, setDragOver] = useState<string | null>(null);
    const [error, setError] = useState<string | null>(null);
    // Dropping on Closed asks for the outcome + closing note first; the card stays put until confirmed.
    const [closing, setClosing] = useState<{ id: number; outcome: string; closing_note: string } | null>(null);

    const move = useMutation({
        mutationFn: ({ id, ...body }: Move) => api.put(`/cases/${id}`, body),
        // Optimistic: move the card now, roll back if the API refuses (e.g. viewer role, closure fields missing)
        onMutate: async ({ id, status }) => {
            setError(null);
            await queryClient.cancelQueries({ queryKey: ['cases', 'board'] });
            const prev = queryClient.getQueriesData<Page<BoardCase>>({ queryKey: ['cases', 'board'] });
            const card = cases.find(c => c.id === id);
            if (card) {
                const from = card.status;
                queryClient.setQueryData<Page<BoardCase>>(columnKey(from), old => old && ({ items: old.items.filter(c => c.id !== id), total: old.total - 1 }));
                queryClient.setQueryData<Page<BoardCase>>(columnKey(status), old => old && ({ items: [{ ...card, status }, ...old.items], total: old.total + 1 }));
            }
            return { prev };
        },
        onError: (e, _v, ctx) => {
            for (const [key, data] of ctx?.prev ?? []) queryClient.setQueryData(key, data);
            setError(errorDetail(e));
        },
        onSettled: () => queryClient.invalidateQueries({ queryKey: ['cases'] }),
    });

    const onDrop = (e: React.DragEvent, status: string) => {
        e.preventDefault();
        setDragOver(null);
        const id = Number(e.dataTransfer.getData('text/plain'));
        const c = cases.find(x => x.id === id);
        if (!c || c.status === status) return;
        if (status === 'closed') setClosing({ id, outcome: c.outcome ?? '', closing_note: c.closing_note ?? '' });
        else move.mutate({ id, status });
    };

    const canClose = !!closing?.outcome && !!closing.closing_note.trim();
    const confirmClose = () => {
        if (!closing || !canClose) return;
        move.mutate({ id: closing.id, status: 'closed', outcome: closing.outcome, closing_note: closing.closing_note });
        setClosing(null);
    };

    return (
        <>
        {error && (
            <div role="alert" className="mb-3 px-3 py-2 rounded-md border border-severity-critical/40 bg-severity-critical/10 text-sm text-severity-critical flex items-center justify-between gap-3">
                <span>{error}</span>
                <button onClick={() => setError(null)} className="text-xs text-fg-2 hover:text-fg">Dismiss</button>
            </div>
        )}
        <div className="flex gap-3 overflow-x-auto pb-2">
            {COLUMNS.map(status => {
                const { items: col, total } = columnOf(status);
                return (
                    <div
                        key={status}
                        onDragOver={e => { e.preventDefault(); setDragOver(status); }}
                        onDragLeave={() => setDragOver(null)}
                        onDrop={e => onDrop(e, status)}
                        className={cn(
                            "flex-1 min-w-56 bg-surface border border-line rounded-lg flex flex-col max-h-[calc(100vh-12rem)]",
                            dragOver === status && "border-accent bg-accent/10"
                        )}
                    >
                        <div className="px-3 py-2 border-b border-line flex items-center justify-between">
                            <span className="label-mono text-xs uppercase tracking-wider text-fg-2">{status.replace('_', ' ')}</span>
                            <span className="num text-xs text-muted">{total}</span>
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
                                        className="block bg-panel border border-line rounded-md p-2.5 hover:border-accent cursor-grab active:cursor-grabbing"
                                    >
                                        <div className="flex items-center justify-between gap-2 mb-1">
                                            <span className="font-mono text-xs text-muted">#{c.id}</span>
                                            <span className={cn("px-1.5 py-0.5 rounded-sm text-[10px] font-bold uppercase border", sev.color, sev.bg, sev.border)}>
                                                {c.severity}
                                            </span>
                                        </div>
                                        <div className="text-sm font-medium text-fg line-clamp-2">{c.title}</div>
                                        {!!c.tags?.length && (
                                            <div className="flex flex-wrap gap-1 mt-1.5">
                                                {c.tags.slice(0, MAX_TAGS).map(t => (
                                                    <span key={t} className="px-1.5 py-0.5 rounded-sm text-[10px] font-mono bg-raised text-fg-2 border border-line truncate max-w-full">{t}</span>
                                                ))}
                                                {c.tags.length > MAX_TAGS && (
                                                    <span className="px-1 py-0.5 text-[10px] font-mono text-muted" title={c.tags.slice(MAX_TAGS).join(', ')}>+{c.tags.length - MAX_TAGS}</span>
                                                )}
                                            </div>
                                        )}
                                        {c.sla_overall_status && <div className="mt-1.5"><SLABadge status={c.sla_overall_status} /></div>}
                                    </Link>
                                );
                            })}
                            {total > col.length && (
                                <p className="px-1 py-1 text-[11px] text-muted">
                                    Showing {col.length} of {total}. Narrow the filters or use the table view for the rest.
                                </p>
                            )}
                        </div>
                    </div>
                );
            })}
        </div>
        <Modal
            open={!!closing}
            onClose={() => setClosing(null)}
            title={`Close case #${closing?.id ?? ''}`}
            size="md"
            footer={<>
                <button onClick={() => setClosing(null)} className={btnSecondary}>Cancel</button>
                <button onClick={confirmClose} disabled={!canClose} className={btnPrimary}>Close case</button>
            </>}
        >
            {closing && (
                <div className="space-y-4">
                    <div>
                        <label htmlFor="close-outcome" className={modalLabel}>Outcome <span className="text-muted font-normal">(required)</span></label>
                        <select
                            id="close-outcome"
                            value={closing.outcome}
                            onChange={e => setClosing({ ...closing, outcome: e.target.value })}
                            className={modalInput}
                        >
                            <option value="">Select an outcome</option>
                            {CASE_OUTCOMES.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
                        </select>
                    </div>
                    <div>
                        <label htmlFor="close-note" className={modalLabel}>Closing note <span className="text-muted font-normal">(required: the findings behind this decision)</span></label>
                        <textarea
                            id="close-note"
                            value={closing.closing_note}
                            onChange={e => setClosing({ ...closing, closing_note: e.target.value })}
                            className={`${modalInput} min-h-[100px]`}
                            maxLength={8000}
                        />
                    </div>
                </div>
            )}
        </Modal>
        </>
    );
}
