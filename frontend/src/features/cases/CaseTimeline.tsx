import { useMemo, useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Plus, Flag, FileText, FileX, Pencil, Trash2, Download, X, Sparkles } from 'lucide-react';
import { api } from '../../api/client';
import { cn } from '../../lib/utils';
import { formatDateTime } from '../../utils/datetime';
import type { CaseEvent, User } from '../../types';
import TimelineSuggest from './TimelineSuggest';

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

const csvCell = (v: unknown) => `"${String(v ?? '').replace(/"/g, '""')}"`;
function exportCsv(events: CaseEvent[], caseId: number) {
    const head = ['event_date', 'title', 'category', 'source', 'tags', 'artifacts', 'flagged', 'in_summary', 'content', 'raw'];
    const rows = events.map((e) => [e.event_date, e.title, e.category, e.source, e.tags.join('|'), e.artifacts.map((a) => a.value).join('|'), e.flagged, e.in_summary, e.content, e.raw]);
    const blob = new Blob([[head, ...rows].map((r) => r.map(csvCell).join(',')).join('\n')], { type: 'text/csv' });
    const a = Object.assign(document.createElement('a'), { href: URL.createObjectURL(blob), download: `case-${caseId}-timeline.csv` });
    a.click();
    URL.revokeObjectURL(a.href);
}

const field = 'w-full bg-panel border border-line rounded-lg px-3 py-2 text-sm text-fg focus:outline-hidden focus:ring-1 focus:ring-white/50';

