import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '../../api/client';
import type { User } from '../../types';
import { Plus, RefreshCw, Trash2, Copy, Check } from 'lucide-react';
import HttpAllowlistCard from './HttpAllowlistCard';
import SlackCard from './SlackCard';
import AIProviderCard from './AIProviderCard';
import ThreatIntelCard from './ThreatIntelCard';
import SecretsCard from './SecretsCard';
import TicketingCard from './TicketingCard';
import PageContainer from '../../components/layout/PageContainer';

interface Webhook { id: number; name: string; key_prefix: string; created_at: string; needs_rotation?: boolean; }
// Only the create response carries the full key; the server keeps just its hash.
interface NewWebhook extends Webhook { api_key: string; }

export default function Integrations() {
    const qc = useQueryClient();
    const [name, setName] = useState('');
    const [copied, setCopied] = useState(false);
    const [created, setCreated] = useState<NewWebhook | null>(null);

    const { data: me } = useQuery({ queryKey: ['currentUser'], queryFn: async () => (await api.get('/users/me')).data as User });
    const isAdmin = me?.role === 'admin' || me?.is_super_admin;

    const { data: webhooks } = useQuery<Webhook[]>({
        queryKey: ['webhooks'],
        queryFn: async () => (await api.get('/integrations/webhooks')).data,
        enabled: !!isAdmin,
    });

    const create = useMutation({
        mutationFn: async (n: string) => (await api.post('/integrations/webhooks', { name: n })).data as NewWebhook,
        onSuccess: (wh) => { setName(''); setCopied(false); setCreated(wh); qc.invalidateQueries({ queryKey: ['webhooks'] }); },
    });
    const rotate = useMutation({
        mutationFn: async (id: number) => (await api.post(`/integrations/webhooks/${id}/rotate`)).data as NewWebhook,
        onSuccess: (wh) => { setCopied(false); setCreated(wh); qc.invalidateQueries({ queryKey: ['webhooks'] }); },
    });
    const revoke = useMutation({
        mutationFn: async (id: number) => api.delete(`/integrations/webhooks/${id}`),
        onSuccess: () => qc.invalidateQueries({ queryKey: ['webhooks'] }),
    });

    const copy = async (key: string) => {
        try {
            await navigator.clipboard.writeText(key);
            setCopied(true);
        } catch {
            // clipboard unavailable (e.g. non-secure context) — don't show a false "copied"
        }
    };
    const ingestUrl = `${window.location.origin}/api/v1/alerts/webhook`;

    return (
        <PageContainer>
            <div>
                <h1 className="text-3xl font-bold text-fg tracking-tight">Integrations</h1>
                <p className="text-fg-3 mt-1">Create webhooks so tools like a SIEM can push alerts into a tenant.</p>
            </div>

            {!isAdmin ? (
                <div className="glass-panel p-8 rounded-xl text-center text-fg-3">Ask a tenant admin to manage webhooks.</div>
            ) : (
                <div className="space-y-6">
                    <div className="glass-panel p-5 rounded-xl border border-line">
                        <h2 className="font-semibold text-fg mb-3">New webhook</h2>
                        <div className="flex gap-2">
                            <input value={name} onChange={e => setName(e.target.value)} placeholder="Source name (e.g. Splunk)"
                                className="flex-1 bg-panel border border-line rounded-lg px-3 py-2 text-sm focus:outline-hidden focus:ring-1 focus:ring-accent" />
                            <button disabled={!name.trim() || create.isPending} onClick={() => create.mutate(name.trim())}
                                className="px-3 py-2 rounded-lg bg-accent text-on-accent text-sm font-semibold hover:bg-accent-hover disabled:opacity-50 flex items-center gap-1.5"><Plus size={16} />Create</button>
                        </div>
                        {created && (
                            <div className="mt-3 p-3 rounded-lg border border-accent/40 bg-surface space-y-2">
                                <p className="text-xs text-fg-2">Key for <span className="font-medium text-fg">{created.name}</span>. Copy it now: it won't be shown again.</p>
                                <div className="flex items-center gap-2">
                                    <code className="flex-1 font-mono text-xs text-fg break-all">{created.api_key}</code>
                                    <button onClick={() => copy(created.api_key)} className="text-fg-2 hover:text-accent" aria-label="Copy key">
                                        {copied ? <Check size={14} className="text-emerald-400" /> : <Copy size={14} />}
                                    </button>
                                    <button onClick={() => setCreated(null)} className="text-xs text-fg-3 hover:text-fg">Done</button>
                                </div>
                            </div>
                        )}
                    </div>

                    <div className="glass-panel rounded-xl border border-line overflow-hidden">
                        <table className="w-full text-sm text-left">
                            <thead className="bg-panel border-b border-line text-xs uppercase text-fg-3 font-semibold tracking-wider">
                                <tr><th className="px-4 py-3">Name</th><th className="px-4 py-3">API Key</th><th className="px-4 py-3 w-32">Created</th><th className="px-4 py-3 w-24"></th></tr>
                            </thead>
                            <tbody className="divide-y divide-line">
                                {(webhooks || []).length === 0 ? (
                                    <tr><td colSpan={4} className="px-6 py-10 text-center text-muted">No webhooks yet</td></tr>
                                ) : (webhooks ?? []).map(wh => (
                                    <tr key={wh.id} className="hover:bg-raised">
                                        <td className="px-4 py-2.5 font-medium text-fg">{wh.name}</td>
                                        <td className="px-4 py-2.5">
                                            <span className="font-mono text-xs text-fg-2">{wh.key_prefix}…</span>
                                            {wh.needs_rotation && (
                                                <span className="ml-2 text-[11px] text-amber-300" title="This key predates the current key hashing and no longer authenticates. Rotate it and update the sending tool.">
                                                    needs rotation
                                                </span>
                                            )}
                                        </td>
                                        <td className="px-4 py-2.5 text-fg-3 text-xs">{new Date(wh.created_at).toLocaleDateString()}</td>
                                        <td className="px-4 py-2.5">
                                            <span className="flex items-center gap-2.5">
                                                <button onClick={() => { if (confirm(`Issue a new key for "${wh.name}"? The current key stops working.`)) rotate.mutate(wh.id); }}
                                                    disabled={rotate.isPending} aria-label={`Rotate key for ${wh.name}`} title="Rotate key"
                                                    className={wh.needs_rotation ? 'text-amber-300 hover:text-amber-200' : 'text-muted hover:text-fg'}><RefreshCw size={15} /></button>
                                                <button onClick={() => { if (confirm(`Revoke "${wh.name}"? Its key stops working.`)) revoke.mutate(wh.id); }}
                                                    aria-label={`Revoke ${wh.name}`} className="text-muted hover:text-red-400"><Trash2 size={15} /></button>
                                            </span>
                                        </td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>

                    <div className="glass-panel p-5 rounded-xl border border-line">
                        <h2 className="font-semibold text-fg mb-2">Pushing alerts</h2>
                        <p className="text-xs text-fg-3 mb-2">POST to the endpoint below with your webhook's key:</p>
                        <pre className="text-xs text-fg-2 bg-surface rounded-lg p-3 overflow-x-auto">{`curl -X POST ${ingestUrl} \\
  -H "X-API-Key: <your webhook key>" \\
  -H "Content-Type: application/json" \\
  -d '{"external_id":"siem-123","title":"Suspicious login","payload":{"ip":"1.2.3.4"}}'`}</pre>
                    </div>

                    {/* key: a tenant switch remounts the card so an unsaved draft never leaks across tenants */}
                    <AIProviderCard key={me?.active_tenant_id ?? 'none'} tenantId={me?.active_tenant_id ?? null} />
                    <ThreatIntelCard key={me?.active_tenant_id ?? 'none'} tenantId={me?.active_tenant_id ?? null} />
                    <SecretsCard key={me?.active_tenant_id ?? 'none'} tenantId={me?.active_tenant_id ?? null} />

                    <SlackCard />
                    <TicketingCard key={`t${me?.active_tenant_id ?? 'none'}`} tenantId={me?.active_tenant_id ?? null} />

                    <HttpAllowlistCard />
                </div>
            )}
        </PageContainer>
    );
}
