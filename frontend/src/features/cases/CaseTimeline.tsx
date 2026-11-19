import { useEffect, useMemo, useRef, useState } from 'react';
import { useLocation } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Plus, Flag, FileText, FileX, Pencil, Trash2, Download, X, Sparkles, Pin, PinOff, MessageSquare, Clock } from 'lucide-react';
import { api } from '../../api/client';
import { cn } from '../../lib/utils';
import { Button, Badge, Skeleton, fieldClass } from '../../components/ui';
import { formatDateTime } from '../../utils/datetime';
import type { CaseEvent, MilestoneKey, TimelineEvent, User } from '../../types';
import { MILESTONES, apiDetail, useSaveMilestones } from './milestones';
import TimelineSuggest from './TimelineSuggest';
import { NoteComposer, NoteEntry } from './CaseNotes';
import { isNote } from './notes';
import { Markdown, MarkdownField } from '../../components/Markdown';
import { csvCell } from '../../lib/csv';

// Categories: 3 generic + 12 ATT&CK tactics. Colour is deterministic per category.
const EVENT_CATEGORIES: [string, string, string][] = [
    ['unspecified', 'Unspecified', '#71717a'],
    ['legitimate', 'Legitimate', '#22c55e'],
    ['remediation', 'Remediation', '#14b8a6'],
    ['initial_access', 'Initial Access', '#f97316'],
    ['execution', 'Execution', '#ef4444'],
    ['persistence', 'Persistence', '#a855f7'],
    ['privilege_escalation', 'Privilege Escalation', '#e11d48'],
    ['defense_evasion', 'Defense Evasion', '#8b5cf6'],
    ['credential_access', 'Credential Access', '#f59e0b'],
    ['discovery', 'Discovery', '#0ea5e9'],
    ['lateral_movement', 'Lateral Movement', '#3b82f6'],
    ['collection', 'Collection', '#06b6d4'],
    ['command_and_control', 'Command & Control', '#d946ef'],
    ['exfiltration', 'Exfiltration', '#ec4899'],
    ['impact', 'Impact', '#dc2626'],
];
// Shown under the category picker; the attacker stages are MITRE ATT&CK tactics.
const CAT_HINT: Record<string, string> = {
    unspecified: 'Not classified yet. You can reclassify later.',
    legitimate: 'Checked and benign or authorised, e.g. an admin login or a scheduled job. Kept to show it was ruled out.',
    remediation: 'An action your team took: isolate a host, reset a password, block an IP.',
    initial_access: 'The attacker gets in: phishing click, exploited VPN, stolen credentials used.',
    execution: 'The attacker runs code: malicious macro, PowerShell launched.',
    persistence: 'The attacker stays in across reboots or logouts: scheduled task, new service, run key.',
    privilege_escalation: 'The attacker gains higher rights: local admin, token theft.',
    defense_evasion: 'The attacker avoids detection: AV disabled, logs cleared.',
    credential_access: 'The attacker steals credentials: LSASS dump, brute force, keylogging.',
    discovery: 'The attacker learns the environment: AD enumeration, network scans.',
    lateral_movement: 'The attacker moves to other machines: PsExec, RDP to a server.',
    collection: 'The attacker gathers data to steal: files staged or archived.',
    command_and_control: 'The attacker talks to their infrastructure: beaconing to a C2 domain.',
    exfiltration: 'The attacker takes data out: upload to cloud storage.',
    impact: 'The attacker does damage: ransomware encryption, data wiped.',
};
// Strips markdown marks; underscores inside words stay, so "in_progress" doesn't become "inprogress".
const plain = (s: string) => s.replace(/@\[([^\]]+)\]\(user:\d+\)/g, '@$1').replace(/[#*`>~[\]]|(?<!\w)_|_(?!\w)/g, '').trim();
const ACTIVITY_DEFAULT_LIMIT = 200; // above this many activity entries the feed opens on the story only
type Source = 'all' | 'investigation' | 'activity';
const SOURCES: [Source, string, string][] = [
    ['all', 'All', 'Everything, in time order'],
    ['investigation', 'Story', 'Investigation events: what happened during the incident'],
    ['activity', 'Notes & changes', 'Notes, comments and what changed on the case'],
];
type Row = { kind: 'inv'; at: number; e: CaseEvent } | { kind: 'act'; at: number; a: TimelineEvent };

const CAT = Object.fromEntries(EVENT_CATEGORIES.map(([k, label, color]) => [k, { label, color }]));

type Draft = {
    event_date: string; title: string; content: string; raw: string; source: string; category: string;
    tags: string; flagged: boolean; in_summary: boolean; artifact_ids: number[];
};
const toLocalInput = (iso?: string) => {
    const d = iso ? new Date(iso) : new Date();
    const p = (n: number) => String(n).padStart(2, '0');
    return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`;
};
const blank = (): Draft => ({ event_date: toLocalInput(), title: '', content: '', raw: '', source: '', category: 'unspecified', tags: '', flagged: false, in_summary: true, artifact_ids: [] });
const fromEvent = (e: CaseEvent): Draft => ({
    event_date: toLocalInput(e.event_date), title: e.title, content: e.content ?? '', raw: e.raw ?? '', source: e.source ?? '',
    category: e.category, tags: e.tags.join(', '), flagged: e.flagged, in_summary: e.in_summary, artifact_ids: e.artifacts.map((a) => a.id),
});
const toPayload = (d: Draft) => ({
    ...d, event_date: new Date(d.event_date).toISOString(),
    tags: d.tags.split(',').map((t) => t.trim()).filter(Boolean),
    content: d.content || null, raw: d.raw || null, source: d.source || null,
});

// Query syntax: free words match everything; `key:value` tokens narrow (category, tag, source, artifact, flag).
function matches(e: CaseEvent, q: string): boolean {
    return q.toLowerCase().split(/\s+/).filter(Boolean).every((tok) => {
        const [k, ...rest] = tok.split(':');
        const v = rest.join(':');
        if (v) {
            if (k === 'category') return e.category.includes(v);
            if (k === 'tag') return e.tags.some((t) => t.toLowerCase().includes(v));
            if (k === 'source') return (e.source ?? '').toLowerCase().includes(v);
            if (k === 'artifact' || k === 'asset' || k === 'ioc') return e.artifacts.some((a) => a.value.toLowerCase().includes(v));
            if (k === 'flag') return e.flagged === (v === 'true' || v === '1');
        }
        const hay = [e.title, e.content, e.raw, e.source, e.category, ...e.tags, ...e.artifacts.map((a) => a.value)].join(' ').toLowerCase();
        return hay.includes(tok);
    });
}

function exportCsv(events: CaseEvent[], caseId: number) {
    const head = ['event_date', 'title', 'category', 'source', 'tags', 'artifacts', 'flagged', 'in_summary', 'content', 'raw'];
    const rows = events.map((e) => [e.event_date, e.title, e.category, e.source, e.tags.join('|'), e.artifacts.map((a) => a.value).join('|'), e.flagged, e.in_summary, e.content, e.raw]);
    const blob = new Blob([[head, ...rows].map((r) => r.map(csvCell).join(',')).join('\n')], { type: 'text/csv' });
    const a = Object.assign(document.createElement('a'), { href: URL.createObjectURL(blob), download: `case-${caseId}-timeline.csv` });
    a.click();
    URL.revokeObjectURL(a.href);
}

const isConflict = (err: unknown) => {
    const r = (err as { response?: { status?: number; data?: { detail?: unknown } } })?.response;
    return r?.status === 409 && r.data?.detail === 'version_conflict';
};
const CONFLICT_COPY = 'This event changed while you were editing — copy your changes, then reload.';

const field = fieldClass();

/** The case's one feed: investigation events (the story, dated when they happened) interleaved with notes and system changes. */
export default function CaseTimeline({ caseId, activity = [] }: { caseId: number; activity?: TimelineEvent[] }) {
    const qc = useQueryClient();
    const { hash } = useLocation();
    const [query, setQuery] = useState('');
    const [flaggedOnly, setFlaggedOnly] = useState(false);
    // A deep link to a note (#event-<id>) needs the notes shown.
    const [source, setSource] = useState<Source>(() => (activity.length > ACTIVITY_DEFAULT_LIMIT && !hash.startsWith('#event-') ? 'investigation' : 'all'));
    const [noting, setNoting] = useState(false);
    const setMilestone = useSaveMilestones(caseId);
    const [editing, setEditing] = useState<number | 'new' | null>(null);
    const [draft, setDraft] = useState<Draft>(blank);
    const [suggesting, setSuggesting] = useState(false);
    const [confirmDelete, setConfirmDelete] = useState<number | null>(null);
    const [baseVersion, setBaseVersion] = useState<string | null>(null); // updated_at of the event being edited, as loaded

    const { data: me } = useQuery({ queryKey: ['currentUser'], queryFn: async () => (await api.get('/users/me')).data as User, staleTime: 300_000 });
    const canEdit = me?.role === 'admin' || me?.role === 'analyst' || me?.is_super_admin;
    const key = ['case-events', caseId];
    const { data: events = [], isLoading } = useQuery({ queryKey: key, queryFn: async () => (await api.get(`/cases/${caseId}/events`)).data as CaseEvent[] });
    const { data: artifacts = [] } = useQuery({
        queryKey: ['artifacts', String(caseId)],
        queryFn: async () => (await api.get(`/artifacts/case/${caseId}`)).data as { id: number; value: string; artifact_type: string }[],
    });

    const invalidate = () => { qc.invalidateQueries({ queryKey: key }); };
    const save = useMutation({
        mutationFn: async () => editing === 'new'
            ? api.post(`/cases/${caseId}/events`, toPayload(draft))
            : api.put(`/cases/${caseId}/events/${editing}`, { ...toPayload(draft), base_updated_at: baseVersion }),
        onSuccess: () => { setEditing(null); invalidate(); },
    });
    const patch = useMutation({
        mutationFn: async ({ id, ...body }: { id: number; updated_at: string | null; flagged?: boolean; in_summary?: boolean }) => {
            const { updated_at, ...rest } = body;
            return api.put(`/cases/${caseId}/events/${id}`, { ...rest, base_updated_at: updated_at });
        },
        onSuccess: invalidate,
    });
    const remove = useMutation({ mutationFn: async (id: number) => api.delete(`/cases/${caseId}/events/${id}`), onSuccess: invalidate });
    const pin = useMutation({
        mutationFn: async ({ id, pinned }: { id: number; pinned: boolean }) => api.patch(`/cases/${caseId}/timeline/${id}/pin`, { pinned }),
        onSuccess: () => qc.invalidateQueries({ queryKey: ['case'] }),
    });

    const shown = useMemo(() => events.filter((e) => (!flaggedOnly || e.flagged) && matches(e, query)), [events, flaggedOnly, query]);
    // Activity entries interleave by their own time; "Pinned" means pinned for them, and the text filter is a plain substring.
    const shownActivity = useMemo(() => activity.filter((a) => (!flaggedOnly || a.pinned)
        && (!query.trim() || plain(a.content).toLowerCase().includes(query.trim().toLowerCase()))), [activity, flaggedOnly, query]);
    const days = useMemo(() => {
        const rows: Row[] = [
            ...(source !== 'activity' ? shown.map((e) => ({ kind: 'inv' as const, at: +new Date(e.event_date), e })) : []),
            ...(source !== 'investigation' ? shownActivity.map((a) => ({ kind: 'act' as const, at: +new Date(a.created_at), a })) : []),
        ].sort((x, y) => x.at - y.at);
        const fmt = new Intl.DateTimeFormat(undefined, { dateStyle: 'full', timeZone: me?.timezone || undefined });
        const out: [string, Row[]][] = [];
        for (const r of rows) {
            const day = fmt.format(new Date(r.at));
            if (out.length && out[out.length - 1][0] === day) out[out.length - 1][1].push(r); else out.push([day, [r]]);
        }
        return out;
    }, [shown, shownActivity, source, me?.timezone]);

    // Deep link from a notification: #event-<id> -> scroll to and briefly highlight the note, once per hash.
    const handledHash = useRef<string | null>(null);
    useEffect(() => {
        if (isLoading || !/^#event-\d+$/.test(hash) || handledHash.current === hash) return;
        const el = document.getElementById(hash.slice(1));
        if (!el) return;
        handledHash.current = hash;
        el.scrollIntoView({ block: 'center' });
        el.classList.add('ring-2', 'ring-accent');
        setTimeout(() => el.classList.remove('ring-2', 'ring-accent'), 2000);
    }, [isLoading, hash, days]);

    if (isLoading) return <Skeleton rows={4} />;

    const form = (
        <div className="p-4 rounded-md border border-line bg-panel space-y-3">
            <div className="flex items-center justify-between">
                <h3 className="text-sm font-semibold text-fg">{editing === 'new' ? 'New event' : 'Edit event'}</h3>
                <Button variant="ghost" size="icon" className="h-7 w-7" onClick={() => setEditing(null)} aria-label="Close"><X size={16} /></Button>
            </div>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                <input type="datetime-local" value={draft.event_date} onChange={(e) => setDraft({ ...draft, event_date: e.target.value })} className={field} aria-label="Event date" />
                <div>
                    <select value={draft.category} onChange={(e) => setDraft({ ...draft, category: e.target.value })} className={field} aria-label="Category" aria-describedby="event-category-hint">
                        {EVENT_CATEGORIES.map(([k, label]) => <option key={k} value={k}>{label}</option>)}
                    </select>
                    <p id="event-category-hint" className="mt-1 text-xs text-fg-3">{CAT_HINT[draft.category]}</p>
                </div>
                <input value={draft.title} onChange={(e) => setDraft({ ...draft, title: e.target.value })} placeholder="Title" className={cn(field, 'sm:col-span-2')} aria-label="Title" />
                <input value={draft.source} onChange={(e) => setDraft({ ...draft, source: e.target.value })} placeholder="Source (EDR, Sysmon, proxy log…)" className={field} aria-label="Source" />
                <input value={draft.tags} onChange={(e) => setDraft({ ...draft, tags: e.target.value })} placeholder="Tags, comma separated" className={field} aria-label="Tags" />
                <div className="sm:col-span-2"><MarkdownField value={draft.content} imageCaseId={caseId} replaceInValue={(f, t) => draft.content.includes(f) && (setDraft({ ...draft, content: draft.content.replace(f, () => t) }), true)}><textarea value={draft.content} onChange={(e) => setDraft({ ...draft, content: e.target.value })} placeholder="What happened (markdown)" rows={3} className={cn(field, 'h-auto py-2 resize-none')} aria-label="Content" /></MarkdownField></div>
                <textarea value={draft.raw} onChange={(e) => setDraft({ ...draft, raw: e.target.value })} placeholder="Raw log line / evidence excerpt" rows={2} className={cn(field, 'h-auto py-2 sm:col-span-2 resize-none font-mono text-xs')} aria-label="Raw" />
            </div>
            {artifacts.length > 0 && (
                <div className="flex flex-wrap gap-1.5">
                    {artifacts.map((a) => {
                        const on = draft.artifact_ids.includes(a.id);
                        return (
                            <button key={a.id} type="button" aria-pressed={on}
                                onClick={() => setDraft({ ...draft, artifact_ids: on ? draft.artifact_ids.filter((x) => x !== a.id) : [...draft.artifact_ids, a.id] })}
                                className={cn('px-1.5 py-0.5 rounded-sm border text-xs font-mono transition-colors', on ? 'bg-accent/10 text-accent border-accent/30' : 'bg-raised text-fg-3 border-line hover:text-fg')}>
                                {a.value}
                            </button>
                        );
                    })}
                </div>
            )}
            <div className="flex items-center gap-4 text-xs text-fg-2">
                <label className="flex items-center gap-1.5"><input type="checkbox" checked={draft.flagged} onChange={(e) => setDraft({ ...draft, flagged: e.target.checked })} /> Pin to glance</label>
                <label className="flex items-center gap-1.5"><input type="checkbox" checked={draft.in_summary} onChange={(e) => setDraft({ ...draft, in_summary: e.target.checked })} /> Include in report</label>
                <div className="ml-auto flex gap-2">
                    <Button size="sm" variant="secondary" onClick={() => setEditing(null)}>Cancel</Button>
                    <Button size="sm" onClick={() => save.mutate()} disabled={!draft.title.trim() || !draft.event_date || save.isPending}>
                        {save.isPending ? 'Saving…' : 'Save'}
                    </Button>
                </div>
            </div>
            {save.isError && <p role="alert" className="text-xs text-severity-critical">{isConflict(save.error) ? CONFLICT_COPY : 'Could not save the event.'}</p>}
        </div>
    );

    return (
        <div className="space-y-4 my-2">
            <div className="flex flex-wrap items-center gap-2">
                <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Filter, e.g. category:lateral"
                    title="Words match title and content. Also: category:… tag:… artifact:… flag:true"
                    className={cn(fieldClass(false, 'sm'), 'flex-1 min-w-40')} aria-label="Filter events (category:, tag:, artifact:, flag:true)" />
                <div role="group" aria-label="Show" className="flex rounded-md overflow-hidden border border-line-strong">
                    {SOURCES.map(([k, label, hint]) => (
                        <button key={k} onClick={() => setSource(k)} aria-pressed={source === k} title={hint}
                            className={cn('h-8 text-xs px-2.5 transition-colors', source === k ? 'bg-accent/10 text-accent font-medium' : 'bg-panel text-fg-2 hover:bg-raised')}>
                            {label}
                        </button>
                    ))}
                </div>
                <Button size="sm" variant="secondary" onClick={() => setFlaggedOnly((v) => !v)} aria-pressed={flaggedOnly} title='Only entries pinned to the glance'
                    className={cn(flaggedOnly && 'bg-accent/10 text-accent border-accent/30')}>
                    <Flag size={12} aria-hidden /> Pinned
                </Button>
                {canEdit && (
                    <Button size="sm" variant="secondary" onClick={() => setSuggesting((v) => !v)} aria-pressed={suggesting}
                        className={cn(suggesting && 'bg-accent/10 text-accent border-accent/30')}>
                        <Sparkles size={12} aria-hidden /> Suggest
                    </Button>
                )}
                {canEdit && (
                    // One "Add" control, two kinds: a note is stamped now, an event is dated when it happened.
                    <div role="group" aria-label="Add to activity" className="flex items-center gap-1.5">
                        <span className="flex items-center gap-1 text-xs text-fg-3" aria-hidden><Plus size={12} /> Add</span>
                        <div className="flex items-stretch h-8 rounded-md overflow-hidden bg-accent text-on-accent text-xs font-medium">
                        <button onClick={() => { setNoting(true); setEditing(null); }} title="A comment or update, stamped now. @mention teammates." aria-label="Add note"
                            className="flex items-center gap-1 px-2.5 hover:bg-accent-hover focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-on-accent">
                            <MessageSquare size={12} aria-hidden /> Note
                        </button>
                        <button onClick={() => { setDraft(blank()); setEditing('new'); setNoting(false); }} title="Something that happened in the incident, dated when it happened. Builds the story, the glance and the report." aria-label="Add event"
                            className="flex items-center gap-1 px-2.5 border-l border-on-accent/20 hover:bg-accent-hover focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-on-accent">
                            <Clock size={12} aria-hidden /> Event
                        </button>
                        </div>
                    </div>
                )}
            </div>
            {(query.trim() || flaggedOnly || source !== 'all') && (
                <p className="text-xs num text-fg-3">
                    {source !== 'activity' && `${shown.length} of ${events.length} events`}
                    {source === 'all' && ' · '}
                    {source !== 'investigation' && `${shownActivity.length} of ${activity.length} activity entries`}
                </p>
            )}
            {patch.isError && <p role="alert" className="text-xs text-severity-critical">{isConflict(patch.error) ? CONFLICT_COPY : 'Could not update the event.'}</p>}
            {setMilestone.isError && <p role="alert" className="text-xs text-severity-critical">{apiDetail(setMilestone.error)}</p>}
            {suggesting && <TimelineSuggest caseId={caseId} categories={EVENT_CATEGORIES} timezone={me?.timezone} onClose={() => setSuggesting(false)} />}
            {noting && <NoteComposer caseId={caseId} onClose={() => setNoting(false)} />}
            {editing === 'new' && form}

            {!events.length && !activity.length && editing !== 'new' && !noting && !suggesting && (
                <div className="text-center py-12 text-fg-3 text-sm">Nothing recorded yet. Add a note for quick updates, or an event for what happened and when. Suggest pulls events from alerts, notes and pasted logs.</div>
            )}

            <div className="relative space-y-6">
                <div className="absolute left-[7px] top-0 bottom-0 w-px bg-line" />
                {days.map(([day, list]) => (
                    <section key={day} className="space-y-3">
                        <h3 className="relative pl-6 label-caps">
                            <span className="absolute left-0 top-1 w-[15px] h-[15px] rounded-full bg-panel border-2 border-line" />
                            {day}
                        </h3>
                        {list.map((r) => {
                            if (r.kind === 'act' && isNote(r.a.event_type)) {
                                return (
                                    <article key={`a${r.a.id}`} id={`event-${r.a.id}`} className="relative pl-6 group scroll-mt-24 rounded-md">
                                        <span className="absolute left-[5px] top-3 w-[5px] h-[5px] rounded-full bg-fg-3" />
                                        <NoteEntry caseId={caseId} note={r.a} me={me} />
                                    </article>
                                );
                            }
                            if (r.kind === 'act') {
                                const a = r.a;
                                return (
                                    <article key={`a${a.id}`} id={`event-${a.id}`} className="relative pl-6 group scroll-mt-24">
                                        <span className="absolute left-[5px] top-2.5 w-[5px] h-[5px] rounded-full bg-fg-3" />
                                        <div className="rounded-md border border-line bg-surface px-3 py-1.5 flex items-center gap-2 text-xs text-fg-3">
                                            <span className="num shrink-0">{formatDateTime(a.created_at, me?.timezone)}</span>
                                            <span className="label-caps shrink-0">{a.event_type.replace(/_/g, ' ')}</span>
                                            <span className="truncate">{plain(a.content)}</span>
                                            {canEdit ? (
                                                <button onClick={() => pin.mutate({ id: a.id, pinned: !a.pinned })} aria-pressed={!!a.pinned}
                                                    title={a.pinned ? 'Unpin from glance' : 'Pin to glance'}
                                                    aria-label={a.pinned ? 'Unpin from glance' : 'Pin to glance'}
                                                    className={cn('ml-auto shrink-0 hover:text-fg', a.pinned ? 'text-accent' : 'text-fg-3 opacity-0 group-hover:opacity-100 focus:opacity-100 [@media(hover:none)]:opacity-100')}>
                                                    {a.pinned ? <Pin size={13} /> : <PinOff size={13} />}
                                                </button>
                                            ) : a.pinned && <Pin size={12} className="ml-auto text-accent" aria-label="Pinned" />}
                                        </div>
                                    </article>
                                );
                            }
                            const e = r.e;
                            const c = CAT[e.category] ?? CAT.unspecified;
                            if (editing === e.id) return <div key={e.id} className="pl-6">{form}</div>;
                            return (
                                <article key={e.id} id={`case-event-${e.id}`} className="relative pl-6 group">
                                    <span className="absolute left-[4px] top-3 w-[7px] h-[7px] rounded-full" style={{ background: c.color }} />
                                    <div className="rounded-md border border-line bg-panel p-3">
                                        <div className="flex flex-wrap items-center gap-2">
                                            <span className="text-xs num text-fg-3">{formatDateTime(e.event_date, me?.timezone)}</span>
                                            <span className="inline-flex items-center gap-1.5 text-xs font-medium text-fg-2"><span className="size-2 rounded-sm" style={{ background: c.color }} aria-hidden />{c.label}</span>
                                            {e.source && <Badge>{e.source}</Badge>}
                                            {e.flagged && <Flag size={12} className="text-severity-high" aria-label="Pinned to glance" />}
                                            {canEdit && (
                                                <span className="ml-auto flex items-center gap-2 opacity-0 group-hover:opacity-100 group-focus-within:opacity-100 [@media(hover:none)]:opacity-100 transition-opacity">
                                                    <button onClick={() => patch.mutate({ id: e.id, updated_at: e.updated_at, flagged: !e.flagged })} title={e.flagged ? 'Unpin from glance' : 'Pin to glance'} aria-label={e.flagged ? 'Unpin from glance' : 'Pin to glance'} aria-pressed={!!e.flagged} className={cn('hover:text-fg', e.flagged ? 'text-severity-high' : 'text-fg-3')}><Flag size={13} /></button>
                                                    <button onClick={() => patch.mutate({ id: e.id, updated_at: e.updated_at, in_summary: !e.in_summary })} title={e.in_summary ? 'In report — click to exclude' : 'Excluded from report — click to include'} aria-label="Include in report" aria-pressed={!!e.in_summary} className={cn('hover:text-fg', e.in_summary ? 'text-accent' : 'text-fg-3')}>
                                                        {e.in_summary ? <FileText size={13} /> : <FileX size={13} />}
                                                    </button>
                                                    <select value="" aria-label="Set as milestone" title="Use this event's time as a case milestone"
                                                        onChange={(ev) => { const k = ev.target.value as MilestoneKey; if (k) setMilestone.mutate({ [`${k}_at`]: e.event_date }); }}
                                                        className="text-xs bg-panel text-fg-2 rounded-sm px-1 py-0.5 border border-line-strong">
                                                        <option value="">Set as…</option>
                                                        {MILESTONES.map(({ key, label }) => <option key={key} value={key}>{label}</option>)}
                                                    </select>
                                                    <button onClick={() => { setDraft(fromEvent(e)); setBaseVersion(e.updated_at ?? null); setEditing(e.id); }} title="Edit" aria-label="Edit event" className="text-fg-3 hover:text-fg"><Pencil size={13} /></button>
                                                    {confirmDelete === e.id ? (
                                                        <span className="flex items-center gap-1.5 text-xs">
                                                            <button onClick={() => remove.mutate(e.id, { onSuccess: () => setConfirmDelete(null) })} disabled={remove.isPending} className="text-severity-critical hover:underline">Delete</button>
                                                            <button onClick={() => setConfirmDelete(null)} className="text-fg-3 hover:underline">Cancel</button>
                                                        </span>
                                                    ) : (
                                                        <button onClick={() => setConfirmDelete(e.id)} title="Delete" aria-label="Delete event" className="text-fg-3 hover:text-severity-critical"><Trash2 size={13} /></button>
                                                    )}
                                                </span>
                                            )}
                                        </div>
                                        <p className="mt-1 text-sm font-medium text-fg">{e.title}</p>
                                        {e.content && <Markdown content={e.content} className="mt-1 text-sm text-fg-2" />}
                                        {e.raw && <pre className="mt-2 text-xs font-mono text-fg-3 bg-raised rounded-sm p-2 overflow-x-auto whitespace-pre-wrap break-all">{e.raw}</pre>}
                                        {(e.tags.length > 0 || e.artifacts.length > 0) && (
                                            <div className="mt-2 flex flex-wrap gap-1.5">
                                                {e.artifacts.map((a) => <Badge key={a.id} variant="brand" className="font-mono">{a.value}</Badge>)}
                                                {e.tags.map((t) => <Badge key={t}>#{t}</Badge>)}
                                            </div>
                                        )}
                                    </div>
                                </article>
                            );
                        })}
                    </section>
                ))}
            </div>
            {shown.length > 0 && (
                <button onClick={() => exportCsv(shown, caseId)}
                    className="text-xs text-fg-3 hover:text-fg-2 flex items-center gap-1.5 rounded-sm">
                    <Download size={12} aria-hidden /> Export events as CSV
                </button>
            )}
        </div>
    );
}
