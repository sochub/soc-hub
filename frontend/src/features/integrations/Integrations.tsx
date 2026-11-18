import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '../../api/client';
import { Plus, RefreshCw, Trash2, Copy, Check, ChevronRight } from 'lucide-react';
import HttpAllowlistCard from './HttpAllowlistCard';
import SlackCard from './SlackCard';
import AIProviderCard from './AIProviderCard';
import ThreatIntelCard from './ThreatIntelCard';
import MailboxCard from './MailboxCard';
import SecretsCard from './SecretsCard';
import TicketingCard from './TicketingCard';
import ContainmentCard from './ContainmentCard';
import EscalationPagingCard from './EscalationPagingCard';
import type { EscalationIntegrations, User } from '../../types';
import { Button, Card, CardContent, CardHeader, CardTitle, EmptyState, Input, Stamp, useConfirm } from '../../components/ui';
import SettingsPageHeader from '../settings/SettingsPageHeader';

interface Webhook { id: number; name: string; key_prefix: string; created_at: string; needs_rotation?: boolean; }
// Only the create response carries the full key; the server keeps just its hash.
interface NewWebhook extends Webhook { api_key: string; }

type Row = { id: string; label: string; description: string; on: boolean | undefined; onLabel?: string; offLabel?: string };

/**
 * Connection state for the index. Same query keys and endpoints as the cards below, so React Query
 * shares one request per integration; the cards stay the source of truth for editing.
 */
function useIntegrationIndex(tenantId: number | null, enabled: boolean, webhookCount: number | undefined): Row[] {
    const get = <T,>(key: unknown[], url: string) => ({ queryKey: key, queryFn: async () => (await api.get(url)).data as T, enabled });
    const ai = useQuery(get<{ source: 'tenant' | 'deployment'; enabled: boolean }>(['ai-config', tenantId], '/ai/config'));
    const ti = useQuery(get<{ sources: Record<string, boolean> }>(['ti-config', tenantId], '/enrichment/config'));
    const secrets = useQuery(get<unknown[]>(['secrets', tenantId], '/secrets/'));
    const mail = useQuery(get<unknown[]>(['mailboxes', tenantId], '/mailboxes/'));
    const slack = useQuery(get<{ configured: boolean; team_id: string | null }>(['slack-config'], '/slack/config'));
    const ticket = useQuery(get<{ enabled: boolean } | null>(['ticketing', tenantId], '/ticketing/connection'));
    const contain = useQuery(get<{ enabled: boolean }>(['containment-policy', tenantId], '/containment/policy'));
    const allow = useQuery(get<{ hosts: string[] }>(['http-allowlist'], '/workflows/settings/http-allowlist'));
    const esc = useQuery(get<EscalationIntegrations>(['escalation-integrations'], '/escalations/integrations'));
    const count = (n: number | undefined, noun: string) => (n == null ? undefined : `${n} ${noun}${n === 1 ? '' : 's'}`);
    return [
        { id: 'webhooks', label: 'Alert webhooks', description: 'Let your SIEM and other tools push alerts in.', on: webhookCount == null ? undefined : webhookCount > 0, onLabel: count(webhookCount, 'webhook') },
        { id: 'ai', label: 'AI provider', description: 'Model used by the copilot and triage.', on: ai.data ? ai.data.source === 'tenant' && ai.data.enabled : undefined, offLabel: 'Deployment default' },
        { id: 'threat-intel', label: 'Threat intelligence', description: 'Enrich IOCs and artifacts from external sources.', on: ti.data ? Object.values(ti.data.sources ?? {}).some(Boolean) : undefined },
        { id: 'secrets', label: 'Secrets', description: 'Credentials workflows can send to approved hosts.', on: secrets.data ? secrets.data.length > 0 : undefined, onLabel: count(secrets.data?.length, 'secret'), offLabel: 'None' },
        { id: 'mailboxes', label: 'Mailboxes', description: 'Turn incoming email into workflow triggers.', on: mail.data ? mail.data.length > 0 : undefined, onLabel: count(mail.data?.length, 'mailbox') },
        { id: 'slack', label: 'Slack', description: 'War rooms and alert notifications.', on: slack.data ? !!slack.data.team_id : undefined, offLabel: slack.data?.configured ? 'Not tested' : undefined },
        { id: 'ticketing', label: 'Ticketing / PSA', description: 'Open and sync tickets from cases.', on: ticket.isSuccess ? !!ticket.data?.enabled : undefined },
        { id: 'containment', label: 'Containment guardrails', description: 'Limits on automated containment actions.', on: contain.data?.enabled, onLabel: 'On', offLabel: 'Off' },
        { id: 'paging', label: 'Escalation paging', description: 'PagerDuty and Opsgenie acknowledgements.', on: esc.data ? !!(esc.data.has_pagerduty_api_key || esc.data.has_pagerduty_webhook_secret || esc.data.has_opsgenie_token) : undefined },
        { id: 'http-allowlist', label: 'Automation HTTP allowlist', description: 'Internal hosts workflows may call.', on: allow.data ? allow.data.hosts.length > 0 : undefined, onLabel: count(allow.data?.hosts.length, 'host'), offLabel: 'None' },
    ];
}

