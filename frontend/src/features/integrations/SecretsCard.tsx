import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { isAxiosError } from 'axios';
import { KeyRound, Pencil, Plus, Trash2 } from 'lucide-react';
import { api } from '../../api/client';
import Modal, { btnPrimary, btnSecondary, modalInput, modalLabel } from '../../components/layout/Modal';
import type { Secret } from '../../types';
import { NAME_RE, errText, hostStatus, parseHosts } from './secretsUtil';

export interface SecretPrefill { name: string; value: string; host: string }

/** Add / edit dialog. Mount it only while open (state initialises from props). Values are write-only. */
export function SecretDialog({ secret, prefill, tenantId, onClose, onSaved }: {
    secret?: Secret | null; prefill?: SecretPrefill; tenantId: number | null;
    onClose: () => void; onSaved?: (name: string) => void;
}) {
    const qc = useQueryClient();
    const edit = !!secret;
    const [name, setName] = useState(secret?.name ?? prefill?.name ?? '');
    const [value, setValue] = useState(prefill?.value ?? '');
    const [hosts, setHosts] = useState(secret ? secret.allowed_hosts.join('\n') : prefill?.host ?? '');
    const [desc, setDesc] = useState(secret?.description ?? '');
    const [err, setErr] = useState<string | null>(null);

    const list = parseHosts(hosts);
    const statuses = list.map(hostStatus);
    const anyBad = statuses.includes('bad');
    const nameOk = edit || NAME_RE.test(name);
    const valueOk = edit || value.trim().length > 0;

    const save = useMutation({
        mutationFn: async (b: Record<string, unknown>) =>
            edit ? api.put(`/secrets/${secret!.name}`, b) : api.post('/secrets/', b),
        onSuccess: () => {
            qc.invalidateQueries({ queryKey: ['secrets', tenantId] });
            qc.invalidateQueries({ queryKey: ['secret-names'] });
            setValue(''); save.reset();
            onSaved?.(edit ? secret!.name : name);
            onClose();
        },
        onError: (e) => setErr(errText(e, 'Save failed')),
    });

    return (
        <Modal open onClose={onClose} size="md" title={edit ? `Edit ${secret!.name}` : 'Add secret'}
            footer={<>
                <button type="button" className={btnSecondary} onClick={onClose}>Cancel</button>
                <button type="button" className={btnPrimary} disabled={save.isPending || !nameOk || !valueOk || anyBad}
                    onClick={() => {
                        setErr(null);
                        if (edit) {
                            const b: Record<string, unknown> = { allowed_hosts: list, description: desc.trim() };
                            if (value.trim()) b.value = value;
                            save.mutate(b);
                        } else save.mutate({ name, value, allowed_hosts: list, description: desc.trim() || null });
                    }}>{save.isPending ? 'Saving…' : 'Save'}</button>
            </>}>
            <form className="space-y-3" onSubmit={(e) => e.preventDefault()} autoComplete="off">
                <label className="block"><span className={modalLabel}>Name</span>
                    <input className={`${modalInput} font-mono`} value={name} disabled={edit} placeholder="EXAMPLE_API_KEY"
                        onChange={(e) => setName(e.target.value.toUpperCase().replace(/[^A-Z0-9_]/g, '_'))} />
                    {!edit && name && !nameOk && <span className="text-xs text-red-700">Start with a letter; A-Z, 0-9 and _ only, 2-64 characters.</span>}
                </label>
                <label className="block"><span className={modalLabel}>Value</span>
                    <input type="password" autoComplete="new-password" className={`${modalInput} font-mono`} value={value}
                        onChange={(e) => setValue(e.target.value)}
                        placeholder={edit ? '•••• (saved — leave blank to keep)' : ''} />
                    <span className="text-xs text-zinc-500">Write-only. It is encrypted and never shown again.</span>
                </label>
                <label className="block"><span className={modalLabel}>Allowed hosts (one per line)</span>
                    <textarea rows={3} className={`${modalInput} font-mono text-xs`} value={hosts} placeholder={'api.example.com\n*.example.org'}
                        onChange={(e) => setHosts(e.target.value)} />
                    <ul className="text-xs space-y-0.5 mt-1">
                        {list.map((h, i) => statuses[i] !== 'ok' && (
                            <li key={`${h}-${i}`} className={statuses[i] === 'bad' ? 'text-red-700' : 'text-amber-700'}>
                                <span className="font-mono">{h}</span>: {statuses[i] === 'bad'
                                    ? 'not a valid host pattern (lowercase hostname or *.domain)'
                                    : 'must be on the tenant HTTP allowlist'}
                            </li>
                        ))}
                    </ul>
                    {list.length === 0 && <span className="text-xs text-amber-700">With no hosts the secret cannot be sent anywhere.</span>}
                </label>
                <label className="block"><span className={modalLabel}>Description</span>
                    <input className={modalInput} value={desc} maxLength={500} onChange={(e) => setDesc(e.target.value)} /></label>
                {err && <p role="alert" className="text-xs text-red-700 bg-red-50 border border-red-200 p-2">{err}</p>}
            </form>
        </Modal>
    );
}

