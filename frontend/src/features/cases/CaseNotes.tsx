import { useRef, useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { Check, Eye, EyeOff, Pencil, Pin, PinOff, Trash2, X } from 'lucide-react';
import { api } from '../../api/client';
import { cn } from '../../lib/utils';
import { Button, Badge, fieldClass, useConfirm } from '../../components/ui';
import Avatar from '../../components/Avatar';
import { Markdown, MarkdownField } from '../../components/Markdown';
import { formatDateTime } from '../../utils/datetime';
import MentionTextarea from './MentionTextarea';
import { stripMentions } from './mentionChips';
import type { TimelineEvent, User } from '../../types';
import { NOTE_TYPES } from './notes';

const field = fieldClass();
const isConflict = (err: unknown) => {
    const r = (err as { response?: { status?: number; data?: { detail?: unknown } } })?.response;
    return r?.status === 409 && r.data?.detail === 'version_conflict';
};
const apiError = (err: unknown, fallback: string) => {
    const d = (err as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
    return typeof d === 'string' ? d : fallback;
};

/** Note type + text; @mentions only in comments (other types are not notifications). */
function NoteFields({ caseId, value, onChange }: { caseId: number; value: { event_type: string; content: string }; onChange: (v: { event_type: string; content: string }) => void }) {
    return (
        <>
            <select value={value.event_type} aria-label="Note type"
                onChange={(e) => { const t = e.target.value; onChange({ event_type: t, content: t === 'comment' ? value.content : stripMentions(value.content) }); }}
                className={field}>
                {NOTE_TYPES.map((t) => <option key={t} value={t}>{t}</option>)}
            </select>
            <MarkdownField value={value.content} imageCaseId={caseId}
                replaceInValue={(f, t) => value.content.includes(f) && (onChange({ ...value, content: value.content.replace(f, () => t) }), true)}>
                {value.event_type === 'comment' ? (
                    <MentionTextarea value={value.content} onChange={(content) => onChange({ ...value, content })}
                        placeholder="What's new? (type @ to mention a teammate)" />
                ) : (
                    <textarea value={value.content} onChange={(e) => onChange({ ...value, content: e.target.value })}
                        placeholder="What's new?" rows={3} className={cn(field, 'h-auto py-2 resize-none')} />
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
        <div className="p-4 rounded-md border border-line bg-panel space-y-3">
            <div className="flex items-center justify-between">
                <h3 className="text-sm font-semibold text-fg">New note <span className="font-normal text-fg-3">· stamped now</span></h3>
                <Button variant="ghost" size="icon" className="h-7 w-7" onClick={onClose} aria-label="Close"><X size={16} /></Button>
            </div>
            <NoteFields caseId={caseId} value={draft} onChange={setDraft} />
            {add.isError && <p role="alert" className="text-xs text-severity-critical">{apiError(add.error, 'Could not add the note.')}</p>}
            <div className="flex justify-end gap-2">
                <Button variant="secondary" onClick={onClose}>Cancel</Button>
                <Button onClick={() => add.mutate()} disabled={!draft.content.trim() || add.isPending}>
                    {add.isPending ? 'Adding…' : 'Add note'}
                </Button>
            </div>
        </div>
    );
}

/** A note in the activity feed: author, guest/MCP badges, guest sharing, pin to glance, edit/delete. */
export function NoteEntry({ caseId, note, me }: { caseId: number; note: TimelineEvent; me?: User }) {
    const qc = useQueryClient();
    const [editing, setEditing] = useState<{ event_type: string; content: string } | null>(null);
    const { confirm, dialog } = useConfirm();
    const baseVersion = useRef<string | null>(null); // updated_at as loaded when editing started; refetches must not advance it
    const isAdmin = !!me && (me.role === 'admin' || !!me.is_super_admin);
    const canWrite = isAdmin || me?.role === 'analyst';
    // Mirrors the backend rule for editing/deleting entries: admin, or the analyst who wrote it.
    const canModify = isAdmin || (!!me && me.role === 'analyst' && note.user_id === me.id);
    const refresh = () => qc.invalidateQueries({ queryKey: ['case', String(caseId)] });
    const base = `/cases/${caseId}/timeline/${note.id}`;

    const save = useMutation({ mutationFn: async () => api.put(base, { ...editing, base_updated_at: baseVersion.current }), onSuccess: () => { setEditing(null); refresh(); } });
    const remove = useMutation({ mutationFn: async () => api.delete(base), onSuccess: refresh });
    const share = useMutation({ mutationFn: async () => api.put(`${base}/share`, { shared: !note.shared_with_guests }), onSuccess: refresh });
    const pin = useMutation({ mutationFn: async () => api.patch(`${base}/pin`, { pinned: !note.pinned }), onSuccess: refresh });
    const error = [save, remove, share, pin].find((m) => m.isError)?.error;

    return (
        <div className="rounded-md border border-line bg-panel p-3">
            {dialog}
            <div className="flex flex-wrap items-center gap-2">
                <span className="text-xs num text-fg-3">{formatDateTime(note.created_at, me?.timezone)}</span>
                <Badge>{note.event_type.replace(/_/g, ' ')}</Badge>
                {note.user && (
                    <span className="inline-flex items-center gap-1.5 text-xs text-fg-3">
                        <Avatar userId={note.user.id} name={note.user.full_name || note.user.email}
                            hasAvatar={note.user.has_avatar} version={note.user.avatar_version} size={18} />
                        {note.user.full_name || note.user.email}
                    </span>
                )}
                {note.guest_email && (
                    <Badge variant="medium"
                        title="Written by an external guest — treat attachments as untrusted">Guest · {note.guest_email}</Badge>
                )}
                {note.via === 'mcp' && (
                    <Badge
                        title="Written by an MCP client acting for this user">via MCP{note.via_client ? ` · ${note.via_client}` : ''}</Badge>
                )}
                {note.shared_with_guests && <span className="text-xs font-medium text-accent">Shared</span>}
                {note.pinned && <Pin size={12} className="text-accent" aria-label="Pinned to glance" />}
                {canWrite && !editing && (
                    <span className="ml-auto flex items-center gap-2 opacity-0 group-hover:opacity-100 group-focus-within:opacity-100 [@media(hover:none)]:opacity-100 transition-opacity">
                        <button onClick={() => pin.mutate()} aria-pressed={!!note.pinned} title={note.pinned ? 'Unpin from glance' : 'Pin to glance'} aria-label={note.pinned ? 'Unpin from glance' : 'Pin to glance'}
                            className={cn('hover:text-fg', note.pinned ? 'text-accent' : 'text-fg-3')}>
                            {note.pinned ? <Pin size={13} /> : <PinOff size={13} />}
                        </button>
                        <button onClick={() => share.mutate()} aria-pressed={!!note.shared_with_guests}
                            title={note.shared_with_guests ? 'Shared with guests — click to stop sharing' : 'Share with guests'} aria-label="Share with guests"
                            className={note.shared_with_guests ? 'text-accent' : 'text-fg-3 hover:text-fg'}>
                            {note.shared_with_guests ? <Eye size={13} /> : <EyeOff size={13} />}
                        </button>
                        {canModify && <button onClick={() => { baseVersion.current = note.updated_at ?? null; setEditing({ event_type: note.event_type, content: note.content }); }} title="Edit" aria-label="Edit note" className="text-fg-3 hover:text-fg"><Pencil size={13} /></button>}
                        {canModify && <button onClick={async () => { if (await confirm({ title: 'Delete this note?', danger: true })) remove.mutate(); }} title="Delete" aria-label="Delete note" className="text-fg-3 hover:text-severity-critical"><Trash2 size={13} /></button>}
                    </span>
                )}
            </div>
            {editing ? (
                <div className="mt-2 space-y-2">
                    <NoteFields caseId={caseId} value={editing} onChange={setEditing} />
                    <div className="flex justify-end gap-2">
                        <Button size="sm" variant="secondary" onClick={() => setEditing(null)}>Cancel</Button>
                        <Button size="sm" onClick={() => save.mutate()} disabled={!editing.content.trim() || save.isPending}>
                            <Check size={12} aria-hidden /> Save
                        </Button>
                    </div>
                </div>
            ) : (
                <Markdown content={note.content} className="mt-1 text-sm text-fg-2" />
            )}
            {error && <p role="alert" className="mt-1 text-xs text-severity-critical">{isConflict(error)
                ? 'This note changed while you were editing — copy your changes, then reload.'
                : apiError(error, 'Could not save the change.')}</p>}
        </div>
    );
}
