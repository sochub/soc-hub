import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { isAxiosError } from 'axios';
import { ExternalLink } from 'lucide-react';
import { api } from '../../api/client';
import { Button, Skeleton, ErrorState, Stamp, verdictTone } from '../../components/ui';
import type { EnrichmentResultRow, EnrichmentView, IOC } from '../../types';

const SOURCE_LABEL: Record<string, string> = {
    virustotal: 'VirusTotal', urlhaus: 'URLhaus', threatfox: 'ThreatFox', rdap: 'RDAP', crtsh: 'crt.sh', misp: 'MISP',
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

    const { data, isLoading, isError, refetch } = useQuery({
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

    if (isError) return <ErrorState message="Could not load threat intel." onRetry={() => refetch()} />;
    if (isLoading || !data) return <Skeleton rows={2} />;
    if (!data.enrichable) return <p className="text-xs text-muted">Not enrichable ({data.indicator_type ?? ioc?.ioc_type ?? kind})</p>;

    const tlp = data.effective_tlp ?? 'unknown';
    const sug = kind === 'ioc' ? data.suggestion : null;

    return (
        <div className="space-y-2">
            {data.results.length === 0 ? (
                <p className="text-xs text-fg-3">Not checked yet{!data.auto && ` (TLP:${tlp} — run manually)`}</p>
            ) : (
                <ul className="divide-y divide-line">
                    {data.results.map((r) => {
                        const st = statusText(r);
                        const f = facts(r.summary);
                        return (
                            <li key={r.source} className="py-1.5 flex items-center gap-2 flex-wrap text-xs">
                                <span className="font-semibold text-fg-2 w-24">{SOURCE_LABEL[r.source] ?? r.source}</span>
                                {r.verdict && <Stamp tone={verdictTone(r.verdict)}>{r.verdict}</Stamp>}
                                {r.score && <span className="num text-fg-2">{r.score}</span>}
                                {st && <span className={r.status === 'error' || r.status === 'rate_limited' ? 'text-severity-critical' : 'text-fg-3'}>{st}</span>}
                                {f.map((x) => <span key={x} className="text-fg-3 font-mono">{x}</span>)}
                                {r.link && <a href={r.link} target="_blank" rel="noopener noreferrer" className="text-accent inline-flex items-center gap-0.5">view<ExternalLink size={11} aria-hidden /></a>}
                                {r.fetched_at && <span className="ml-auto text-fg-3 num">{relTime(r.fetched_at)}</span>}
                            </li>
                        );
                    })}
                </ul>
            )}

            {sug && (sug.threat_level || sug.tags.length > 0) && (
                <div className="flex items-center gap-2 rounded-md border border-severity-medium/40 bg-severity-medium/10 px-2 py-1 text-xs text-fg-2">
                    <span>Suggest: {sug.threat_level ?? ''}{sug.tags.length > 0 && ` +${sug.tags.join(' +')}`}</span>
                    {canRun && <Button size="sm" variant="secondary" onClick={() => apply.mutate()} disabled={apply.isPending} className="ml-auto h-6">Apply</Button>}
                </div>
            )}

            {canRun && (confirmMsg ? (
                <div className="rounded-md border border-severity-medium/40 bg-severity-medium/10 p-2 text-xs text-fg-2 space-y-2">
                    <p>{confirmMsg}</p>
                    <div className="flex gap-2">
                        <Button size="sm" onClick={() => run.mutate(true)} disabled={run.isPending}>Send</Button>
                        <Button size="sm" variant="secondary" onClick={() => setConfirmMsg(null)}>Cancel</Button>
                    </div>
                </div>
            ) : (
                <Button size="sm" variant="secondary" onClick={() => { setErr(null); run.mutate(false); }} disabled={run.isPending}>
                    {data.results.length === 0 ? 'Enrich' : 'Re-check'}
                </Button>
            ))}
            {err && <p role="alert" className="text-xs text-severity-critical">{err}</p>}
        </div>
    );
}
