import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ShieldQuestion } from 'lucide-react';
import { isAxiosError } from 'axios';
import { api } from '../../api/client';

interface TIConfig {
    auto_max_tlp: string; artifact_tlp: string; cache_ttl_hours: number;
    sources: Record<string, boolean>; vt_per_minute: number; vt_per_day: number;
    internal_domains: string[];
    credentials_set: { virustotal: boolean; abusech: boolean };
}
interface Draft {
    auto_max_tlp?: string; artifact_tlp?: string; cache_ttl_hours?: number | string;
    vt_per_minute?: number | string; vt_per_day?: number | string;
    sources?: Record<string, boolean>; domains?: string;
    vt_key?: string; abusech_key?: string; clear_virustotal?: boolean; clear_abusech?: boolean;
}

const SOURCE_LABEL: Record<string, string> = {
    virustotal: 'VirusTotal', urlhaus: 'URLhaus', threatfox: 'ThreatFox', rdap: 'RDAP', crtsh: 'crt.sh',
};
const AUTO_TLP = ['none', 'white', 'green', 'amber', 'red'];
const TLP = ['white', 'green', 'amber', 'red'];

const errText = (e: unknown, fb: string): string => {
    const d: unknown = isAxiosError(e) ? e.response?.data?.detail : undefined;
    if (typeof d === 'string') return d;
    if (Array.isArray(d) && typeof d[0]?.msg === 'string') return d[0].msg;
    return fb;
};

