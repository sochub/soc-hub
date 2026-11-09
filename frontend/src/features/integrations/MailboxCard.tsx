import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Mail } from 'lucide-react';
import { isAxiosError } from 'axios';
import { api } from '../../api/client';

interface Mailbox {
    id: number; name: string; host: string; port: number; use_ssl: boolean; username: string;
    folder: string; poll_interval_seconds: number; enabled: boolean; fallback_create_alert: boolean;
    last_polled_at: string | null; last_error: string | null; password_set: boolean;
}
interface Form {
    name: string; host: string; port: number | string; use_ssl: boolean; username: string; password: string;
    folder: string; poll_interval_seconds: number | string; enabled: boolean; fallback_create_alert: boolean;
}
const EMPTY: Form = {
    name: '', host: '', port: 993, use_ssl: true, username: '', password: '',
    folder: 'INBOX', poll_interval_seconds: 120, enabled: true, fallback_create_alert: false,
};

const errText = (e: unknown, fb: string): string => {
    const d: unknown = isAxiosError(e) ? e.response?.data?.detail : undefined;
    if (typeof d === 'string') return d;
    if (Array.isArray(d) && typeof d[0]?.msg === 'string') return d[0].msg;
    return fb;
};

export default function MailboxCard({ tenantId }: { tenantId: number | null }) {
    const key = ['mailboxes', tenantId];
    const qc = useQueryClient();
    const { data } = useQuery({ queryKey: key, queryFn: async () => (await api.get('/mailboxes/')).data as Mailbox[] });
    const [editing, setEditing] = useState<number | 'new' | null>(null);
    const [form, setForm] = useState<Form>(EMPTY);
    const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
    const [tests, setTests] = useState<Record<number, { ok: boolean; text: string }>>({});
    const [confirmDel, setConfirmDel] = useState<number | null>(null);

    const set = (p: Partial<Form>) => { setForm((f) => ({ ...f, ...p })); setMsg(null); };
    const open = (m: Mailbox | null) => {
        setMsg(null);
        setEditing(m ? m.id : 'new');
        setForm(m ? { ...m, password: '' } : EMPTY);
    };

    const save = useMutation({
        mutationFn: async () => {
            const body: Record<string, unknown> = {
                ...form, port: Number(form.port), poll_interval_seconds: Number(form.poll_interval_seconds),
            };
            if (!form.password) delete body.password; // omitted on edit = keep the stored password
            return editing === 'new' ? api.post('/mailboxes/', body) : api.put(`/mailboxes/${editing}`, body);
        },
        onSuccess: () => { qc.invalidateQueries({ queryKey: key }); setEditing(null); setMsg({ ok: true, text: 'Saved' }); },
        onError: (e) => setMsg({ ok: false, text: errText(e, 'Save failed') }),
    });
    const del = useMutation({
        mutationFn: async (id: number) => api.delete(`/mailboxes/${id}`),
        onSuccess: () => { setConfirmDel(null); qc.invalidateQueries({ queryKey: key }); },
        onError: (e) => { setConfirmDel(null); setMsg({ ok: false, text: errText(e, 'Delete failed') }); },
    });
    const test = useMutation({
        mutationFn: async (id: number) => {
            try {
                const r = (await api.post(`/mailboxes/${id}/test`)).data as { ok: boolean; message_count?: number | null; error?: string | null };
                return { id, ok: r.ok, text: r.ok ? `Connected, ${r.message_count ?? 0} message(s) in folder` : (r.error || 'Connection failed') };
            } catch (e) { return { id, ok: false, text: errText(e, 'Test failed') }; }
        },
        onSuccess: (r) => setTests((t) => ({ ...t, [r.id]: { ok: r.ok, text: r.text } })),
    });

    if (!data) return null;
    const input = 'w-full border border-line-strong px-2 py-1.5 text-sm';
    const chk = (label: string, k: 'use_ssl' | 'enabled' | 'fallback_create_alert') => (
        <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={form[k]} onChange={(e) => set({ [k]: e.target.checked })} />{label}</label>
    );

    return (
        <div className="bg-panel border border-line p-5 space-y-3">
            <div className="flex items-center gap-2">
                <Mail size={16} className="text-accent" />
                <h2 className="font-semibold text-fg">Mailboxes</h2>
            </div>
            <p className="text-xs text-fg-3">New emails in a mailbox folder fire the <code>email.received</code> workflow trigger (see the Email to alert template). Passwords are write-only.</p>

            {data.length === 0 && editing === null && <p className="text-sm text-fg-3">No mailboxes configured.</p>}
            <ul className="space-y-2">
                {data.map((m) => (
                    <li key={m.id} className="border border-line p-3 text-sm space-y-1">
                        <div className="flex items-center gap-2 flex-wrap">
                            <span className="font-semibold text-fg">{m.name}</span>
                            <span className="font-mono text-xs text-fg-3">{m.username}@{m.host}:{m.port}/{m.folder}</span>
                            {!m.enabled && <span className="label-mono">disabled</span>}
                            <span className="ml-auto flex items-center gap-2">
                                <button onClick={() => test.mutate(m.id)} disabled={test.isPending} className="h-7 px-2 border border-line-strong text-xs disabled:opacity-50">Test</button>
                                <button onClick={() => open(m)} className="h-7 px-2 border border-line-strong text-xs">Edit</button>
                                {confirmDel === m.id ? (
                                    <>
                                        <button onClick={() => del.mutate(m.id)} disabled={del.isPending} className="h-7 px-2 bg-red-600 text-white text-xs disabled:opacity-50">Confirm delete</button>
                                        <button onClick={() => setConfirmDel(null)} className="h-7 px-2 border border-line-strong text-xs">Cancel</button>
                                    </>
                                ) : <button onClick={() => setConfirmDel(m.id)} className="h-7 px-2 border border-line-strong text-xs">Delete</button>}
                            </span>
                        </div>
                        <p className="text-xs text-fg-3">
                            Last polled: {m.last_polled_at ? new Date(m.last_polled_at).toLocaleString() : 'never'}
                        </p>
                        {m.last_error && <p className="text-xs text-red-300 break-words">Last error: {m.last_error}</p>}
                        {tests[m.id] && <p role="status" className={`text-xs break-words ${tests[m.id].ok ? 'text-emerald-300' : 'text-red-300'}`}>{tests[m.id].text}</p>}
                    </li>
                ))}
            </ul>

            {editing !== null ? (
                <div className="border border-line p-3 space-y-3">
                    <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                        <label className="block"><span className="label-mono">name</span>
                            <input className={input} value={form.name} onChange={(e) => set({ name: e.target.value })} /></label>
                        <label className="block"><span className="label-mono">host</span>
                            <input className={`${input} font-mono`} value={form.host} onChange={(e) => set({ host: e.target.value })} placeholder="imap.example.com" /></label>
                        <label className="block"><span className="label-mono">port</span>
                            <input type="number" min={1} max={65535} className={`${input} num`} value={form.port} onChange={(e) => set({ port: e.target.value })} /></label>
                        <label className="block"><span className="label-mono">folder</span>
                            <input className={input} value={form.folder} onChange={(e) => set({ folder: e.target.value })} /></label>
                        <label className="block"><span className="label-mono">username</span>
                            <input autoComplete="off" className={input} value={form.username} onChange={(e) => set({ username: e.target.value })} /></label>
                        <label className="block"><span className="label-mono">password</span>
                            <input type="password" autoComplete="new-password" className={`${input} font-mono`} value={form.password}
                                onChange={(e) => set({ password: e.target.value })} placeholder={editing === 'new' ? '' : 'unchanged'} /></label>
                        <label className="block"><span className="label-mono">poll interval (seconds, min 60)</span>
                            <input type="number" min={60} className={`${input} num`} value={form.poll_interval_seconds} onChange={(e) => set({ poll_interval_seconds: e.target.value })} /></label>
                    </div>
                    <div className="flex flex-wrap gap-4">
                        {chk('Use SSL', 'use_ssl')}{chk('Enabled', 'enabled')}{chk('Create an alert if no workflow handles the email', 'fallback_create_alert')}
                    </div>
                    <div className="flex items-center gap-2">
                        <button onClick={() => save.mutate()} disabled={save.isPending} className="h-8 px-3 bg-accent text-on-accent text-sm disabled:opacity-50">Save</button>
                        <button onClick={() => { setEditing(null); setMsg(null); }} className="h-8 px-3 border border-line-strong text-sm">Cancel</button>
                    </div>
                </div>
            ) : <button onClick={() => open(null)} className="h-8 px-3 border border-line-strong text-sm">Add mailbox</button>}
            {msg && <p role="status" className={`text-xs break-words ${msg.ok ? 'text-emerald-300' : 'text-red-300'}`}>{msg.text}</p>}
        </div>
    );
}
