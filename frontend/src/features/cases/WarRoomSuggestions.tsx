import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Bot, Check, ExternalLink, Loader2, X } from 'lucide-react';
import { api } from '../../api/client';
import type { WarRoomSuggestion } from '../../types';

const TYPE_STYLE: Record<string, [string, string]> = {
    ip: ['🌐', 'IP'], domain: ['🔗', 'domain'], url: ['🔗', 'URL'], file_hash: ['🧬', 'file hash'],
    email: ['✉️', 'email address'], hostname: ['🖥️', 'host'], username: ['👤', 'user'],
};
const MILESTONE_STYLE: Record<string, [string, string]> = {
    first_seen: ['👀', 'First seen'], detected: ['🚨', 'Detected'], contained: ['🛡️', 'Contained'], recovered: ['✅', 'Recovered'],
};

function describe(s: WarRoomSuggestion): { text: string; accept: string } {
    if (s.kind === 'artifact') {
        const t = s.payload.type ?? '';
        const [emoji, noun] = TYPE_STYLE[t] ?? ['🔎', t];
        return { text: `${emoji} Add the ${noun} ${s.payload.value ?? ''} to the case artifacts?`, accept: 'Add to case' };
    }
    const key = s.payload.key ?? '';
    const [emoji, title] = MILESTONE_STYLE[key] ?? ['🔎', key];
    const at = s.payload.at ? new Date(s.payload.at) : null;
    const when = at && !Number.isNaN(at.getTime()) ? at.toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' }) : (s.payload.at ?? '');
    return { text: `${emoji} Set the case's ${title} milestone to ${when}?`, accept: 'Set milestone' };
}

export default function WarRoomSuggestions({ caseId }: { caseId: number }) {
    const qc = useQueryClient();
    const key = ['war-room-suggestions', caseId];
    const { data } = useQuery({
        queryKey: key,
        queryFn: async () => (await api.get(`/cases/${caseId}/war-room/suggestions`, { params: { status: 'pending' } })).data as WarRoomSuggestion[],
    });
    const act = useMutation({
        mutationFn: async ({ sid, verb }: { sid: number; verb: 'accept' | 'dismiss' }) =>
            (await api.post(`/cases/${caseId}/war-room/suggestions/${sid}/${verb}`)).data,
        onSettled: () => {
            qc.invalidateQueries({ queryKey: key });
            qc.invalidateQueries({ queryKey: ['case'] });
        },
    });
    if (!data || data.length === 0) return null;
    const busy = act.isPending ? act.variables?.sid : null;

    return (
        <div className="mb-4 flex flex-wrap items-center gap-2" aria-label="SOC-bot suggestions">
            <span className="flex items-center gap-1 text-xs text-fg-3"><Bot size={13} /> SOC-bot</span>
            {data.map((s) => {
                const d = describe(s);
                return (
                    <div key={s.id} className="flex items-center gap-2 rounded-lg border border-amber-500/30 bg-amber-500/[0.07] px-2.5 py-1.5 text-xs">
                        <span className="text-fg-2">{d.text}</span>
                        {s.permalink?.startsWith('https://') && (
                            <a href={s.permalink} target="_blank" rel="noopener noreferrer" title="Open in Slack" className="text-fg-3 hover:text-fg-2">
                                <ExternalLink size={12} />
                            </a>
                        )}
                        {busy === s.id ? <Loader2 size={13} className="animate-spin text-fg-2" /> : (
                            <span className="flex items-center gap-1">
                                <button type="button" onClick={() => act.mutate({ sid: s.id, verb: 'accept' })}
                                    className="inline-flex items-center gap-1 rounded-sm bg-amber-500/90 px-2 py-0.5 font-semibold text-on-accent hover:bg-amber-400 transition-colors">
                                    <Check size={11} /> {d.accept}
                                </button>
                                <button type="button" onClick={() => act.mutate({ sid: s.id, verb: 'dismiss' })} aria-label="Dismiss suggestion"
                                    className="p-0.5 text-fg-3 hover:text-fg-2"><X size={13} /></button>
                            </span>
                        )}
                    </div>
                );
            })}
            {act.isError && <span className="text-xs text-severity-critical">Action failed</span>}
        </div>
    );
}
