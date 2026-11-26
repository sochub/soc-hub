import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Check, Pencil, Trash2, X } from 'lucide-react';
import { api } from '../../api/client';
import { Badge, Button, Input, Select, useConfirm } from '../../components/ui';
import type { Case, User } from '../../types';
import { formatDateTime } from '../../utils/datetime';
import { DATE_INPUT_BOUNDS, apiDetail, isoToLocal, localToIso } from './milestones';

const STATUSES = ['new', 'open', 'in_progress', 'pending', 'resolved', 'closed'];
const STATUS_RE = /^Status changed from (\w+) to (\w+)/;
const label = (s: string) => s.replace(/_/g, ' ');

/** The status changes behind the glance's phase bar, oldest first: correct a change's time or statuses, or delete one
 *  made by mistake. The case's current status never changes; the backend re-derives resolved_at from the history. */
export default function StatusHistoryEditor({ caseData, tz, onDone }: { caseData: Case; tz?: string | null; onDone: () => void }) {
    const qc = useQueryClient();
    const { confirm, dialog } = useConfirm();
    const [edit, setEdit] = useState<{ id: number; at: string; from: string; to: string } | null>(null);
    const { data: me } = useQuery({ queryKey: ['currentUser'], queryFn: async () => (await api.get('/users/me')).data as User, staleTime: 300_000 });
    const isAdmin = !!me && (me.role === 'admin' || !!me.is_super_admin);

    const rows = (caseData.timeline_events ?? [])
        .filter((e) => e.event_type === 'status_change')
        .flatMap((e) => { const m = STATUS_RE.exec(e.content); return m ? [{ e, from: m[1], to: m[2] }] : []; })
        .sort((a, b) => +new Date(a.e.created_at) - +new Date(b.e.created_at) || a.e.id - b.e.id);
    const endsAt = rows.length ? rows[rows.length - 1].to : null;
    const refresh = () => qc.invalidateQueries({ queryKey: ['case', String(caseData.id)] });
    const base = `/cases/${caseData.id}/status-history`;

    const save = useMutation({
        mutationFn: async () => {
            const at = edit && localToIso(edit.at);
            if (!edit || !at) throw new Error('Enter a valid date and time.');
            return api.put(`${base}/${edit.id}`, { at, from_status: edit.from, to_status: edit.to });
        },
        onSuccess: () => { setEdit(null); refresh(); },
    });
    const remove = useMutation({ mutationFn: async (id: number) => api.delete(`${base}/${id}`), onSuccess: refresh });
    const error = save.error ?? remove.error;

    return (
        <div className="space-y-3">
            {dialog}
            <div className="flex items-start justify-between gap-2">
                <p className="text-xs text-fg-3 max-w-[80ch]">
                    The status changes behind the bar, oldest first. Fix a wrong time or status, or delete a change made by mistake.
                    The case stays <strong>{label(caseData.status)}</strong>; its resolved time is recalculated, and every correction is in the audit log.
                </p>
                <Button variant="ghost" size="icon" onClick={onDone} aria-label="Close"><X size={16} /></Button>
            </div>
            {endsAt && endsAt !== caseData.status && (
                <p role="status" className="text-xs text-severity-high">
                    The history ends at <strong>{label(endsAt)}</strong>, but the case is <strong>{label(caseData.status)}</strong>.
                </p>
            )}
            {!rows.length && <p className="text-xs text-muted">No status changes recorded for this case.</p>}
            <ol className="space-y-1.5">
                {rows.map(({ e, from, to }) => {
                    const mine = isAdmin || (me?.role === 'analyst' && e.user_id === me.id);
                    const who = e.user?.full_name || e.user?.email;
                    return (
                        <li key={e.id} className="rounded-md border border-line bg-panel px-3 py-2">
                            {edit?.id === e.id ? (
                                <div className="flex flex-wrap items-center gap-2">
                                    <Input type="datetime-local" value={edit.at} {...DATE_INPUT_BOUNDS} aria-label="When"
                                        onChange={(ev) => setEdit({ ...edit, at: ev.target.value })} className="w-auto" />
                                    <Select value={edit.from} onChange={(ev) => setEdit({ ...edit, from: ev.target.value })} aria-label="From status" className="w-auto">
                                        {STATUSES.map((s) => <option key={s} value={s}>{label(s)}</option>)}
                                    </Select>
                                    <span className="text-xs text-muted">→</span>
                                    <Select value={edit.to} onChange={(ev) => setEdit({ ...edit, to: ev.target.value })} aria-label="To status" className="w-auto">
                                        {STATUSES.map((s) => <option key={s} value={s}>{label(s)}</option>)}
                                    </Select>
                                    <span className="ml-auto flex gap-2">
                                        <Button size="sm" variant="secondary" onClick={() => { setEdit(null); save.reset(); }}>Cancel</Button>
                                        <Button size="sm" onClick={() => save.mutate()} disabled={save.isPending || edit.from === edit.to}>
                                            <Check size={12} aria-hidden /> Save
                                        </Button>
                                    </span>
                                </div>
                            ) : (
                                <div className="flex flex-wrap items-center gap-2 text-xs">
                                    <span className="num text-fg-3">{formatDateTime(e.created_at, tz)}</span>
                                    <span className="text-fg">{label(from)} → {label(to)}</span>
                                    {who && <span className="text-fg-3">by {who}</span>}
                                    {e.via === 'mcp' && <Badge title="Made by an MCP client acting for this user">via MCP{e.via_client ? ` · ${e.via_client}` : ''}</Badge>}
                                    {mine && (
                                        <span className="ml-auto flex items-center gap-2">
                                            <button onClick={() => { save.reset(); setEdit({ id: e.id, at: isoToLocal(e.created_at), from, to }); }}
                                                title="Correct" aria-label="Correct this status change" className="text-fg-3 hover:text-fg"><Pencil size={13} /></button>
                                            <button onClick={async () => {
                                                if (await confirm({
                                                    title: `Delete “${label(from)} → ${label(to)}”?`, danger: true, confirmLabel: 'Delete change',
                                                    body: `Made ${formatDateTime(e.created_at, tz)}${who ? ` by ${who}` : ''}. The case stays ${label(caseData.status)}; the bar and the resolved time are recalculated from the remaining changes. The audit log keeps the deleted entry.`,
                                                })) remove.mutate(e.id);
                                            }} title="Delete" aria-label="Delete this status change" className="text-fg-3 hover:text-severity-critical"><Trash2 size={13} /></button>
                                        </span>
                                    )}
                                </div>
                            )}
                        </li>
                    );
                })}
            </ol>
            {error && <p role="alert" className="text-xs text-severity-critical">{error instanceof Error && !('response' in error) ? error.message : apiDetail(error)}</p>}
        </div>
    );
}
