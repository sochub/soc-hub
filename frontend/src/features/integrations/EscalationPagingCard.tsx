import { useState } from 'react';
import { Copy, Check, BellRing } from 'lucide-react';
import { Button, Input } from '../../components/ui';
import { errMsg, useEscalationIntegrations, useOpsgenieToken, useSaveEscalationIntegrations } from '../escalation/api';

function CopyField({ label, value }: { label: string; value: string }) {
    const [copied, setCopied] = useState(false);
    const copy = async () => {
        try { await navigator.clipboard.writeText(value); setCopied(true); } catch { /* clipboard unavailable */ }
    };
    return (
        <div className="space-y-1">
            <div className="text-xs text-fg-2">{label}</div>
            <div className="flex items-center gap-2">
                <code className="flex-1 min-w-0 truncate bg-canvas border border-line rounded-lg px-3 py-2 text-xs font-mono text-fg">{value}</code>
                <Button variant="secondary" size="sm" aria-label={`Copy ${label}`} onClick={copy}>{copied ? <Check size={14} /> : <Copy size={14} />}</Button>
            </div>
        </div>
    );
}

export default function EscalationPagingCard() {
    const { data } = useEscalationIntegrations();
    const save = useSaveEscalationIntegrations();
    const gen = useOpsgenieToken();
    const [secret, setSecret] = useState('');
    const [apiKey, setApiKey] = useState('');
    const [token, setToken] = useState<string | null>(null);
    const [tokenCopied, setTokenCopied] = useState(false);
    const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);

    const submit = () => {
        const body: { pagerduty_webhook_secret?: string; pagerduty_api_key?: string } = {};
        if (secret) body.pagerduty_webhook_secret = secret;
        if (apiKey) body.pagerduty_api_key = apiKey;
        save.mutate(body, {
            onSuccess: () => { setSecret(''); setApiKey(''); setMsg({ ok: true, text: 'Saved.' }); },
            onError: (e) => setMsg({ ok: false, text: errMsg(e, 'Save failed') }),
        });
    };
    const clear = (field: 'pagerduty_webhook_secret' | 'pagerduty_api_key') =>
        save.mutate({ [field]: '' }, {
            onSuccess: () => setMsg({ ok: true, text: 'Cleared.' }),
            onError: (e) => setMsg({ ok: false, text: errMsg(e, 'Save failed') }),
        });
    const generate = () => gen.mutate(undefined, {
        onSuccess: (r) => { setToken(r.token); setTokenCopied(false); setMsg(null); },
        onError: (e) => setMsg({ ok: false, text: errMsg(e, 'Could not generate a token') }),
    });
    const copyToken = async () => {
        if (!token) return;
        try { await navigator.clipboard.writeText(token); setTokenCopied(true); } catch { /* clipboard unavailable */ }
    };

    return (
        <div className="bg-panel border border-line p-5 space-y-4">
            <div className="flex items-center gap-2">
                <BellRing size={16} className="text-accent" />
                <h2 className="font-semibold text-fg">Escalation paging</h2>
            </div>
            <p className="text-xs text-fg-3">Point PagerDuty or Opsgenie at these URLs so acknowledging a page accepts the escalated case.</p>
            {data && (<>
                <CopyField label="PagerDuty webhook URL" value={data.pagerduty_url} />
                <CopyField label="Opsgenie webhook URL" value={data.opsgenie_url} />
            </>)}

            <div className="grid gap-3 md:grid-cols-2">
                <div className="space-y-1">
                    <label htmlFor="pd-secret" className="text-xs text-fg-2">PagerDuty signing secret {data?.has_pagerduty_webhook_secret && <span className="text-emerald-300">(set)</span>}</label>
                    <div className="flex gap-2">
                        <Input id="pd-secret" type="password" autoComplete="off" value={secret} onChange={(e) => setSecret(e.target.value)}
                            placeholder={data?.has_pagerduty_webhook_secret ? 'Enter a new value to replace' : ''} />
                        {data?.has_pagerduty_webhook_secret && <Button variant="ghost" size="sm" aria-label="Clear PagerDuty signing secret" onClick={() => clear('pagerduty_webhook_secret')}>Clear</Button>}
                    </div>
                </div>
                <div className="space-y-1">
                    <label htmlFor="pd-key" className="text-xs text-fg-2">PagerDuty API key {data?.has_pagerduty_api_key && <span className="text-emerald-300">(set)</span>}</label>
                    <div className="flex gap-2">
                        <Input id="pd-key" type="password" autoComplete="off" value={apiKey} onChange={(e) => setApiKey(e.target.value)}
                            placeholder={data?.has_pagerduty_api_key ? 'Enter a new value to replace' : ''} />
                        {data?.has_pagerduty_api_key && <Button variant="ghost" size="sm" aria-label="Clear PagerDuty API key" onClick={() => clear('pagerduty_api_key')}>Clear</Button>}
                    </div>
                </div>
            </div>
            <Button variant="secondary" size="sm" disabled={(!secret && !apiKey) || save.isPending} onClick={submit}>Save</Button>

            <div className="space-y-2 border-t border-line pt-4">
                <div className="text-xs text-fg-2">Opsgenie token {data?.has_opsgenie_token && <span className="text-emerald-300">(set)</span>}</div>
                <Button variant="secondary" size="sm" disabled={gen.isPending} onClick={generate}>
                    {data?.has_opsgenie_token ? 'Regenerate token' : 'Generate token'}
                </Button>
                {token && (
                    <div className="p-3 rounded-lg border border-accent/40 bg-surface space-y-2">
                        <p className="text-xs text-fg-2">Copy it now: it won't be shown again. Any previous token stops working.</p>
                        <div className="flex items-center gap-2">
                            <code className="flex-1 min-w-0 truncate font-mono text-xs text-fg">{token}</code>
                            <Button variant="secondary" size="sm" aria-label="Copy token" onClick={copyToken}>{tokenCopied ? <Check size={14} /> : <Copy size={14} />}</Button>
                            <Button variant="ghost" size="sm" onClick={() => setToken(null)}>Done</Button>
                        </div>
                    </div>
                )}
            </div>
            {msg && <p role={msg.ok ? 'status' : 'alert'} className={`text-xs ${msg.ok ? 'text-emerald-300' : 'text-severity-critical'}`}>{msg.text}</p>}
        </div>
    );
}
