import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Ticket } from 'lucide-react';
import { isAxiosError } from 'axios';
import { api } from '../../api/client';

type Provider = 'jira' | 'halopsa' | 'connectwise';
interface Connection {
    provider: Provider; base_url: string; settings: Record<string, string>; enabled: boolean;
    auto_create_min_severity: string | null; push_notes: boolean; has_credentials: boolean;
    last_synced_at: string | null; last_error: string | null;
}
type Field = { key: string; label: string; secret?: boolean; optional?: boolean; hint?: string };

const LABEL: Record<Provider, string> = { jira: 'Jira', halopsa: 'HaloPSA', connectwise: 'ConnectWise Manage' };
const URL_HINT: Record<Provider, string> = {
    jira: 'https://your-site.atlassian.net (or your Data Center URL)',
    halopsa: 'https://your-instance.halopsa.com',
    connectwise: 'https://na.myconnectwise.net/v4_6_release/apis/3.0',
};
const SETTINGS: Record<Provider, Field[]> = {
    jira: [{ key: 'project_key', label: 'Project key', hint: 'e.g. SEC' }, { key: 'issue_type', label: 'Issue type', optional: true, hint: 'Task' }],
    halopsa: [
        { key: 'tickettype_id', label: 'Ticket type ID', optional: true }, { key: 'client_id', label: 'Client ID', optional: true },
        { key: 'tenant', label: 'Halo tenant', optional: true, hint: 'hosted Halo only' },
    ],
    connectwise: [{ key: 'board_id', label: 'Service board ID' }, { key: 'company_identifier', label: 'Company identifier', hint: 'the client company' }],
};
const CREDS: Record<Provider, Field[]> = {
    jira: [{ key: 'email', label: 'Account email', hint: 'Cloud only' }, { key: 'token', label: 'API token (Cloud) or personal access token (DC)', secret: true }],
    halopsa: [{ key: 'client_id', label: 'API client ID' }, { key: 'client_secret', label: 'API client secret', secret: true }],
    connectwise: [
        { key: 'company_id', label: 'Company ID (login)' }, { key: 'public_key', label: 'Public key' },
        { key: 'private_key', label: 'Private key', secret: true }, { key: 'client_id', label: 'Client ID', hint: 'from developer.connectwise.com' },
    ],
};
const errText = (e: unknown, fb: string): string => {
    const d: unknown = isAxiosError(e) ? e.response?.data?.detail : undefined;
    if (typeof d === 'string') return d;
    if (Array.isArray(d) && typeof d[0]?.msg === 'string') return d[0].msg;
    return fb;
};
const input = 'w-full border border-line-strong px-2 py-1.5 text-sm';