export default function SecretsCard({ tenantId }: { tenantId: number | null }) {
    const key = ['secrets', tenantId];
    const qc = useQueryClient();
    const { data, isError, error } = useQuery({ queryKey: key, queryFn: async () => (await api.get('/secrets/')).data as Secret[] });
    const [dialog, setDialog] = useState<{ secret: Secret | null } | null>(null);
    const [confirm, setConfirm] = useState<string | null>(null);
    const [inUse, setInUse] = useState<{ name: string; workflows: { id: number; name: string }[] } | null>(null);
    const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);

    const del = useMutation({
        mutationFn: async (v: { name: string; force: boolean }) =>
            api.delete(`/secrets/${v.name}`, { params: v.force ? { force: true } : undefined }),
        onSuccess: (_r, v) => {
            setConfirm(null); setInUse(null); setMsg({ ok: true, text: `Deleted ${v.name}` });
            qc.invalidateQueries({ queryKey: key }); qc.invalidateQueries({ queryKey: ['secret-names'] });
        },
        onError: (e, v) => {
            setConfirm(null);
            if (isAxiosError(e) && e.response?.status === 409 && Array.isArray(e.response.data?.workflows)) {
                setInUse({ name: v.name, workflows: e.response.data.workflows });
            } else setMsg({ ok: false, text: errText(e, 'Delete failed') });
        },
    });

    if (isError) return <div role="alert" className="bg-white border border-zinc-200 p-5 text-sm text-red-700">Secrets: {errText(error, 'could not load secrets')}</div>;
    if (!data) return null;
    const btn = 'h-8 px-3 text-sm';

    return (
        <div className="bg-white border border-zinc-200 p-5 space-y-3">
            <div className="flex items-center gap-2">
                <KeyRound size={16} className="text-accent-600" />
                <h2 className="font-semibold text-zinc-800">Secrets</h2>
                <button onClick={() => { setMsg(null); setDialog({ secret: null }); }}
                    className={`ml-auto ${btn} bg-accent-600 text-white inline-flex items-center gap-1.5`}><Plus size={14} />Add secret</button>
            </div>
            <p className="text-xs text-zinc-500">Credentials for workflow HTTP requests. Reference one as <code className="font-mono">{'{{ secrets.NAME }}'}</code> in a URL, header value or body. Values are encrypted, only sent to the allowed hosts, and redacted from run history.</p>
            {msg && <p role="status" className={msg.ok ? 'text-xs text-emerald-700' : 'text-xs text-red-700'}>{msg.text}</p>}

            <div className="border border-zinc-200 overflow-x-auto">
                <table className="w-full text-sm">
                    <thead className="bg-zinc-50 text-left label-mono">
                        <tr><th className="px-3 py-2">Name</th><th className="px-3 py-2">Description</th><th className="px-3 py-2">Hosts</th>
                            <th className="px-3 py-2">Used by</th><th className="px-3 py-2">Last used</th><th className="px-3 py-2"></th></tr>
                    </thead>
                    <tbody className="divide-y divide-zinc-100">
                        {data.length === 0 && <tr><td colSpan={6} className="px-3 py-6 text-center text-zinc-400">No secrets yet</td></tr>}
                        {data.map((s) => (
                            <tr key={s.name} className="align-top">
                                <td className="px-3 py-2 font-mono text-xs text-zinc-900">{s.name}</td>
                                <td className="px-3 py-2 text-xs text-zinc-600">{s.description || '—'}</td>
                                <td className="px-3 py-2">
                                    <div className="flex flex-wrap gap-1">
                                        {s.allowed_hosts.length === 0 && <span className="text-xs text-amber-700">none</span>}
                                        {s.allowed_hosts.map((h) => <span key={h} className="px-1.5 py-0.5 border border-zinc-200 bg-zinc-50 font-mono text-[11px]">{h}</span>)}
                                    </div>
                                </td>
                                <td className="px-3 py-2 text-xs">
                                    <span className="num">{s.in_use_by.length}</span>
                                    {s.in_use_by.length > 0 && <span className="text-zinc-500"> · {s.in_use_by.map((w) => w.name).join(', ')}</span>}
                                </td>
                                <td className="px-3 py-2 text-xs text-zinc-500">{s.last_used_at ? new Date(s.last_used_at).toLocaleString() : 'never'}</td>
                                <td className="px-3 py-2 whitespace-nowrap">
                                    {confirm === s.name ? (
                                        <span className="flex items-center gap-2 text-xs text-zinc-700">Delete?
                                            <button onClick={() => del.mutate({ name: s.name, force: false })} disabled={del.isPending} className={`${btn} bg-red-600 text-white disabled:opacity-50`}>Delete</button>
                                            <button onClick={() => setConfirm(null)} className={`${btn} border border-zinc-300`}>Cancel</button></span>
                                    ) : (
                                        <span className="flex items-center gap-2">
                                            <button aria-label={`Edit ${s.name}`} onClick={() => { setMsg(null); setDialog({ secret: s }); }} className="p-1 text-zinc-400 hover:text-accent-600"><Pencil size={14} /></button>
                                            <button aria-label={`Delete ${s.name}`} onClick={() => { setMsg(null); setInUse(null); setConfirm(s.name); }} className="p-1 text-zinc-400 hover:text-red-600"><Trash2 size={14} /></button>
                                        </span>
                                    )}
                                </td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            </div>

            {inUse && (
                <div role="alert" className="text-xs border border-amber-200 bg-amber-50 text-amber-900 p-3 space-y-2">
                    <p><span className="font-mono">{inUse.name}</span> is used by {inUse.workflows.length} workflow(s): {inUse.workflows.map((w) => w.name).join(', ')}. Deleting it will make those workflows fail.</p>
                    <div className="flex gap-2">
                        <button onClick={() => del.mutate({ name: inUse.name, force: true })} disabled={del.isPending} className={`${btn} bg-red-600 text-white disabled:opacity-50`}>Delete anyway</button>
                        <button onClick={() => setInUse(null)} className={`${btn} border border-zinc-300 bg-white`}>Cancel</button>
                    </div>
                </div>
            )}

            {dialog && <SecretDialog secret={dialog.secret} tenantId={tenantId} onClose={() => setDialog(null)} />}
        </div>
    );
}
