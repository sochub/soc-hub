import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Pencil, Trash2 } from 'lucide-react';
import { api } from '../../api/client';
import type { User } from '../../types';
import { Badge, Button, Input, Skeleton, ErrorState, useConfirm } from '../../components/ui';
import { formatDuration, useCaseTime, type TimeEntry } from './timeApi';

function apiError(e: unknown): string {
    const d = (e as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
    return typeof d === 'string' ? d : 'Request failed';
}

export default function CaseTime({ caseId }: { caseId: number }) {
    const qc = useQueryClient();
    const { data: me } = useQuery({
        queryKey: ['currentUser'],
        queryFn: async () => (await api.get('/users/me')).data as User,
        staleTime: 300_000,
    });
    const isAdmin = !!me && (me.role === 'admin' || !!me.is_super_admin);
    const canWrite = isAdmin || me?.role === 'analyst';
    const { data, isLoading, isError, refetch } = useCaseTime(caseId);
    const { confirm, dialog } = useConfirm();
    const refresh = () => qc.invalidateQueries({ queryKey: ['case-time', caseId] });

    const [minutes, setMinutes] = useState('');
    const [note, setNote] = useState('');
    const [editId, setEditId] = useState<number | null>(null);
    const [editMinutes, setEditMinutes] = useState('');
    const [editNote, setEditNote] = useState('');
    const [error, setError] = useState<string | null>(null);

    const add = useMutation({
        mutationFn: async () => {
            const ended = new Date();
            const started = new Date(ended.getTime() - Number(minutes) * 60_000);
            await api.post(`/cases/${caseId}/time`, {
                started_at: started.toISOString(), ended_at: ended.toISOString(), note: note.trim() || null,
            });
        },
        onSuccess: () => { setMinutes(''); setNote(''); setError(null); refresh(); },
        onError: (e) => setError(apiError(e)),
    });
    const save = useMutation({
        mutationFn: async (e: TimeEntry) => {
            const ended = new Date(new Date(e.started_at).getTime() + Number(editMinutes) * 60_000);
            await api.patch(`/cases/${caseId}/time/${e.id}`, { ended_at: ended.toISOString(), note: editNote.trim() || null });
        },
        onSuccess: () => { setEditId(null); setError(null); refresh(); },
        onError: (e) => setError(apiError(e)),
    });
    const remove = useMutation({
        mutationFn: async (id: number) => { await api.delete(`/cases/${caseId}/time/${id}`); },
        onSuccess: () => { setError(null); refresh(); },
        onError: (e) => setError(apiError(e)),
    });

    const who = (uid: number) => data?.by_user.find((u) => u.user_id === uid)?.email ?? `User ${uid}`;
    const fmt = (iso: string) => new Date(iso).toLocaleString(undefined, { timeZone: me?.timezone || undefined });
    const minsValid = Number(minutes) > 0 && Number.isFinite(Number(minutes));

    if (isLoading) return <Skeleton rows={3} />;
    if (isError || !data) return <ErrorState message="Could not load time." onRetry={() => refetch()} />;

    return (
        <div className="space-y-6">
            {dialog}
            <div className="flex flex-wrap items-center gap-2">
                <span className="text-sm text-fg-2">Total</span>
                <span className="text-lg font-semibold text-fg num">{formatDuration(data.total_seconds)}</span>
                {data.by_user.map((u) => (
                    <Badge key={u.user_id}>{u.email}: {formatDuration(u.seconds)}</Badge>
                ))}
            </div>

            {canWrite && (
                <form className="flex flex-wrap items-end gap-3" onSubmit={(e) => { e.preventDefault(); if (minsValid) add.mutate(); }}>
                    <label className="text-xs font-medium text-fg-2">Minutes
                        <Input type="number" min={1} value={minutes} onChange={(e) => setMinutes(e.target.value)} className="w-28 mt-1" />
                    </label>
                    <label className="text-xs font-medium text-fg-2 flex-1 min-w-48">Note (optional)
                        <Input value={note} maxLength={500} onChange={(e) => setNote(e.target.value)} className="mt-1" />
                    </label>
                    <Button type="submit" size="md" disabled={!minsValid || add.isPending}>Add time</Button>
                </form>
            )}
            {error && <p role="alert" className="text-sm text-severity-critical">{error}</p>}

            {data.entries.length === 0 ? (
                <p className="text-sm text-fg-3">No time recorded yet.</p>
            ) : (
                <div className="overflow-x-auto rounded-md border border-line">
                <table className="w-full text-sm">
                    <thead>
                        <tr className="text-left label-caps border-b border-line">
                            <th className="px-3 py-2">Analyst</th><th className="px-3 py-2">Started</th><th className="px-3 py-2">Duration</th>
                            <th className="px-3 py-2">Source</th><th className="px-3 py-2">Note</th><th className="px-3 py-2"><span className="sr-only">Actions</span></th>
                        </tr>
                    </thead>
                    <tbody>
                        {data.entries.map((e) => {
                            const mine = me?.id === e.user_id || isAdmin;
                            const editing = editId === e.id;
                            return (
                                <tr key={e.id} className="border-b border-line last:border-b-0 text-fg-2 hover:bg-raised">
                                    <td className="px-3 py-2.5">{who(e.user_id)}</td>
                                    <td className="px-3 py-2.5 whitespace-nowrap num">{fmt(e.started_at)}</td>
                                    <td className="px-3 py-2.5 num">
                                        {editing
                                            ? <Input type="number" min={1} value={editMinutes} onChange={(ev) => setEditMinutes(ev.target.value)} className="w-24" aria-label="Minutes" />
                                            : formatDuration(e.seconds)}
                                    </td>
                                    <td className="px-3 py-2.5">
                                        {e.source === 'auto'
                                            ? <Badge variant="info">Estimated from case activity</Badge>
                                            : <Badge>Manual</Badge>}
                                    </td>
                                    <td className="px-3 py-2.5">
                                        {editing
                                            ? <Input value={editNote} maxLength={500} onChange={(ev) => setEditNote(ev.target.value)} aria-label="Note" />
                                            : e.note}
                                    </td>
                                    <td className="px-3 py-2.5 text-right whitespace-nowrap">
                                        {mine && (editing ? (
                                            <>
                                                <Button size="sm" disabled={!(Number(editMinutes) > 0) || save.isPending} onClick={() => save.mutate(e)}>Save</Button>
                                                <Button size="sm" variant="ghost" onClick={() => setEditId(null)}>Cancel</Button>
                                            </>
                                        ) : (
                                            <>
                                                <button aria-label="Edit entry" className="p-1.5 rounded-sm text-fg-3 hover:text-fg hover:bg-raised"
                                                    onClick={() => { setEditId(e.id); setEditMinutes(String(Math.max(1, Math.round(e.seconds / 60)))); setEditNote(e.note ?? ''); }}>
                                                    <Pencil size={14} />
                                                </button>
                                                <button aria-label="Delete entry" className="p-1.5 rounded-sm text-fg-3 hover:text-severity-critical hover:bg-raised"
                                                    disabled={remove.isPending} onClick={async () => { if (await confirm({ title: 'Delete this time entry?', danger: true })) remove.mutate(e.id); }}>
                                                    <Trash2 size={14} />
                                                </button>
                                            </>
                                        ))}
                                    </td>
                                </tr>
                            );
                        })}
                    </tbody>
                </table>
                </div>
            )}
        </div>
    );
}
