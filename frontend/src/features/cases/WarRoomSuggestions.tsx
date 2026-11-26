import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Bot, Check, ExternalLink, Loader2, X } from 'lucide-react';
import { api } from '../../api/client';
import type { WarRoomSuggestion } from '../../types';
import { Button, useConfirm } from '../../components/ui';

const TYPE_NOUN: Record<string, string> = {
    ip: 'IP', domain: 'domain', url: 'URL', file_hash: 'file hash',
    email: 'email address', hostname: 'host', username: 'user',
};
const MILESTONE_TITLE: Record<string, string> = {
    first_seen: 'First seen', detected: 'Detected', contained: 'Contained', recovered: 'Recovered',
};

function when(iso?: string | null): string {
    const at = iso ? new Date(iso) : null;
    return at && !Number.isNaN(at.getTime()) ? at.toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' }) : (iso ?? '');
}

function describe(s: WarRoomSuggestion): { text: string; accept: string } {
    if (s.kind === 'artifact') {
        const t = s.payload.type ?? '';
        const noun = TYPE_NOUN[t] ?? t;
        return { text: `Add the ${noun} ${s.payload.value ?? ''} to the case artifacts?`, accept: 'Add to case' };
    }
    const key = s.payload.key ?? '';
    const title = MILESTONE_TITLE[key] ?? key;
    // accepting replaces a time someone declared on the case: say which
    const current = s.current ? ` (currently ${when(s.current)})` : '';
    return { text: `Set the case's ${title} milestone to ${when(s.payload.at)}?${current}`, accept: 'Set milestone' };
}

export default function WarRoomSuggestions({ caseId }: { caseId: number }) {
    const qc = useQueryClient();
    const { confirm, dialog } = useConfirm();
    const key = ['war-room-suggestions', caseId];
    const { data } = useQuery({
        queryKey: key,
        queryFn: async () => (await api.get(`/cases/${caseId}/war-room/suggestions`, { params: { status: 'pending' } })).data as WarRoomSuggestion[],
        refetchInterval: 15_000,  // Slack keeps talking while the page is open
    });
    const refresh = (accepted: boolean) => {
        qc.invalidateQueries({ queryKey: key });
        qc.invalidateQueries({ queryKey: ['case'] });
        if (accepted) {
            qc.invalidateQueries({ queryKey: ['artifacts', String(caseId)] });
            qc.invalidateQueries({ queryKey: ['artifacts', 'all'] });
            qc.invalidateQueries({ queryKey: ['case-events', caseId] });
        }
    };
    const act = useMutation({
        mutationFn: async ({ sid, verb }: { sid: number; verb: 'accept' | 'dismiss' }) =>
            (await api.post(`/cases/${caseId}/war-room/suggestions/${sid}/${verb}`)).data,
        onSettled: (_d, _e, v) => refresh(v.verb === 'accept'),
    });
    const bulk = useMutation({
        mutationFn: async (accept: boolean) =>
            (await api.post(`/cases/${caseId}/war-room/suggestions/bulk`, { ids: (data ?? []).map((s) => s.id), accept })).data,
        onSettled: (_d, _e, accept) => refresh(accept),
    });
    if (!data || data.length === 0) return null;
    const busy = act.isPending ? act.variables?.sid : null;
    const addAll = async () => {
        if (await confirm({ title: `Add all ${data.length} suggestions to the case?`, confirmLabel: 'Add all',
            body: 'Every artifact is added and every milestone time is set. A milestone that would be out of order stays here.' })) bulk.mutate(true);
    };

    return (
        <div className="mb-4 flex flex-wrap items-center gap-2" aria-label="SOC-bot suggestions">
            <span className="flex items-center gap-1 text-xs text-fg-3"><Bot size={13} aria-hidden /> SOC-bot</span>
            {data.map((s) => {
                const d = describe(s);
                return (
                    <div key={s.id} className="flex items-center gap-2 rounded-md border border-severity-medium/30 bg-severity-medium/5 px-2.5 py-1.5 text-xs">
                        <span className="text-fg-2">{d.text}</span>
                        {s.payload.conflict && <span role="alert" className="text-severity-critical">Not set: {s.payload.conflict}. Fix it on the case, then try again.</span>}
                        {s.permalink?.startsWith('https://') && (
                            <a href={s.permalink} target="_blank" rel="noopener noreferrer" title="Open in Slack" aria-label="Open in Slack" className="text-fg-3 hover:text-fg-2">
                                <ExternalLink size={12} />
                            </a>
                        )}
                        {busy === s.id || bulk.isPending ? <Loader2 size={13} className="animate-spin text-fg-2" aria-label="Working" /> : (
                            <span className="flex items-center gap-1">
                                <Button size="sm" className="h-6 px-2" aria-label={`${d.accept}: ${d.text}`} onClick={() => act.mutate({ sid: s.id, verb: 'accept' })}>
                                    <Check size={11} aria-hidden /> {d.accept}
                                </Button>
                                <button type="button" onClick={() => act.mutate({ sid: s.id, verb: 'dismiss' })} aria-label={`Dismiss suggestion: ${d.text}`}
                                    className="p-0.5 text-fg-3 hover:text-fg-2"><X size={13} /></button>
                            </span>
                        )}
                    </div>
                );
            })}
            {data.length > 1 && !bulk.isPending && (
                <span className="flex items-center gap-1 text-xs">
                    <Button size="sm" className="h-6 px-2" aria-label="Add all suggestions to the case" onClick={addAll}>
                        <Check size={11} aria-hidden /> Add all
                    </Button>
                    <button type="button" onClick={() => bulk.mutate(false)} aria-label="Dismiss all suggestions"
                        className="px-1 text-fg-3 hover:text-fg-2">Dismiss all</button>
                </span>
            )}
            {(act.isError || bulk.isError) && <span role="alert" className="text-xs text-severity-critical">Action failed</span>}
            {dialog}
        </div>
    );
}
