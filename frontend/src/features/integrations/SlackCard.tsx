import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { MessageSquare } from 'lucide-react';
import { isAxiosError } from 'axios';
import { api } from '../../api/client';

interface SlackConfig {
    configured: boolean; team_id: string | null; default_channel: string | null;
    alert_channel: string | null; alert_min_severity: string | null;
    socket_mode: boolean; socket_connected: boolean;
}

const SEVERITIES = ['critical', 'high', 'medium', 'low', 'info'];

const detail = (e: unknown, fallback: string) => (isAxiosError(e) && typeof e.response?.data?.detail === 'string' ? e.response.data.detail : fallback);

export default function SlackCard() {
    const qc = useQueryClient();
    const { data } = useQuery({ queryKey: ['slack-config'], queryFn: async () => (await api.get('/slack/config')).data as SlackConfig });
    const [token, setToken] = useState('');
    const [secret, setSecret] = useState('');
    const [appToken, setAppToken] = useState('');
    const [channelEdit, setChannel] = useState<string | null>(null);
    const channel = channelEdit ?? data?.default_channel ?? '';
    const [alertEdit, setAlert] = useState<string | null>(null);
    const alertChannel = alertEdit ?? data?.alert_channel ?? '';
    const [minEdit, setMin] = useState<string | null>(null);
    const minSeverity = minEdit ?? data?.alert_min_severity ?? '';
    const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);

    const save = useMutation({
        mutationFn: async () => (await api.put('/slack/config', {
            bot_token: token || null, signing_secret: secret || null, app_token: appToken || null, default_channel: channel,
            alert_channel: alertChannel, alert_min_severity: minSeverity,
        })).data,
        onSuccess: () => { setToken(''); setSecret(''); setAppToken(''); setChannel(null); setAlert(null); setMin(null); setMsg({ ok: true, text: 'Saved — now click Test' }); qc.invalidateQueries({ queryKey: ['slack-config'] }); },
        onError: (e) => setMsg({ ok: false, text: detail(e, 'Save failed') }),
    });
    const test = useMutation({
        mutationFn: async () => (await api.post('/slack/config/test')).data as { team: string },
        onSuccess: (r) => { setMsg({ ok: true, text: `Connected to ${r.team}` }); qc.invalidateQueries({ queryKey: ['slack-config'] }); },
        onError: (e) => setMsg({ ok: false, text: detail(e, 'Test failed') }),
    });

    const input = 'w-full border border-line-strong px-2 py-1.5 text-sm font-mono';
    return (
        <div className="bg-panel border border-line p-5 space-y-3">
            <div className="flex items-center gap-2">
                <MessageSquare size={16} className="text-accent" />
                <h2 className="font-semibold text-fg">Slack</h2>
                <span className="ml-auto label-mono">{data?.team_id ? `connected · ${data.team_id}${data.socket_mode ? ' · socket mode' : ''}` : data?.configured ? 'saved · not tested' : 'not configured'}</span>
            </div>
            <div className="text-xs text-fg-3 space-y-1">
                <p>Create a Slack app from <code className="font-mono">docs/slack/slack-app-manifest.yml</code> (Socket Mode: no public URL needed), then paste the bot token and an app-level token with <code className="font-mono">connections:write</code>.</p>
                <p>In Slack: <code className="font-mono">/sochub new</code>, <code className="font-mono">/sochub case 42</code>, the message menu's “Create sochub case”, and the app's Home tab.</p>
            </div>
            <label className="block"><span className="label-mono">bot token</span>
                <input type="password" className={input} value={token} placeholder={data?.configured ? '•••••• (unchanged)' : 'xoxb-…'} onChange={(e) => setToken(e.target.value)} /></label>
            <label className="block"><span className="label-mono">app-level token (socket mode)</span>
                <input type="password" className={input} value={appToken} placeholder={data?.socket_mode ? '•••••• (unchanged) · type - to remove' : 'xapp-…'} onChange={(e) => setAppToken(e.target.value)} /></label>
            {data?.socket_mode && (
                <p className={data.socket_connected ? 'text-xs text-emerald-300' : 'text-xs text-amber-300'}>
                    {data.socket_connected ? '● socket connected' : '○ socket not connected yet (is the slack-socket service running?)'}
                </p>
            )}
            <details className="text-xs text-fg-3">
                <summary className="cursor-pointer">HTTP mode instead (signing secret + request URLs)</summary>
                <label className="block mt-2"><span className="label-mono">signing secret</span>
                    <input type="password" className={input} value={secret} placeholder={data?.configured ? '•••••• (unchanged)' : ''} onChange={(e) => setSecret(e.target.value)} /></label>
                <ul className="font-mono space-y-0.5 mt-2">
                    {['interactions', 'commands', 'events'].map((p) => <li key={p}>{window.location.origin}/api/v1/slack/{p}</li>)}
                </ul>
            </details>
            <label className="block"><span className="label-mono">default channel</span>
                <input className={input} value={channel} placeholder="#soc-alerts" onChange={(e) => setChannel(e.target.value)} /></label>
            <div className="grid gap-3 sm:grid-cols-2 border-t border-line pt-3">
                <label className="block"><span className="label-mono">responder alerts channel</span>
                    <input className={input} value={alertChannel} placeholder="defaults to the channel above" onChange={(e) => setAlert(e.target.value)} /></label>
                <label className="block"><span className="label-mono">alert on severity at least</span>
                    <select className={input} value={minSeverity} onChange={(e) => setMin(e.target.value)}>
                        <option value="">off</option>
                        {SEVERITIES.map((s) => <option key={s} value={s}>{s}</option>)}
                    </select></label>
                <p className="sm:col-span-2 text-xs text-fg-3">Cases at or above this severity are posted as a live card when they're created, escalated into it or breach SLA; later updates thread under the card. Owners also get a DM on escalation and SLA breach.</p>
            </div>
            <div className="flex items-center gap-2">
                <button onClick={() => save.mutate()} disabled={save.isPending} className="h-8 px-3 bg-accent text-on-accent text-sm disabled:opacity-50">Save</button>
                <button onClick={() => test.mutate()} disabled={!data?.configured || test.isPending} className="h-8 px-3 border border-line-strong text-sm disabled:opacity-50">Test</button>
                {msg && <span className={msg.ok ? 'text-xs text-emerald-300' : 'text-xs text-red-300'}>{msg.text}</span>}
            </div>
        </div>
    );
}