/** Admin card: one ticketing/PSA connection per tenant. Credentials are write-only. */
export default function TicketingCard({ tenantId }: { tenantId: number | null }) {
    const key = ['ticketing', tenantId];
    const qc = useQueryClient();
    const { data, isLoading } = useQuery({ queryKey: key, queryFn: async () => (await api.get('/ticketing/connection')).data as Connection | null });
    const [draft, setDraft] = useState<{ provider: Provider; base_url: string; settings: Record<string, string>; creds: Record<string, string>;
        auto: string; push_notes: boolean; enabled: boolean; deployment: string } | null>(null);
    const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);

    const form = draft ?? {
        provider: data?.provider ?? 'jira', base_url: data?.base_url ?? '', settings: { ...(data?.settings ?? {}) }, creds: {},
        auto: data?.auto_create_min_severity ?? '', push_notes: data?.push_notes ?? false, enabled: data?.enabled ?? true,
        deployment: data?.settings?.deployment ?? 'cloud',
    };
    const set = (patch: Partial<typeof form>) => { setDraft({ ...form, ...patch }); setMsg(null); };
    const sameProvider = !!data && data.provider === form.provider;
    const credFields = CREDS[form.provider].filter((f) => !(form.provider === 'jira' && form.deployment === 'datacenter' && f.key === 'email'));

    const save = useMutation({
        mutationFn: async () => {
            const anyCred = Object.values(form.creds).some((v) => v.trim());
            const settings = Object.fromEntries(Object.entries({ ...form.settings, ...(form.provider === 'jira' ? { deployment: form.deployment } : {}) })
                .filter(([k, v]) => v !== '' && v != null && (SETTINGS[form.provider].some((f) => f.key === k) || k === 'deployment')));
            return (await api.put('/ticketing/connection', {
                provider: form.provider, base_url: form.base_url, settings, enabled: form.enabled,
                auto_create_min_severity: form.auto || null, push_notes: form.push_notes,
                credentials: anyCred || !sameProvider ? form.creds : null,
            })).data as Connection;
        },
        onSuccess: (r) => { qc.setQueryData(key, r); setDraft(null); setMsg({ ok: true, text: 'Saved' }); },
        onError: (e) => setMsg({ ok: false, text: errText(e, 'Save failed') }),
    });
    const test = useMutation({
        mutationFn: async () => (await api.post('/ticketing/connection/test')).data as { ok: boolean; error?: string },
        onSuccess: (r) => setMsg({ ok: r.ok, text: r.ok ? 'Connected' : r.error ?? 'Failed' }),
        onError: (e) => setMsg({ ok: false, text: errText(e, 'Test failed') }),
    });
    const remove = useMutation({
        mutationFn: async () => api.delete('/ticketing/connection'),
        onSuccess: () => { qc.setQueryData(key, null); setDraft(null); setMsg({ ok: true, text: 'Removed. Linked tickets stay on their cases but stop syncing.' }); },
    });

    if (isLoading) return null;
    return (
        <div className="bg-panel border border-line p-5 space-y-3">
            <div className="flex items-center gap-2">
                <Ticket size={16} className="text-accent" />
                <h2 className="font-semibold text-fg">Ticketing / PSA</h2>
                <span className="ml-auto label-mono">{data ? `${LABEL[data.provider]}${data.enabled ? '' : ' · disabled'}` : 'not connected'}</span>
            </div>
            <p className="text-xs text-fg-3">
                Open a ticket for a case and keep both sides in step: every 5 minutes SOC Hub pulls the ticket's status and comments into
                the case activity, and posts a comment when the case is resolved. A closed ticket never closes the case.
            </p>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                <label className="text-xs text-fg-2 space-y-1">Provider
                    <select value={form.provider} onChange={(e) => set({ provider: e.target.value as Provider, settings: {}, creds: {} })} className={input}>
                        {(Object.keys(LABEL) as Provider[]).map((p) => <option key={p} value={p}>{LABEL[p]}</option>)}
                    </select>
                </label>
                {form.provider === 'jira' && (
                    <label className="text-xs text-fg-2 space-y-1">Deployment
                        <select value={form.deployment} onChange={(e) => set({ deployment: e.target.value })} className={input}>
                            <option value="cloud">Cloud</option><option value="datacenter">Data Center / Server</option>
                        </select>
                    </label>
                )}
                <label className="text-xs text-fg-2 space-y-1 sm:col-span-2">Base URL
                    <input value={form.base_url} onChange={(e) => set({ base_url: e.target.value })} placeholder={URL_HINT[form.provider]} className={input} />
                </label>
                {SETTINGS[form.provider].map((f) => (
                    <label key={f.key} className="text-xs text-fg-2 space-y-1">{f.label}{f.optional && ' (optional)'}
                        <input value={form.settings[f.key] ?? ''} placeholder={f.hint} onChange={(e) => set({ settings: { ...form.settings, [f.key]: e.target.value } })} className={input} />
                    </label>
                ))}
                {credFields.map((f) => (
                    <label key={f.key} className="text-xs text-fg-2 space-y-1">{f.label}
                        <input type={f.secret ? 'password' : 'text'} autoComplete="off" value={form.creds[f.key] ?? ''}
                            placeholder={sameProvider && data?.has_credentials ? '•••••• (saved; leave blank to keep)' : f.hint}
                            onChange={(e) => set({ creds: { ...form.creds, [f.key]: e.target.value } })} className={input} />
                    </label>
                ))}
                <label className="text-xs text-fg-2 space-y-1">Create tickets automatically for
                    <select value={form.auto} onChange={(e) => set({ auto: e.target.value })} className={input}>
                        <option value="">No case (create by hand)</option>
                        <option value="critical">Critical cases</option>
                        <option value="high">High and critical cases</option>
                        <option value="medium">Medium and above</option>
                        <option value="low">Low and above</option>
                        <option value="info">Every case</option>
                    </select>
                    <span className="block text-muted">Only cases created after you turn this on.</span>
                </label>
                <div className="text-xs text-fg-2 space-y-2 pt-4">
                    <label className="flex items-center gap-2"><input type="checkbox" checked={form.push_notes} onChange={(e) => set({ push_notes: e.target.checked })} />
                        Post analyst notes to the ticket</label>
                    <span className="block text-muted">As internal notes in HaloPSA and ConnectWise; as normal comments in Jira. Off by default: clients may see PSA tickets.</span>
                    <label className="flex items-center gap-2"><input type="checkbox" checked={form.enabled} onChange={(e) => set({ enabled: e.target.checked })} /> Sync enabled</label>
                </div>
            </div>
            {data?.last_error && <p className="text-xs text-severity-high">Last sync: {data.last_error}</p>}
            {data?.last_synced_at && !data.last_error && <p className="text-xs text-muted">Last synced {new Date(data.last_synced_at).toLocaleString()}</p>}
            {msg && <p role={msg.ok ? 'status' : 'alert'} className={msg.ok ? 'text-xs text-emerald-300' : 'text-xs text-red-300'}>{msg.text}</p>}
            <div className="flex gap-2">
                <button onClick={() => save.mutate()} disabled={save.isPending || !form.base_url} className="px-3 py-1.5 text-sm bg-accent text-on-accent hover:bg-accent-hover disabled:opacity-50">Save</button>
                <button onClick={() => test.mutate()} disabled={!data || !!draft || test.isPending} title={draft ? 'Save first' : undefined}
                    className="px-3 py-1.5 text-sm border border-line-strong text-fg-2 hover:bg-raised disabled:opacity-50">Test</button>
                {data && <button onClick={() => { if (confirm('Remove the ticketing connection?')) remove.mutate(); }}
                    className="ml-auto px-3 py-1.5 text-sm text-severity-critical hover:bg-raised">Remove</button>}
            </div>
        </div>
    );
}
