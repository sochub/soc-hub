import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '../../api/client';
import type { User } from '../../types';
import { Plus, Trash2, Copy, Check } from 'lucide-react';

interface Webhook { id: number; name: string; api_key: string; created_at: string; }

export default function Integrations() {
    const qc = useQueryClient();
    const [name, setName] = useState('');
    const [copied, setCopied] = useState<number | null>(null);

    const { data: me } = useQuery({ queryKey: ['currentUser'], queryFn: async () => (await api.get('/users/me')).data as User });
    const isAdmin = me?.role === 'admin' || me?.is_super_admin;

    const { data: webhooks } = useQuery<Webhook[]>({
        queryKey: ['webhooks'],
        queryFn: async () => (await api.get('/integrations/webhooks')).data,
        enabled: !!isAdmin,
    });

    const create = useMutation({
        mutationFn: async (n: string) => (await api.post('/integrations/webhooks', { name: n })).data,
        onSuccess: () => { setName(''); qc.invalidateQueries({ queryKey: ['webhooks'] }); },
    });
    const revoke = useMutation({
        mutationFn: async (id: number) => api.delete(`/integrations/webhooks/${id}`),
        onSuccess: () => qc.invalidateQueries({ queryKey: ['webhooks'] }),
    });

    const copy = async (wh: Webhook) => {
        try {
            await navigator.clipboard.writeText(wh.api_key);
            setCopied(wh.id);
            setTimeout(() => setCopied(null), 1500);
        } catch {
            // clipboard unavailable (e.g. non-secure context) — don't show a false "copied"
        }
    };
    const ingestUrl = `${window.location.origin}/api/v1/alerts/webhook`;

    return (
        <div className="space-y-6">
            <div>
                <h1 className="text-3xl font-bold text-zinc-900 tracking-tight">Integrations</h1>
                <p className="text-zinc-500 mt-1">Create webhooks so tools like a SIEM can push alerts into a tenant.</p>
            </div>

            {!isAdmin ? (
                <div className="glass-panel p-8 rounded-xl text-center text-zinc-500">Ask a tenant admin to manage webhooks.</div>
            ) : (
                <div className="space-y-6">
                    <div className="glass-panel p-5 rounded-xl border border-zinc-200">
                        <h2 className="font-semibold text-zinc-800 mb-3">New webhook</h2>
                        <div className="flex gap-2">
                            <input value={name} onChange={e => setName(e.target.value)} placeholder="Source name (e.g. Splunk)"
                                className="flex-1 bg-white border border-zinc-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-accent-500" />
                            <button disabled={!name.trim() || create.isPending} onClick={() => create.mutate(name.trim())}
                                className="px-3 py-2 rounded-lg bg-accent-600 text-white text-sm font-semibold hover:bg-accent-700 disabled:opacity-50 flex items-center gap-1.5"><Plus size={16} />Create</button>
                        </div>
                    </div>

                    <div className="glass-panel rounded-xl border border-zinc-200 overflow-hidden">
                        <table className="w-full text-sm text-left">
                            <thead className="bg-white border-b border-zinc-200 text-xs uppercase text-zinc-500 font-semibold tracking-wider">
                                <tr><th className="px-4 py-3">Name</th><th className="px-4 py-3">API Key</th><th className="px-4 py-3 w-32">Created</th><th className="px-4 py-3 w-20"></th></tr>
                            </thead>
                            <tbody className="divide-y divide-zinc-200">
                                {(webhooks || []).length === 0 ? (
                                    <tr><td colSpan={4} className="px-6 py-10 text-center text-zinc-400">No webhooks yet</td></tr>
                                ) : (webhooks ?? []).map(wh => (
                                    <tr key={wh.id} className="hover:bg-zinc-100">
                                        <td className="px-4 py-2.5 font-medium text-zinc-800">{wh.name}</td>
                                        <td className="px-4 py-2.5">
                                            <button onClick={() => copy(wh)} className="font-mono text-xs text-zinc-600 hover:text-accent-600 flex items-center gap-1.5">
                                                <span className="truncate max-w-[16rem]">{wh.api_key}</span>
                                                {copied === wh.id ? <Check size={12} className="text-emerald-600" /> : <Copy size={12} />}
                                            </button>
                                        </td>
                                        <td className="px-4 py-2.5 text-zinc-500 text-xs">{new Date(wh.created_at).toLocaleDateString()}</td>
                                        <td className="px-4 py-2.5">
                                            <button onClick={() => { if (confirm(`Revoke "${wh.name}"? Its key stops working.`)) revoke.mutate(wh.id); }}
                                                className="text-zinc-400 hover:text-red-600"><Trash2 size={15} /></button>
                                        </td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>

                    <div className="glass-panel p-5 rounded-xl border border-zinc-200">
                        <h2 className="font-semibold text-zinc-800 mb-2">Pushing alerts</h2>
                        <p className="text-xs text-zinc-500 mb-2">POST to the endpoint below with your webhook's key:</p>
                        <pre className="text-xs text-zinc-600 bg-zinc-50 rounded-lg p-3 overflow-x-auto">{`curl -X POST ${ingestUrl} \\
  -H "X-API-Key: <your webhook key>" \\
  -H "Content-Type: application/json" \\
  -d '{"external_id":"siem-123","title":"Suspicious login","payload":{"ip":"1.2.3.4"}}'`}</pre>
                    </div>
                </div>
            )}
        </div>
    );
}