export default function ThreatIntelCard() {
    const qc = useQueryClient();
    const { data } = useQuery({ queryKey: ['ti-config'], queryFn: async () => (await api.get('/enrichment/config')).data as TIConfig });
    const [draft, setDraft] = useState<Draft>({});
    const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
    const [tests, setTests] = useState<Record<string, { ok: boolean; message: string }> | null>(null);
    const [confirmReset, setConfirmReset] = useState(false);

    const set = (p: Draft) => { setDraft((d) => ({ ...d, ...p })); setMsg(null); };

    // PUT replaces the whole config, so always send every non-key field (loaded value unless edited).
    const body = (d: TIConfig) => {
        const b: Record<string, unknown> = {
            auto_max_tlp: draft.auto_max_tlp ?? d.auto_max_tlp,
            artifact_tlp: draft.artifact_tlp ?? d.artifact_tlp,
            cache_ttl_hours: Number(draft.cache_ttl_hours ?? d.cache_ttl_hours),
            vt_per_minute: Number(draft.vt_per_minute ?? d.vt_per_minute),
            vt_per_day: Number(draft.vt_per_day ?? d.vt_per_day),
            sources: draft.sources ?? d.sources,
            internal_domains: draft.domains !== undefined
                ? draft.domains.split('\n').map((x) => x.trim()).filter(Boolean) : d.internal_domains,
        };
        if (draft.vt_key?.trim()) b.virustotal_api_key = draft.vt_key.trim();
        if (draft.abusech_key?.trim()) b.abusech_auth_key = draft.abusech_key.trim();
        if (draft.clear_virustotal) b.clear_virustotal = true;
        if (draft.clear_abusech) b.clear_abusech = true;
        return b;
    };

    const save = useMutation({
        mutationFn: async (d: TIConfig) => (await api.put('/enrichment/config', body(d))).data as TIConfig,
        onSuccess: (r) => { qc.setQueryData(['ti-config'], r); setDraft({}); setTests(null); setMsg({ ok: true, text: 'Saved' }); },
        onError: (e) => setMsg({ ok: false, text: errText(e, 'Save failed') }),
    });
    const test = useMutation({
        mutationFn: async () => (await api.post('/enrichment/config/test')).data as Record<string, { ok: boolean; message: string }>,
        onSuccess: (r) => { setTests(r); setMsg(null); },
        onError: (e) => setMsg({ ok: false, text: errText(e, 'Test failed') }),
    });
    const reset = useMutation({
        mutationFn: async () => api.delete('/enrichment/config'),
        onSuccess: () => {
            setDraft({}); setTests(null); setConfirmReset(false);
            qc.invalidateQueries({ queryKey: ['ti-config'] }); setMsg({ ok: true, text: 'Reset to defaults' });
        },
        onError: (e) => { setConfirmReset(false); setMsg({ ok: false, text: errText(e, 'Reset failed') }); },
    });

    if (!data) return null;
    const input = 'w-full border border-zinc-300 px-2 py-1.5 text-sm';
    const sources = draft.sources ?? data.sources;
    const keyField = (label: string, isSet: boolean, valKey: 'vt_key' | 'abusech_key', clearKey: 'clear_virustotal' | 'clear_abusech', note?: string) => (
        <div>
            <label className="block"><span className="label-mono">{label}{isSet && <span className="ml-2 text-emerald-700">· set</span>}</span>
                <input type="password" autoComplete="off" className={`${input} font-mono`} value={draft[valKey] ?? ''}
                    onChange={(e) => set({ [valKey]: e.target.value })} placeholder={isSet ? '•••• (saved — leave blank to keep)' : ''} /></label>
            {note && <p className="text-xs text-zinc-500 mt-1">{note}</p>}
            {isSet && <label className="flex items-center gap-2 text-xs text-zinc-600 mt-1"><input type="checkbox" checked={!!draft[clearKey]} onChange={(e) => set({ [clearKey]: e.target.checked })} />Clear</label>}
        </div>
    );

    return (
        <div className="bg-white border border-zinc-200 p-5 space-y-3">
            <div className="flex items-center gap-2">
                <ShieldQuestion size={16} className="text-accent-600" />
                <h2 className="font-semibold text-zinc-800">Threat intelligence</h2>
            </div>
            <p className="text-xs text-zinc-500">Indicators are sent to the enabled external sources to enrich IOCs and artifacts. Private and internal values are never sent.</p>

            {keyField('VirusTotal API key', data.credentials_set.virustotal, 'vt_key', 'clear_virustotal')}
            {keyField('abuse.ch Auth-Key', data.credentials_set.abusech, 'abusech_key', 'clear_abusech', 'Free key from auth.abuse.ch — enables URLhaus and ThreatFox.')}

            <fieldset>
                <legend className="label-mono">sources</legend>
                <div className="flex flex-wrap gap-4 mt-1">
                    {Object.keys(SOURCE_LABEL).map((s) => (
                        <label key={s} className="flex items-center gap-2 text-sm"><input type="checkbox" checked={sources[s] ?? true}
                            onChange={(e) => set({ sources: { ...sources, [s]: e.target.checked } })} />{SOURCE_LABEL[s]}</label>
                    ))}
                </div>
            </fieldset>

            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                <label className="block"><span className="label-mono">Auto-enrich up to TLP</span>
                    <select className={input} value={draft.auto_max_tlp ?? data.auto_max_tlp} onChange={(e) => set({ auto_max_tlp: e.target.value })}>
                        {AUTO_TLP.map((t) => <option key={t} value={t}>{t}</option>)}</select></label>
                <label className="block"><span className="label-mono">Treat artifacts as TLP</span>
                    <select className={input} value={draft.artifact_tlp ?? data.artifact_tlp} onChange={(e) => set({ artifact_tlp: e.target.value })}>
                        {TLP.map((t) => <option key={t} value={t}>{t}</option>)}</select></label>
            </div>
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
                <label className="block"><span className="label-mono">cache hours</span>
                    <input type="number" min={1} max={720} className={`${input} num`} value={draft.cache_ttl_hours ?? data.cache_ttl_hours} onChange={(e) => set({ cache_ttl_hours: e.target.value })} /></label>
                <label className="block"><span className="label-mono">VT per minute</span>
                    <input type="number" min={1} className={`${input} num`} value={draft.vt_per_minute ?? data.vt_per_minute} onChange={(e) => set({ vt_per_minute: e.target.value })} /></label>
                <label className="block"><span className="label-mono">VT per day</span>
                    <input type="number" min={1} className={`${input} num`} value={draft.vt_per_day ?? data.vt_per_day} onChange={(e) => set({ vt_per_day: e.target.value })} /></label>
            </div>
            <label className="block"><span className="label-mono">internal domains (one per line, never sent)</span>
                <textarea rows={3} className={`${input} font-mono text-xs`} value={draft.domains ?? data.internal_domains.join('\n')} onChange={(e) => set({ domains: e.target.value })} placeholder="corp.example.com" /></label>

            <div className="flex items-center gap-2 flex-wrap">
                <button onClick={() => save.mutate(data)} disabled={save.isPending} className="h-8 px-3 bg-accent-600 text-white text-sm disabled:opacity-50">Save</button>
                <button onClick={() => test.mutate()} disabled={test.isPending} className="h-8 px-3 border border-zinc-300 text-sm disabled:opacity-50">{test.isPending ? 'Testing…' : 'Test keys'}</button>
                {confirmReset ? (
                    <span className="flex items-center gap-2 text-xs text-zinc-700">Remove keys and settings for this tenant?
                        <button onClick={() => reset.mutate()} disabled={reset.isPending} className="h-8 px-3 bg-red-600 text-white text-sm disabled:opacity-50">Reset</button>
                        <button onClick={() => setConfirmReset(false)} className="h-8 px-3 border border-zinc-300 text-sm">Cancel</button></span>
                ) : <button onClick={() => setConfirmReset(true)} className="h-8 px-3 border border-zinc-300 text-sm">Reset to defaults</button>}
                {msg && <span role="status" className={msg.ok ? 'text-xs text-emerald-700' : 'text-xs text-red-700'}>{msg.text}</span>}
            </div>
            {tests && (
                <ul className="text-xs space-y-0.5">
                    {Object.entries(tests).map(([k, v]) => (
                        <li key={k} className={v.ok ? 'text-emerald-700' : 'text-red-700'}><span className="font-semibold">{k === 'abusech' ? 'abuse.ch' : 'VirusTotal'}</span>: {v.message}</li>
                    ))}
                </ul>
            )}
        </div>
    );
}
