import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { MessageSquare } from 'lucide-react';
import { isAxiosError } from 'axios';
import { api } from '../../api/client';

interface SlackConfig { configured: boolean; team_id: string | null; default_channel: string | null }

const detail = (e: unknown, fallback: string) => (isAxiosError(e) && typeof e.response?.data?.detail === 'string' ? e.response.data.detail : fallback);

export default function SlackCard() {
    const qc = useQueryClient();
    const { data } = useQuery({ queryKey: ['slack-config'], queryFn: async () => (await api.get('/slack/config')).data as SlackConfig });
    const [token, setToken] = useState('');
    const [secret, setSecret] = useState('');
    const [channelEdit, setChannel] = useState<string | null>(null);
    const channel = channelEdit ?? data?.default_channel ?? '';
    const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);

    const save = useMutation({
        mutationFn: async () => (await api.put('/slack/config', { bot_token: token || null, signing_secret: secret || null, default_channel: channel })).data,
        onSuccess: () => { setToken(''); setSecret(''); setChannel(null); setMsg({ ok: true, text: 'Saved — now click Test' }); qc.invalidateQueries({ queryKey: ['slack-config'] }); },
        onError: (e) => setMsg({ ok: false, text: detail(e, 'Save failed') }),
    });
    const test = useMutation({
        mutationFn: async () => (await api.post('/slack/config/test')).data as { team: string },
        onSuccess: (r) => { setMsg({ ok: true, text: `Connected to ${r.team}` }); qc.invalidateQueries({ queryKey: ['slack-config'] }); },
        onError: (e) => setMsg({ ok: false, text: detail(e, 'Test failed') }),
    });

    const input = 'w-full border border-zinc-300 px-2 py-1.5 text-sm font-mono';
    return (
        <div className="bg-white border border-zinc-200 p-5 space-y-3">
            <div className="flex items-center gap-2">
                <MessageSquare size={16} className="text-accent-600" />
                <h2 className="font-semibold text-zinc-800">Slack</h2>
                <span className="ml-auto label-mono">{data?.team_id ? `connected · ${data.team_id}` : data?.configured ? 'saved · not tested' : 'not configured'}</span>
            </div>
            <p className="text-xs text-zinc-500">
                Create a Slack app from <code className="font-mono">docs/slack/slack-app-manifest.yml</code> and paste its credentials. Interactivity URL:{' '}
                <code className="font-mono">{window.location.origin}/api/v1/slack/interactions</code>
            </p>
            <label className="block"><span className="label-mono">bot token</span>
                <input type="password" className={input} value={token} placeholder={data?.configured ? '•••••• (unchanged)' : 'xoxb-…'} onChange={(e) => setToken(e.target.value)} /></label>
            <label className="block"><span className="label-mono">signing secret</span>
                <input type="password" className={input} value={secret} placeholder={data?.configured ? '•••••• (unchanged)' : ''} onChange={(e) => setSecret(e.target.value)} /></label>
            <label className="block"><span className="label-mono">default channel</span>
                <input className={input} value={channel} placeholder="#soc-alerts" onChange={(e) => setChannel(e.target.value)} /></label>
            <div className="flex items-center gap-2">
                <button onClick={() => save.mutate()} disabled={save.isPending} className="h-8 px-3 bg-accent-600 text-white text-sm disabled:opacity-50">Save</button>
                <button onClick={() => test.mutate()} disabled={!data?.configured || test.isPending} className="h-8 px-3 border border-zinc-300 text-sm disabled:opacity-50">Test</button>
                {msg && <span className={msg.ok ? 'text-xs text-emerald-700' : 'text-xs text-red-700'}>{msg.text}</span>}
            </div>
        </div>
    );
}
