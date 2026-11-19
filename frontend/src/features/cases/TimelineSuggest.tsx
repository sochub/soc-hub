import { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { Sparkles, X } from 'lucide-react';
import { api } from '../../api/client';
import { cn } from '../../lib/utils';
import { Button, Badge, fieldClass } from '../../components/ui';
import { formatDateTime } from '../../utils/datetime';

interface Suggested {
    event_date: string; title: string; content: string | null; raw: string | null; source: string | null;
    category: string; origin: 'ai' | 'alert' | 'log'; artifact_ids: number[];
}
type Row = Suggested & { keep: boolean };

const ORIGIN: Record<Suggested['origin'], string> = { ai: 'AI', alert: 'Alert', log: 'Pasted log' };
const field = fieldClass(false, 'sm');

// Review-before-save: suggestions come from untrusted alert payloads and pasted text, so nothing is written
// until the analyst picks rows and presses Add.
export default function TimelineSuggest({ caseId, categories, timezone, onClose }: {
    caseId: number; categories: [string, string, string][]; timezone?: string | null; onClose: () => void;
}) {
    const qc = useQueryClient();
    const [text, setText] = useState('');
    const [useAi, setUseAi] = useState(true);
    const [naiveTz, setNaiveTz] = useState('UTC');
    const localTz = timezone || Intl.DateTimeFormat().resolvedOptions().timeZone;
    const [rows, setRows] = useState<Row[] | null>(null);
    const [aiState, setAiState] = useState<'used' | 'unavailable' | 'off' | null>(null);

    const find = useMutation({
        mutationFn: async () => (await api.post(`/cases/${caseId}/events/suggest`, { text: text || null, use_ai: useAi, naive_tz: naiveTz })).data as
            { events: Suggested[]; ai: 'used' | 'unavailable' | 'off' },
        onSuccess: (d) => { setRows(d.events.map((e) => ({ ...e, keep: true }))); setAiState(d.ai); },
    });
    const add = useMutation({
        // ponytail: one POST per event; add a bulk endpoint if people add hundreds at once.
        mutationFn: async (picked: Row[]) => {
            for (const r of picked) {
                await api.post(`/cases/${caseId}/events`, {
                    event_date: r.event_date, title: r.title, content: r.content, raw: r.raw, source: r.source,
                    category: r.category, artifact_ids: r.artifact_ids, tags: ['auto'],
                });
            }
        },
        onSettled: () => qc.invalidateQueries({ queryKey: ['case-events', caseId] }),
        onSuccess: onClose,
    });

    const set = (i: number, patch: Partial<Row>) => setRows((rs) => rs && rs.map((r, j) => (j === i ? { ...r, ...patch } : r)));
    const picked = (rows || []).filter((r) => r.keep && r.title.trim());

    return (
        <div className="p-4 rounded-md border border-line bg-panel space-y-3">
            <div className="flex items-center justify-between">
                <h3 className="text-sm font-semibold text-fg flex items-center gap-2"><Sparkles size={14} className="text-accent" aria-hidden /> Suggest events</h3>
                <Button variant="ghost" size="icon" className="h-7 w-7" onClick={onClose} aria-label="Close"><X size={16} /></Button>
            </div>
            <p className="text-xs text-fg-3">
                Scans this case's alerts and analyst notes. Paste logs or notes below to include them; lines with a timestamp become events.
            </p>
            <textarea value={text} onChange={(e) => setText(e.target.value)} rows={4} maxLength={50000}
                placeholder="2026-10-01 10:02:03 sshd: Accepted password for root from 203.0.113.7"
                className={cn(fieldClass(), 'h-auto py-2 resize-y font-mono text-xs')} aria-label="Pasted logs or notes" />
            <div className="flex flex-wrap items-center gap-3">
                <label className="text-xs text-fg-2 flex items-center gap-1.5">
                    <input type="checkbox" checked={useAi} onChange={(e) => setUseAi(e.target.checked)} /> Use AI
                </label>
                <label className="text-xs text-fg-2 flex items-center gap-1.5">
                    Times without a zone are
                    <select value={naiveTz} onChange={(e) => setNaiveTz(e.target.value)} className={cn(field, 'w-auto')} aria-label="Time zone for pasted timestamps">
                        <option value="UTC">UTC</option>
                        {localTz && localTz !== 'UTC' && <option value={localTz}>{localTz}</option>}
                    </select>
                </label>
                <Button size="sm" onClick={() => find.mutate()} disabled={find.isPending}>
                    <Sparkles size={12} aria-hidden /> {find.isPending ? 'Looking…' : 'Find events'}
                </Button>
            </div>
            {find.isError && <p role="alert" className="text-xs text-severity-critical">Could not get suggestions.</p>}
            {aiState === 'unavailable' && (
                <p role="status" className="text-xs text-severity-medium">AI is unavailable, so these come from parsing only. Check Settings › AI.</p>
            )}

            {rows && (rows.length === 0 ? (
                <p className="text-xs text-fg-3">Nothing new found. Events already on the timeline are skipped.</p>
            ) : (
                <>
                    <div className="flex items-center justify-between text-xs text-fg-3">
                        <label className="flex items-center gap-1.5">
                            <input type="checkbox" checked={rows.every((r) => r.keep)}
                                onChange={(e) => setRows(rows.map((r) => ({ ...r, keep: e.target.checked })))} /> {picked.length} of {rows.length} selected
                        </label>
                    </div>
                    <ul className="space-y-2 max-h-96 overflow-y-auto pr-1">
                        {rows.map((r, i) => (
                            <li key={i} className={cn('flex flex-wrap sm:flex-nowrap items-center gap-2 p-2 rounded-md border border-line', !r.keep && 'opacity-50')}>
                                <input type="checkbox" checked={r.keep} onChange={(e) => set(i, { keep: e.target.checked })} aria-label={`Keep ${r.title}`} />
                                <span className="text-xs num text-fg-3 shrink-0 w-36">{formatDateTime(r.event_date, timezone)}</span>
                                <Badge className="shrink-0">{ORIGIN[r.origin]}</Badge>
                                <select value={r.category} onChange={(e) => set(i, { category: e.target.value })} className={cn(field, 'w-auto shrink-0')} aria-label="Category">
                                    {categories.map(([k, label]) => <option key={k} value={k}>{label}</option>)}
                                </select>
                                <input value={r.title} onChange={(e) => set(i, { title: e.target.value })} className={cn(field, 'flex-1 min-w-40')} aria-label="Title" />
                            </li>
                        ))}
                    </ul>
                    <div className="flex justify-end gap-2">
                        {add.isError && <p role="alert" className="text-xs text-severity-critical mr-auto">Some events could not be added.</p>}
                        <Button size="sm" onClick={() => add.mutate(picked)} disabled={!picked.length || add.isPending}>
                            {add.isPending ? 'Adding…' : `Add ${picked.length} event${picked.length === 1 ? '' : 's'}`}
                        </Button>
                    </div>
                </>
            ))}
        </div>
    );
}
