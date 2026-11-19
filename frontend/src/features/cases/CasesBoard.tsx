import { useMutation, useQueries, useQueryClient } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { useState } from 'react';
import { api } from '../../api/client';
import { cn } from '../../lib/utils';
import { SLABadge } from './SLABadge';
import Modal from '../../components/layout/Modal';
import { Button, ErrorState, Label, Select, SeverityStamp, Textarea } from '../../components/ui';
import { CASE_STATUSES, statusLabel } from './statuses';
import { CASE_OUTCOMES } from './outcomes';
import type { SLAStatus } from '../../types';
import { getPage, OPEN_CASE_STATUSES, type Page } from '../../api/paging';

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

const MAX_TAGS = 3;
const COLUMNS = CASE_STATUSES.map(s => s.value as string);
// Cards loaded per column; a column with more says so instead of silently dropping the rest.
const COLUMN_CAP = 100;

export default function CasesBoard({ filters, status: statusFilter }: {
    filters: Record<string, string>;
    status: string; // 'all', 'active' (open statuses) or one status: only those columns load
}) {
    const queryClient = useQueryClient();
    const columnKey = (status: string) => ['cases', 'board', status, filters];
    const columns = useQueries({
        queries: COLUMNS.map(status => ({
            queryKey: columnKey(status),
            queryFn: () => getPage<BoardCase>('/cases/', { ...filters, status, limit: COLUMN_CAP }),
            enabled: statusFilter === 'all' || statusFilter === status || (statusFilter === 'active' && OPEN_CASE_STATUSES.includes(status)),
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

    // Shared by drag and drop and the per-card "Move to…" select (the keyboard path).
    const moveTo = (id: number, status: string) => {
        const c = cases.find(x => x.id === id);
        if (!c || c.status === status) return;
        if (status === 'closed') setClosing({ id, outcome: c.outcome ?? '', closing_note: c.closing_note ?? '' });
        else move.mutate({ id, status });
    };
    const onDrop = (e: React.DragEvent, status: string) => {
        e.preventDefault();
        setDragOver(null);
        moveTo(Number(e.dataTransfer.getData('text/plain')), status);
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
            <div className="mb-3 flex items-center gap-2">
                <ErrorState message={error} className="flex-1" />
                <Button size="sm" variant="ghost" onClick={() => setError(null)}>Dismiss</Button>
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
                            "flex-1 min-w-56 bg-surface border border-line rounded-md flex flex-col max-h-[calc(100vh-12rem)]",
                            dragOver === status && "border-accent bg-accent/10"
                        )}
                    >
                        <div className="px-3 py-2 border-b border-line flex items-center justify-between">
                            <h3 className="label-caps">{statusLabel(status)}</h3>
                            <span className="num text-xs text-fg-3">{total}</span>
                        </div>
                        <div className="p-2 space-y-2 overflow-y-auto">
                            {col.map(c => (
                                <article
                                    key={c.id}
                                    draggable
                                    onDragStart={e => e.dataTransfer.setData('text/plain', String(c.id))}
                                    className="group/card relative bg-panel border border-line rounded-md p-2.5 hover:border-line-strong focus-within:border-accent cursor-grab active:cursor-grabbing"
                                >
                                    <div className="flex items-center justify-between gap-2 mb-1">
                                        <span className="num text-xs text-fg-3">#{c.id}</span>
                                        <SeverityStamp severity={c.severity} />
                                    </div>
                                    <Link to={`/cases/${c.id}`} className="block text-sm font-medium text-fg line-clamp-2 hover:text-accent hover:underline">{c.title}</Link>
                                    {!!c.tags?.length && (
                                        <div className="flex flex-wrap gap-1 mt-1.5">
                                            {c.tags.slice(0, MAX_TAGS).map(t => (
                                                <span key={t} className="px-1.5 py-0.5 rounded-sm text-xs bg-raised text-fg-2 border border-line truncate max-w-full">{t}</span>
                                            ))}
                                            {c.tags.length > MAX_TAGS && (
                                                <span className="px-1 py-0.5 text-xs text-fg-3" title={c.tags.slice(MAX_TAGS).join(', ')}>+{c.tags.length - MAX_TAGS}</span>
                                            )}
                                        </div>
                                    )}
                                    {c.sla_overall_status && <div className="mt-1.5"><SLABadge status={c.sla_overall_status} /></div>}
                                    <Select
                                        inputSize="sm"
                                        aria-label={`Move case #${c.id} to`}
                                        value=""
                                        onChange={e => { if (e.target.value) moveTo(c.id, e.target.value); }}
                                        className="mt-2 w-full cursor-pointer opacity-0 group-hover/card:opacity-100 focus:opacity-100 [@media(hover:none)]:opacity-100"
                                    >
                                        <option value="">Move to…</option>
                                        {CASE_STATUSES.filter(s => s.value !== c.status).map(s => <option key={s.value} value={s.value}>{s.label}</option>)}
                                    </Select>
                                </article>
                            ))}
                            {total > col.length && (
                                <p className="px-1 py-1 text-xs text-fg-3">
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
                <Button variant="secondary" onClick={() => setClosing(null)}>Cancel</Button>
                <Button onClick={confirmClose} disabled={!canClose}>Close case</Button>
            </>}
        >
            {closing && (
                <div className="space-y-4">
                    <div>
                        <Label htmlFor="close-outcome">Outcome <span className="text-fg-3 font-normal">(required)</span></Label>
                        <Select
                            id="close-outcome"
                            value={closing.outcome}
                            onChange={e => setClosing({ ...closing, outcome: e.target.value })}
                        >
                            <option value="">Select an outcome</option>
                            {CASE_OUTCOMES.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
                        </Select>
                    </div>
                    <div>
                        <Label htmlFor="close-note">Closing note <span className="text-fg-3 font-normal">(required: the findings behind this decision)</span></Label>
                        <Textarea
                            id="close-note"
                            value={closing.closing_note}
                            onChange={e => setClosing({ ...closing, closing_note: e.target.value })}
                            className="min-h-[100px]"
                            maxLength={8000}
                        />
                    </div>
                </div>
            )}
        </Modal>
        </>
    );
}
