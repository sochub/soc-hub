import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { isAxiosError } from 'axios';
import { ExternalLink } from 'lucide-react';
import { api } from '../../api/client';
import { cn } from '../../lib/utils';
import type { EnrichmentResultRow, EnrichmentView, IOC } from '../../types';

const SOURCE_LABEL: Record<string, string> = {
    virustotal: 'VirusTotal', urlhaus: 'URLhaus', threatfox: 'ThreatFox', rdap: 'RDAP', crtsh: 'crt.sh',
};
const VERDICT_BADGE: Record<string, string> = {
    malicious: 'bg-red-50 text-red-700 border-red-200',
    suspicious: 'bg-amber-50 text-amber-700 border-amber-200',
    harmless: 'bg-green-50 text-green-700 border-green-200',
    unknown: 'bg-zinc-100 text-zinc-500 border-zinc-200',
};

const relTime = (iso: string): string => {
    const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
    if (s < 60) return 'just now';
    if (s < 3600) return `${Math.floor(s / 60)}m ago`;
    if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
    return `${Math.floor(s / 86400)}d ago`;
};

const facts = (summary: Record<string, unknown>): string[] => {
    const out: string[] = [];
    for (const [k, v] of Object.entries(summary ?? {})) {
        if (out.length >= 3) break;
        if (v === null || v === undefined || typeof v === 'object' && !Array.isArray(v)) continue;
        if (Array.isArray(v) && v.length > 3) continue;
        out.push(`${k}: ${Array.isArray(v) ? v.join(', ') : String(v)}`);
    }
    return out;
};

const statusText = (r: EnrichmentResultRow): string | null => {
    switch (r.status) {
        case 'pending': return r.stalled ? 'Stalled — re-check' : 'Checking…';
        case 'not_found': return 'No record';
        case 'skipped': return 'Not sent (private or internal)';
        case 'rate_limited': return r.error || 'Quota reached';
        case 'error': return r.error;
        default: return null;
    }
};

const errText = (e: unknown, fb: string): string => {
    const d: unknown = isAxiosError(e) ? e.response?.data?.detail : undefined;
    return typeof d === 'string' ? d : fb;
};

interface Props { kind: 'ioc' | 'artifact'; id: number; canRun: boolean; ioc?: IOC }

