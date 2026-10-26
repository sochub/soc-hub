import { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { Check, Eye, EyeOff, Pencil, Pin, PinOff, Trash2, X } from 'lucide-react';
import { api } from '../../api/client';
import { cn } from '../../lib/utils';
import Avatar from '../../components/Avatar';
import { Markdown, MarkdownField } from '../../components/Markdown';
import { formatDateTime } from '../../utils/datetime';
import MentionTextarea from './MentionTextarea';
import { stripMentions } from './mentionChips';
import type { TimelineEvent, User } from '../../types';
import { NOTE_TYPES } from './notes';

const field = 'w-full bg-panel border border-line rounded-lg px-3 py-2 text-sm text-fg focus:outline-hidden focus:ring-1 focus:ring-white/50';
const apiError = (err: unknown, fallback: string) => {
    const d = (err as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
    return typeof d === 'string' ? d : fallback;
};

/** Note type + text; @mentions only in comments (other types are not notifications). */
function NoteFields({ value, onChange }: { value: { event_type: string; content: string }; onChange: (v: { event_type: string; content: string }) => void }) {
    return (
        <>
            <select value={value.event_type} aria-label="Note type"
                onChange={(e) => { const t = e.target.value; onChange({ event_type: t, content: t === 'comment' ? value.content : stripMentions(value.content) }); }}
                className={field}>
                {NOTE_TYPES.map((t) => <option key={t} value={t}>{t}</option>)}
            </select>
            <MarkdownField value={value.content}>
                {value.event_type === 'comment' ? (
                    <MentionTextarea value={value.content} onChange={(content) => onChange({ ...value, content })}
                        placeholder="What's new? (type @ to mention a teammate)" />
                ) : (
                    <textarea value={value.content} onChange={(e) => onChange({ ...value, content: e.target.value })}
                        placeholder="What's new?" rows={3} className={cn(field, 'resize-none')} />
                )}
            </MarkdownField>
        </>
    );
}

export function NoteComposer({ caseId, onClose }: { caseId: number; onClose: () => void }) {
    const qc = useQueryClient();
    const [draft, setDraft] = useState({ event_type: 'comment', content: '' });
    const add = useMutation({
        mutationFn: async () => api.post(`/cases/${caseId}/timeline`, draft),
        onSuccess: () => { qc.invalidateQueries({ queryKey: ['case', String(caseId)] }); onClose(); },
    });
    return (
        <div className="glass-panel p-4 rounded-xl border border-line bg-panel space-y-3">
            <div className="flex items-center justify-between">
                <h4 className="text-sm font-semibold text-fg">New note <span className="font-normal text-muted">· stamped now</span></h4>
                <button onClick={onClose} className="text-muted hover:text-fg" aria-label="Close"><X size={16} /></button>
            </div>
            <NoteFields value={draft} onChange={setDraft} />
            {add.isError && <p role="alert" className="text-xs text-severity-critical">{apiError(add.error, 'Could not add the note.')}</p>}
            <div className="flex justify-end gap-2">
                <button onClick={onClose} className="px-3 py-1.5 text-xs text-fg-3 hover:text-fg">Cancel</button>
                <button onClick={() => add.mutate()} disabled={!draft.content.trim() || add.isPending}
                    className="px-3 py-1.5 text-xs bg-accent text-on-accent rounded-sm font-medium hover:bg-accent-hover disabled:opacity-50">
                    {add.isPending ? 'Adding…' : 'Add note'}
                </button>
            </div>
        </div>
    );
}

/** A note in the activity feed: author, guest/MCP badges, guest sharing, pin to glance, edit/delete. */
export function NoteEntry({ caseId, note, me }: { caseId: number; note: TimelineEvent; me?: User }) {
    const qc = useQueryClient();
    const [editing, setEditing] = useState<{ event_type: string; content: string } | null>(null);
    const isAdmin = !!me && (me.role === 'admin' || !!me.is_super_admin);
    const canWrite = isAdmin || me?.role === 'analyst';
    // Mirrors the backend rule for editing/deleting entries: admin, or the analyst who wrote it.
    const canModify = isAdmin || (!!me && me.role === 'analyst' && note.user_id === me.id);
    const refresh = () => qc.invalidateQueries({ queryKey: ['case', String(caseId)] });
    const base = `/cases/${caseId}/timeline/${note.id}`;

    const save = useMutation({ mutationFn: async () => api.put(base, editing), onSuccess: () => { setEditing(null); refresh(); } });
    const remove = useMutation({ mutationFn: async () => api.delete(base), onSuccess: refresh });
    const share = useMutation({ mutationFn: async () => api.put(`${base}/share`, { shared: !note.shared_with_guests }), onSuccess: refresh });
    const pin = useMutation({ mutationFn: async () => api.patch(`${base}/pin`, { pinned: !note.pinned }), onSuccess: refresh });
    const error = [save, remove, share, pin].find((m) => m.isError)?.error;

    return (
        <div className="rounded-lg border border-line bg-panel p-3">
            <div className="flex flex-wrap items-center gap-2">
                <span className="text-xs font-mono text-muted">{formatDateTime(note.created_at, me?.timezone)}</span>
                <span className="text-[10px] font-mono text-muted bg-raised px-1.5 py-0.5 rounded-sm">{note.event_type.replace(/_/g, ' ')}</span>
                {note.user && (
                    <span className="inline-flex items-center gap-1.5 text-[10px] font-mono text-muted">
                        <Avatar userId={note.user.id} name={note.user.full_name || note.user.email}
                            hasAvatar={note.user.has_avatar} version={note.user.avatar_version} size={18} />
                        {note.user.full_name || note.user.email}
                    </span>
                )}
                {note.guest_email && (
                    <span className="px-1.5 py-0.5 rounded text-[10px] font-medium bg-amber-500/15 text-amber-300"
                        title="Written by an external guest — treat attachments as untrusted">Guest · {note.guest_email}</span>
                )}
                {note.via === 'mcp' && (
                    <span className="text-[10px] font-mono text-muted bg-raised px-1.5 py-0.5 rounded-sm"
                        title="Written by an MCP client acting for this user">via MCP{note.via_client ? ` · ${note.via_client}` : ''}</span>
                )}
                {note.shared_with_guests && <span className="text-[10px] font-medium text-accent">Shared</span>}
                {note.pinned && <Pin size={12} className="text-accent" aria-label="Pinned to glance" />}
                {canWrite && !editing && (
                    <span className="ml-auto flex items-center gap-2 opacity-0 group-hover:opacity-100 focus-within:opacity-100 transition-opacity">
                        <button onClick={() => pin.mutate()} aria-pressed={!!note.pinned} title={note.pinned ? 'Unpin from glance' : 'Pin to glance'}
                            className={cn('hover:text-fg', note.pinned ? 'text-accent' : 'text-muted')}>
                            {note.pinned ? <Pin size={13} /> : <PinOff size={13} />}
                        </button>
                        <button onClick={() => share.mutate()} aria-pressed={!!note.shared_with_guests}
                            title={note.shared_with_guests ? 'Shared with guests — click to stop sharing' : 'Share with guests'}
                            className={note.shared_with_guests ? 'text-accent' : 'text-muted hover:text-fg'}>
                            {note.shared_with_guests ? <Eye size={13} /> : <EyeOff size={13} />}
                        </button>
                        {canModify && <button onClick={() => setEditing({ event_type: note.event_type, content: note.content })} title="Edit" className="text-muted hover:text-fg"><Pencil size={13} /></button>}
                        {canModify && <button onClick={() => { if (confirm('Delete this note?')) remove.mutate(); }} title="Delete" className="text-muted hover:text-severity-critical"><Trash2 size={13} /></button>}
                    </span>
                )}
            </div>
            {editing ? (
                <div className="mt-2 space-y-2">
                    <NoteFields value={editing} onChange={setEditing} />
                    <div className="flex justify-end gap-2">
                        <button onClick={() => setEditing(null)} className="px-2 py-1 text-xs text-fg-3 hover:text-fg">Cancel</button>
                        <button onClick={() => save.mutate()} disabled={!editing.content.trim() || save.isPending}
                            className="px-2 py-1 text-xs bg-accent text-on-accent rounded-sm font-medium hover:bg-accent-hover disabled:opacity-50 flex items-center gap-1">
                            <Check size={12} /> Save
                        </button>
                    </div>
                </div>
            ) : (
                <Markdown content={note.content} className="mt-1 text-sm text-fg-2" />
            )}
            {error && <p role="alert" className="mt-1 text-xs text-severity-critical">{apiError(error, 'Could not save the change.')}</p>}
        </div>
    );
}