export default function CaseTimeline({ caseId }: { caseId: number }) {
    const qc = useQueryClient();
    const [query, setQuery] = useState('');
    const [flaggedOnly, setFlaggedOnly] = useState(false);
    const [editing, setEditing] = useState<number | 'new' | null>(null);
    const [draft, setDraft] = useState<Draft>(blank);
    const [suggesting, setSuggesting] = useState(false);

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
            : api.put(`/cases/${caseId}/events/${editing}`, toPayload(draft)),
        onSuccess: () => { setEditing(null); invalidate(); },
    });
    const patch = useMutation({
        mutationFn: async ({ id, ...body }: { id: number; flagged?: boolean; in_summary?: boolean }) => api.put(`/cases/${caseId}/events/${id}`, body),
        onSuccess: invalidate,
    });
    const remove = useMutation({ mutationFn: async (id: number) => api.delete(`/cases/${caseId}/events/${id}`), onSuccess: invalidate });

    const shown = useMemo(() => events.filter((e) => (!flaggedOnly || e.flagged) && matches(e, query)), [events, flaggedOnly, query]);
    const days = useMemo(() => {
        const fmt = new Intl.DateTimeFormat(undefined, { dateStyle: 'full', timeZone: me?.timezone || undefined });
        const out: [string, CaseEvent[]][] = [];
        for (const e of shown) {
            const day = fmt.format(new Date(e.event_date));
            if (out.length && out[out.length - 1][0] === day) out[out.length - 1][1].push(e); else out.push([day, [e]]);
        }
        return out;
    }, [shown, me?.timezone]);

    if (isLoading) return <p className="font-mono text-xs text-muted">$ loading timeline…</p>;

    const form = (
        <div className="glass-panel p-4 rounded-xl border border-line bg-panel space-y-3">
            <div className="flex items-center justify-between">
                <h4 className="text-sm font-semibold text-fg">{editing === 'new' ? 'New event' : 'Edit event'}</h4>
                <button onClick={() => setEditing(null)} className="text-muted hover:text-fg" aria-label="Close"><X size={16} /></button>
            </div>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                <input type="datetime-local" value={draft.event_date} onChange={(e) => setDraft({ ...draft, event_date: e.target.value })} className={field} aria-label="Event date" />
                <select value={draft.category} onChange={(e) => setDraft({ ...draft, category: e.target.value })} className={field} aria-label="Category">
                    {EVENT_CATEGORIES.map(([k, label]) => <option key={k} value={k}>{label}</option>)}
                </select>
                <input value={draft.title} onChange={(e) => setDraft({ ...draft, title: e.target.value })} placeholder="Title" className={cn(field, 'sm:col-span-2')} aria-label="Title" />
                <input value={draft.source} onChange={(e) => setDraft({ ...draft, source: e.target.value })} placeholder="Source (EDR, Sysmon, proxy log…)" className={field} aria-label="Source" />
                <input value={draft.tags} onChange={(e) => setDraft({ ...draft, tags: e.target.value })} placeholder="Tags, comma separated" className={field} aria-label="Tags" />
                <textarea value={draft.content} onChange={(e) => setDraft({ ...draft, content: e.target.value })} placeholder="What happened (markdown)" rows={3} className={cn(field, 'sm:col-span-2 resize-none')} aria-label="Content" />
                <textarea value={draft.raw} onChange={(e) => setDraft({ ...draft, raw: e.target.value })} placeholder="Raw log line / evidence excerpt" rows={2} className={cn(field, 'sm:col-span-2 resize-none font-mono text-xs')} aria-label="Raw" />
            </div>
            {artifacts.length > 0 && (
                <div className="flex flex-wrap gap-1.5">
                    {artifacts.map((a) => {
                        const on = draft.artifact_ids.includes(a.id);
                        return (
                            <button key={a.id} type="button" aria-pressed={on}
                                onClick={() => setDraft({ ...draft, artifact_ids: on ? draft.artifact_ids.filter((x) => x !== a.id) : [...draft.artifact_ids, a.id] })}
                                className={cn('px-2 py-0.5 rounded-full border text-[11px] font-mono transition-colors', on ? 'bg-accent/15 text-accent border-accent/30' : 'bg-raised text-fg-3 border-line hover:text-fg')}>
                                {a.value}
                            </button>
                        );
                    })}
                </div>
            )}
            <div className="flex items-center gap-4 text-xs text-fg-2">
                <label className="flex items-center gap-1.5"><input type="checkbox" checked={draft.flagged} onChange={(e) => setDraft({ ...draft, flagged: e.target.checked })} /> Flagged</label>
                <label className="flex items-center gap-1.5"><input type="checkbox" checked={draft.in_summary} onChange={(e) => setDraft({ ...draft, in_summary: e.target.checked })} /> Include in report</label>
                <div className="ml-auto flex gap-2">
                    <button onClick={() => setEditing(null)} className="px-3 py-1.5 text-xs text-fg-3 hover:text-fg">Cancel</button>
                    <button onClick={() => save.mutate()} disabled={!draft.title.trim() || !draft.event_date || save.isPending}
                        className="px-3 py-1.5 text-xs bg-accent text-on-accent rounded-sm font-medium hover:bg-accent-hover disabled:opacity-50">
                        {save.isPending ? 'Saving…' : 'Save'}
                    </button>
                </div>
            </div>
            {save.isError && <p role="alert" className="text-xs text-severity-critical">Could not save the event.</p>}
        </div>
    );

    return (
        <div className="space-y-4 my-2">
            <div className="flex flex-wrap items-center gap-2">
                <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Filter… (e.g. category:lateral tag:psexec artifact:10.0 flag:true)"
                    className={cn(field, 'flex-1 min-w-48 font-mono text-xs')} aria-label="Filter events" />
                <button onClick={() => setFlaggedOnly((v) => !v)} aria-pressed={flaggedOnly}
                    className={cn('text-xs px-3 py-1.5 rounded-sm flex items-center gap-1.5 transition-colors', flaggedOnly ? 'bg-accent/15 text-accent' : 'bg-raised text-fg-2 hover:bg-raised-2')}>
                    <Flag size={12} /> Flagged
                </button>
                <button onClick={() => exportCsv(shown, caseId)} disabled={!shown.length} className="text-xs bg-raised hover:bg-raised-2 text-fg-2 px-3 py-1.5 rounded-sm flex items-center gap-1.5 disabled:opacity-50">
                    <Download size={12} /> CSV
                </button>
                {canEdit && (
                    <button onClick={() => setSuggesting((v) => !v)} aria-pressed={suggesting}
                        className="text-xs bg-raised hover:bg-raised-2 text-fg-2 px-3 py-1.5 rounded-sm flex items-center gap-1.5">
                        <Sparkles size={12} /> Suggest
                    </button>
                )}
                {canEdit && (
                    <button onClick={() => { setDraft(blank()); setEditing('new'); }} className="text-xs bg-accent text-on-accent hover:bg-accent-hover px-3 py-1.5 rounded-sm flex items-center gap-1.5 font-medium">
                        <Plus size={12} /> Add event
                    </button>
                )}
            </div>
            <p className="text-[11px] font-mono text-muted">{shown.length} of {events.length} events</p>
            {suggesting && <TimelineSuggest caseId={caseId} categories={EVENT_CATEGORIES} timezone={me?.timezone} onClose={() => setSuggesting(false)} />}
            {editing === 'new' && form}

            {!events.length && editing !== 'new' && !suggesting && (
                <div className="text-center py-12 text-muted text-sm">No investigation events yet. Add what happened, when, and where it was seen, or use Suggest to pull them from alerts, notes and pasted logs.</div>
            )}

            <div className="relative space-y-6">
                <div className="absolute left-[7px] top-0 bottom-0 w-px bg-raised" />
                {days.map(([day, list]) => (
                    <section key={day} className="space-y-3">
                        <h3 className="relative pl-6 text-[11px] font-mono uppercase tracking-wider text-muted">
                            <span className="absolute left-0 top-1 w-[15px] h-[15px] rounded-full bg-panel border-2 border-line" />
                            {day}
                        </h3>
                        {list.map((e) => {
                            const c = CAT[e.category] ?? CAT.unspecified;
                            if (editing === e.id) return <div key={e.id} className="pl-6">{form}</div>;
                            return (
                                <article key={e.id} className="relative pl-6 group">
                                    <span className="absolute left-[4px] top-3 w-[7px] h-[7px] rounded-full" style={{ background: c.color }} />
                                    <div className="rounded-lg border border-line bg-panel p-3 border-l-4" style={{ borderLeftColor: c.color }}>
                                        <div className="flex flex-wrap items-center gap-2">
                                            <span className="text-xs font-mono text-muted">{formatDateTime(e.event_date, me?.timezone)}</span>
                                            <span className="text-[10px] font-medium px-1.5 py-0.5 rounded-sm" style={{ color: c.color, background: `${c.color}22` }}>{c.label}</span>
                                            {e.source && <span className="text-[10px] font-mono text-fg-3 bg-raised px-1.5 py-0.5 rounded-sm">{e.source}</span>}
                                            {e.flagged && <Flag size={12} className="text-severity-high" aria-label="Flagged" />}
                                            {canEdit && (
                                                <span className="ml-auto flex items-center gap-2 opacity-0 group-hover:opacity-100 focus-within:opacity-100 transition-opacity">
                                                    <button onClick={() => patch.mutate({ id: e.id, flagged: !e.flagged })} title={e.flagged ? 'Unflag' : 'Flag'} className={cn('hover:text-fg', e.flagged ? 'text-severity-high' : 'text-muted')}><Flag size={13} /></button>
                                                    <button onClick={() => patch.mutate({ id: e.id, in_summary: !e.in_summary })} title={e.in_summary ? 'In report — click to exclude' : 'Excluded from report — click to include'} className={cn('hover:text-fg', e.in_summary ? 'text-accent' : 'text-muted')}>
                                                        {e.in_summary ? <FileText size={13} /> : <FileX size={13} />}
                                                    </button>
                                                    <button onClick={() => { setDraft(fromEvent(e)); setEditing(e.id); }} title="Edit" className="text-muted hover:text-fg"><Pencil size={13} /></button>
                                                    <button onClick={() => { if (confirm('Delete this event?')) remove.mutate(e.id); }} title="Delete" className="text-muted hover:text-severity-critical"><Trash2 size={13} /></button>
                                                </span>
                                            )}
                                        </div>
                                        <p className="mt-1 text-sm font-medium text-fg">{e.title}</p>
                                        {e.content && <p className="mt-1 text-sm text-fg-2 whitespace-pre-wrap">{e.content}</p>}
                                        {e.raw && <pre className="mt-2 text-[11px] font-mono text-fg-3 bg-raised rounded-sm p-2 overflow-x-auto whitespace-pre-wrap break-all">{e.raw}</pre>}
                                        {(e.tags.length > 0 || e.artifacts.length > 0) && (
                                            <div className="mt-2 flex flex-wrap gap-1.5">
                                                {e.artifacts.map((a) => <span key={a.id} className="px-2 py-0.5 rounded-full border border-accent/30 bg-accent/10 text-accent text-[11px] font-mono">{a.value}</span>)}
                                                {e.tags.map((t) => <span key={t} className="px-2 py-0.5 rounded-full border border-line bg-raised text-fg-3 text-[11px]">#{t}</span>)}
                                            </div>
                                        )}
                                    </div>
                                </article>
                            );
                        })}
                    </section>
                ))}
            </div>
        </div>
    );
}