export default function EnrichmentPanel({ kind, id, canRun, ioc }: Props) {
    const qc = useQueryClient();
    const key = ['enrichment', kind, id];
    const [confirmMsg, setConfirmMsg] = useState<string | null>(null);
    const [err, setErr] = useState<string | null>(null);

    const { data, isLoading } = useQuery({
        queryKey: key,
        queryFn: async () => (await api.get(`/enrichment/${kind}/${id}`)).data as EnrichmentView,
        refetchInterval: (q) => q.state.data?.results.some((r) => r.status === 'pending' && !r.stalled) ? 3000 : false,
    });

    const run = useMutation({
        mutationFn: async (confirm: boolean) => api.post(`/enrichment/${kind}/${id}/run`, confirm ? { confirm: true } : undefined),
        onSuccess: () => { setConfirmMsg(null); setErr(null); qc.invalidateQueries({ queryKey: key }); },
        onError: (e) => {
            if (isAxiosError(e) && e.response?.status === 409) { setConfirmMsg(errText(e, 'Confirm to send this value to external services.')); return; }
            setConfirmMsg(null); setErr(errText(e, 'Lookup failed'));
        },
    });

    const apply = useMutation({
        onMutate: () => setErr(null),
        mutationFn: async () => {
            const s = data?.suggestion;
            if (!ioc || !s) return;
            // Merge into the IOC as it is now, not the (possibly stale) list row, so no edit is overwritten.
            const fresh = (await api.get(`/iocs/${id}`)).data as IOC;
            const tags = Array.from(new Set([...(fresh.tags ?? []), ...s.tags]));
            return api.put(`/iocs/${id}`, { threat_level: s.threat_level ?? fresh.threat_level, tags });
        },
        onSuccess: () => {
            qc.invalidateQueries({ queryKey: ['iocs'] });
            qc.invalidateQueries({ queryKey: ['enrichment', 'ioc', id] });
        },
        onError: (e) => setErr(errText(e, 'Could not apply the suggestion')),
    });

    if (isLoading || !data) return <p className="text-xs text-zinc-400">Loading…</p>;
    if (!data.enrichable) return <p className="text-xs text-zinc-400">Not enrichable ({data.indicator_type ?? ioc?.ioc_type ?? kind})</p>;

    const tlp = data.effective_tlp ?? 'unknown';
    const sug = kind === 'ioc' ? data.suggestion : null;

    return (
        <div className="space-y-2">
            {data.results.length === 0 ? (
                <p className="text-xs text-zinc-500">Not checked yet{!data.auto && ` (TLP:${tlp} — run manually)`}</p>
            ) : (
                <ul className="divide-y divide-zinc-100">
                    {data.results.map((r) => {
                        const st = statusText(r);
                        const f = facts(r.summary);
                        return (
                            <li key={r.source} className="py-1.5 flex items-center gap-2 flex-wrap text-xs">
                                <span className="font-semibold text-zinc-700 w-24">{SOURCE_LABEL[r.source] ?? r.source}</span>
                                {r.verdict && <span className={cn('px-1.5 py-0.5 border text-[11px] font-bold uppercase', VERDICT_BADGE[r.verdict] ?? VERDICT_BADGE.unknown)}>{r.verdict}</span>}
                                {r.score && <span className="num text-zinc-700">{r.score}</span>}
                                {st && <span className={r.status === 'error' || r.status === 'rate_limited' ? 'text-red-700' : 'text-zinc-500'}>{st}</span>}
                                {f.map((x) => <span key={x} className="text-zinc-500 font-mono">{x}</span>)}
                                {r.link && <a href={r.link} target="_blank" rel="noopener noreferrer" className="text-accent-600 inline-flex items-center gap-0.5">view<ExternalLink size={11} /></a>}
                                {r.fetched_at && <span className="ml-auto text-zinc-400">{relTime(r.fetched_at)}</span>}
                            </li>
                        );
                    })}
                </ul>
            )}

            {sug && (sug.threat_level || sug.tags.length > 0) && (
                <div className="flex items-center gap-2 bg-amber-50 border border-amber-200 px-2 py-1 text-xs text-amber-800">
                    <span>Suggest: {sug.threat_level ?? ''}{sug.tags.length > 0 && ` +${sug.tags.join(' +')}`}</span>
                    {canRun && <button onClick={() => apply.mutate()} disabled={apply.isPending} className="ml-auto h-6 px-2 bg-amber-600 text-white disabled:opacity-50">Apply</button>}
                </div>
            )}

            {canRun && (confirmMsg ? (
                <div className="bg-amber-50 border border-amber-200 p-2 text-xs text-amber-800 space-y-2">
                    <p>{confirmMsg}</p>
                    <div className="flex gap-2">
                        <button onClick={() => run.mutate(true)} disabled={run.isPending} className="h-7 px-3 bg-accent-600 text-white disabled:opacity-50">Send</button>
                        <button onClick={() => setConfirmMsg(null)} className="h-7 px-3 border border-zinc-300 bg-white text-zinc-700">Cancel</button>
                    </div>
                </div>
            ) : (
                <button onClick={() => { setErr(null); run.mutate(false); }} disabled={run.isPending} className="h-7 px-3 border border-zinc-300 text-xs disabled:opacity-50">
                    {data.results.length === 0 ? 'Enrich' : 'Re-check'}
                </button>
            ))}
            {err && <p role="status" className="text-xs text-red-700">{err}</p>}
        </div>
    );
}
