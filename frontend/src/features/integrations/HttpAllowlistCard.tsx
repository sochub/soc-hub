import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../../api/client';

function errorText(e: unknown): string {
    const d = (e as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
    return typeof d === 'string' ? d : 'Save failed';
}

export default function HttpAllowlistCard() {
    const qc = useQueryClient();
    const { data } = useQuery({ queryKey: ['http-allowlist'], queryFn: async () => (await api.get('/workflows/settings/http-allowlist')).data as { hosts: string[] } });
    const [draft, setText] = useState<string | null>(null);
    const text = draft ?? data?.hosts.join('\n') ?? '';
    const save = useMutation({
        mutationFn: async () => (await api.put('/workflows/settings/http-allowlist', { hosts: text.split('\n') })).data,
        onSuccess: (res) => { setText(null); qc.setQueryData(['http-allowlist'], res); },
    });
    return (
        <div className="bg-panel border border-line p-5">
            <h2 className="font-semibold text-fg">Automation HTTP allowlist</h2>
            <p className="text-xs text-fg-3 mt-1 mb-3">
                Workflow HTTP requests to private or internal addresses are blocked. List internal hostnames (one per line, e.g. <span className="font-mono">soar.corp.example.com</span> or <span className="font-mono">*.corp.example.com</span>) that workflows may call. IP addresses and single-label names are not accepted; reaching private addresses also requires the platform operator to enable it. Loopback and cloud metadata addresses are always blocked.
            </p>
            <textarea rows={4} className="w-full border border-line-strong px-2 py-1.5 font-mono text-xs" value={text}
                placeholder="soar.internal.corp" onChange={(e) => { save.reset(); setText(e.target.value); }} />
            <button onClick={() => save.mutate()} disabled={save.isPending}
                className="mt-2 h-8 px-3 bg-accent text-on-accent text-sm disabled:opacity-50">{save.isSuccess ? 'Saved' : 'Save'}</button>
            {save.isError && <span role="alert" className="ml-2 text-xs text-red-300">{errorText(save.error)}</span>}
        </div>
    );
}