export default function Integrations() {
    const qc = useQueryClient();
    const { confirm, dialog } = useConfirm();
    const [name, setName] = useState('');
    const [copied, setCopied] = useState(false);
    const [created, setCreated] = useState<NewWebhook | null>(null);

    const { data: me } = useQuery({ queryKey: ['currentUser'], queryFn: async () => (await api.get('/users/me')).data as User });
    const isAdmin = me?.role === 'admin' || me?.is_super_admin;
    const tenantId = me?.active_tenant_id ?? null;

    const { data: webhooks } = useQuery<Webhook[]>({
        queryKey: ['webhooks'],
        queryFn: async () => (await api.get('/integrations/webhooks')).data,
        enabled: !!isAdmin,
    });
    const index = useIntegrationIndex(tenantId, !!isAdmin, webhooks?.length);

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
    const section = (id: string, node: React.ReactNode) => <div id={`int-${id}`} className="scroll-mt-4">{node}</div>;

    return (
        <div className="space-y-6">
            {dialog}
            <SettingsPageHeader title="Integrations" description="Connect the tools this organization already uses." />

            {!isAdmin ? (
                <Card><EmptyState title="Managed by your admin" body="Ask an organization admin to manage webhooks and integrations." /></Card>
            ) : (
                <>
                    <Card>
                        <ul className="divide-y divide-line">
                            {index.map((r) => (
                                <li key={r.id}>
                                    <a href={`#int-${r.id}`} className="group flex items-center gap-3 px-4 py-2.5 hover:bg-raised">
                                        <span className="min-w-0 flex-1">
                                            <span className="block text-sm font-medium text-fg">{r.label}</span>
                                            <span className="block text-xs text-fg-3 truncate">{r.description}</span>
                                        </span>
                                        {r.on === undefined ? <span className="text-xs text-fg-3">…</span>
                                            : <Stamp tone={r.on ? 'ok' : 'neutral'}>{r.on ? (r.onLabel ?? 'Connected') : (r.offLabel ?? 'Not connected')}</Stamp>}
                                        <ChevronRight size={14} className="text-fg-3 group-hover:text-fg" aria-hidden />
                                    </a>
                                </li>
                            ))}
                        </ul>
                    </Card>

                    {section('webhooks', (
                        <Card>
                            <CardHeader><CardTitle>Alert webhooks</CardTitle></CardHeader>
                            <CardContent className="space-y-4">
                                <div>
                                    <label htmlFor="webhook-name" className="block text-xs font-medium text-fg-2 mb-1.5">New webhook</label>
                                    <div className="flex gap-2">
                                        <Input id="webhook-name" value={name} onChange={e => setName(e.target.value)} placeholder="Source name (e.g. Splunk)" className="flex-1" />
                                        <Button disabled={!name.trim() || create.isPending} onClick={() => create.mutate(name.trim())}><Plus size={16} />Create</Button>
                                    </div>
                                </div>
                                {created && (
                                    <div className="p-3 rounded-md border border-accent/40 bg-surface space-y-2">
                                        <p className="text-xs text-fg-2">Key for <span className="font-medium text-fg">{created.name}</span>. Copy it now: it won't be shown again.</p>
                                        <div className="flex items-center gap-2">
                                            <code className="flex-1 font-mono text-xs text-fg break-all">{created.api_key}</code>
                                            <Button variant="ghost" size="icon" onClick={() => copy(created.api_key)} aria-label="Copy key">
                                                {copied ? <Check size={14} className="text-lime" /> : <Copy size={14} />}
                                            </Button>
                                            <Button variant="ghost" size="sm" onClick={() => setCreated(null)}>Done</Button>
                                        </div>
                                    </div>
                                )}
                                <div className="overflow-x-auto rounded-md border border-line">
                                    <table className="w-full text-sm text-left">
                                        <thead className="border-b border-line">
                                            <tr className="label-caps">
                                                <th className="px-3 py-2">Name</th><th className="px-3 py-2">API key</th><th className="px-3 py-2 w-32">Created</th>
                                                <th className="px-3 py-2 w-24"><span className="sr-only">Actions</span></th>
                                            </tr>
                                        </thead>
                                        <tbody className="divide-y divide-line">
                                            {(webhooks || []).length === 0 ? (
                                                <tr><td colSpan={4} className="px-3 py-8 text-center text-fg-3">No webhooks yet</td></tr>
                                            ) : (webhooks ?? []).map(wh => (
                                                <tr key={wh.id} className="hover:bg-raised">
                                                    <td className="px-3 py-2.5 font-medium text-fg">{wh.name}</td>
                                                    <td className="px-3 py-2.5">
                                                        <span className="font-mono text-xs text-fg-2">{wh.key_prefix}…</span>
                                                        {wh.needs_rotation && (
                                                            <span className="ml-2 text-xs text-severity-medium" title="This key predates the current key hashing and no longer authenticates. Rotate it and update the sending tool.">
                                                                needs rotation
                                                            </span>
                                                        )}
                                                    </td>
                                                    <td className="px-3 py-2.5 text-fg-3 text-xs num">{new Date(wh.created_at).toLocaleDateString()}</td>
                                                    <td className="px-3 py-2.5">
                                                        <span className="flex items-center gap-1">
                                                            <Button variant="ghost" size="icon" className={wh.needs_rotation ? 'h-8 w-8 text-severity-medium' : 'h-8 w-8'}
                                                                disabled={rotate.isPending} aria-label={`Rotate key for ${wh.name}`} title="Rotate key"
                                                                onClick={async () => { if (await confirm({ title: `Issue a new key for "${wh.name}"?`, body: 'The current key stops working.', confirmLabel: 'Rotate key' })) rotate.mutate(wh.id); }}>
                                                                <RefreshCw size={15} />
                                                            </Button>
                                                            <Button variant="ghost" size="icon" className="h-8 w-8 hover:text-severity-critical" aria-label={`Revoke ${wh.name}`}
                                                                onClick={async () => { if (await confirm({ title: `Revoke "${wh.name}"?`, body: 'Its key stops working.', danger: true, confirmLabel: 'Revoke' })) revoke.mutate(wh.id); }}>
                                                                <Trash2 size={15} />
                                                            </Button>
                                                        </span>
                                                    </td>
                                                </tr>
                                            ))}
                                        </tbody>
                                    </table>
                                </div>
                                <div>
                                    <h4 className="text-xs font-semibold text-fg-2 mb-1">Pushing alerts</h4>
                                    <p className="text-xs text-fg-3 mb-2">POST to the endpoint below with your webhook's key:</p>
                                    <pre className="text-xs text-fg-2 bg-surface border border-line rounded-md p-3 overflow-x-auto">{`curl -X POST ${ingestUrl} \\
  -H "X-API-Key: <your webhook key>" \\
  -H "Content-Type: application/json" \\
  -d '{"external_id":"siem-123","title":"Suspicious login","payload":{"ip":"1.2.3.4"}}'`}</pre>
                                </div>
                            </CardContent>
                        </Card>
                    ))}

                    {/* key: a tenant switch remounts the card so an unsaved draft never leaks across tenants */}
                    {section('ai', <AIProviderCard key={tenantId ?? 'none'} tenantId={tenantId} />)}
                    {section('threat-intel', <ThreatIntelCard key={tenantId ?? 'none'} tenantId={tenantId} />)}
                    {section('secrets', <SecretsCard key={tenantId ?? 'none'} tenantId={tenantId} />)}
                    {section('mailboxes', <MailboxCard key={`m${tenantId ?? 'none'}`} tenantId={tenantId} />)}
                    {section('slack', <SlackCard />)}
                    {section('ticketing', <TicketingCard key={`t${tenantId ?? 'none'}`} tenantId={tenantId} />)}
                    {section('containment', <ContainmentCard key={`c${tenantId ?? 'none'}`} tenantId={tenantId} isAdmin={!!isAdmin} />)}
                    {section('paging', <EscalationPagingCard key={`e${tenantId ?? 'none'}`} />)}
                    {section('http-allowlist', <HttpAllowlistCard />)}
                </>
            )}
        </div>
    );
}
