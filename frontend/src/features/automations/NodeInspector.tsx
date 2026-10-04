import { useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Lock, Trash2 } from 'lucide-react';
import { api } from '../../api/client';
import type { SecretName, User } from '../../types';
import { SecretDialog, type SecretPrefill } from '../integrations/SecretsCard';
import { secretTemplate, suggestName } from '../integrations/secretsUtil';
import { NODE_DEF, type FieldDef } from './nodeCatalog';
import type { WfNode } from './types';

interface Props {
    node: WfNode;
    readOnly: boolean;
    errors: string[];
    onChange: (node: WfNode) => void;
    onRename: (oldId: string, newId: string) => boolean;
    onDelete: () => void;
}

const inputCls = 'w-full border border-zinc-300 px-2 py-1.5 text-sm bg-white focus:outline-none focus:border-accent-500 disabled:bg-zinc-50';
const monoCls = inputCls + ' font-mono text-xs';

const CRED_HEADERS = new Set(['authorization', 'x-api-key', 'api-key', 'apikey', 'x-auth-token', 'x-apikey', 'private-token']);
const SCHEME_RE = /^((?:Bearer|Token)\s+)(\S[\s\S]*)$/i;

function urlHost(url: unknown): string {
    const m = typeof url === 'string' ? /^https?:\/\/([^/{?#:@]+)/i.exec(url) : null;
    return m ? m[1].toLowerCase() : '';
}

/** "Insert secret" select: picks a tenant secret name; the caller inserts `{{ secrets.NAME }}`. */
function SecretPicker({ onPick, disabled }: { onPick: (name: string) => void; disabled: boolean }) {
    const { data: me } = useQuery({ queryKey: ['currentUser'], queryFn: async () => (await api.get('/users/me')).data as User, staleTime: 300_000 });
    const { data: names = [] } = useQuery({
        queryKey: ['secret-names', me?.active_tenant_id ?? null],
        queryFn: async () => (await api.get('/secrets/names')).data as SecretName[],
        enabled: !disabled,
    });
    const [picked, setPicked] = useState<string>('');
    if (disabled) return null;
    const cur = names.find((n) => n.name === picked);
    return (
        <div className="mt-1 flex items-center gap-2 flex-wrap">
            <Lock size={12} className="text-zinc-400" />
            <select aria-label="Insert secret" className="border border-zinc-300 px-1.5 py-0.5 text-xs bg-white font-mono" value=""
                onChange={(e) => { if (e.target.value) { setPicked(e.target.value); onPick(e.target.value); } }}>
                <option value="">{names.length ? 'Insert secret…' : 'No secrets defined'}</option>
                {names.map((n) => <option key={n.name} value={n.name}>{n.name}</option>)}
            </select>
            {cur && <span className="text-[11px] text-zinc-500">allowed: <span className="font-mono">{cur.allowed_hosts.join(', ') || 'no hosts'}</span></span>}
        </div>
    );
}

function insertAt(el: HTMLInputElement | HTMLTextAreaElement | null, text: string, name: string): string {
    const tpl = secretTemplate(name);
    const a = el?.selectionStart ?? text.length;
    const b = el?.selectionEnd ?? a;
    return text.slice(0, a) + tpl + text.slice(b);
}

function JsonField({ value, disabled, onChange, secrets }: { value: unknown; disabled: boolean; onChange: (v: unknown) => void; secrets?: boolean }) {
    const ref = useRef<HTMLTextAreaElement>(null);
    const [text, setText] = useState(value === undefined || value === null ? '' : typeof value === 'string' ? value : JSON.stringify(value, null, 2));
    const [err, setErr] = useState<string | null>(null);
    const commit = (t: string) => {
        if (!t.trim()) { setErr(null); onChange(undefined); return; }
        try { onChange(JSON.parse(t)); setErr(null); }
        catch { onChange(t); setErr('Not valid JSON — saved as a text template'); }
    };
    return (
        <>
            <textarea ref={ref} rows={4} className={monoCls} disabled={disabled} value={text} onChange={(e) => setText(e.target.value)}
                onBlur={() => commit(text)} />
            {secrets && <SecretPicker disabled={disabled} onPick={(n) => { const t = insertAt(ref.current, text, n); setText(t); commit(t); }} />}
            {err && <p className="text-[11px] text-amber-700 mt-0.5">{err}</p>}
        </>
    );
}

function Field({ f, value, disabled, onChange, secrets }: { f: FieldDef; value: unknown; disabled: boolean; onChange: (v: unknown) => void; secrets?: boolean }) {
    const str = value === undefined || value === null ? '' : String(value);
    const ref = useRef<HTMLInputElement>(null);
    switch (f.kind) {
        case 'select':
            return <select className={inputCls} disabled={disabled} value={str} onChange={(e) => onChange(e.target.value || undefined)}>
                {(f.options ?? []).map((o) => <option key={o} value={o}>{o || '—'}</option>)}
            </select>;
        case 'textarea':
            return <textarea rows={4} className={monoCls} disabled={disabled} value={str} placeholder={f.placeholder} onChange={(e) => onChange(e.target.value)} />;
        case 'json':
            return <JsonField key={JSON.stringify(value) ?? ''} value={value} disabled={disabled} onChange={onChange} secrets={secrets} />;
        default:
            return <>
                <input ref={ref} className={f.kind === 'number' ? inputCls : monoCls} disabled={disabled} value={str} placeholder={f.placeholder}
                    onChange={(e) => onChange(e.target.value === '' ? undefined : e.target.value)} />
                {secrets && <SecretPicker disabled={disabled} onPick={(n) => onChange(insertAt(ref.current, str, n))} />}
            </>;
    }
}

export default function NodeInspector({ node, readOnly, errors, onChange, onRename, onDelete }: Props) {
    const def = NODE_DEF[node.type];
    const [idDraft, setIdDraft] = useState(node.id);
    const [move, setMove] = useState<{ key: string; prefix: string; prefill: SecretPrefill } | null>(null);
    const { data: me } = useQuery({ queryKey: ['currentUser'], queryFn: async () => (await api.get('/users/me')).data as User, staleTime: 300_000 });
    const headers = node.type === 'http_request' && node.config.headers && typeof node.config.headers === 'object' && !Array.isArray(node.config.headers)
        ? node.config.headers as Record<string, unknown> : {};
    const literalHeaders = Object.entries(headers).filter(([k, v]) =>
        CRED_HEADERS.has(k.toLowerCase()) && typeof v === 'string' && !v.includes('{{') && v.trim() !== '');
    const setCfg = (key: string, v: unknown) => {
        const config = { ...node.config };
        if (v === undefined) delete config[key]; else config[key] = v;
        onChange({ ...node, config });
    };

    return (
        <div className="p-4 space-y-4 text-sm" key={node.id}>
            <div className="flex items-center justify-between">
                <span className="font-semibold text-zinc-900">{def?.label ?? node.type}</span>
                {!readOnly && node.type !== 'trigger' && (
                    <button onClick={onDelete} className="p-1 text-zinc-400 hover:text-red-600" aria-label="Delete node"><Trash2 size={15} /></button>
                )}
            </div>
            {errors.length > 0 && <ul className="text-xs text-red-700 bg-red-50 border border-red-200 p-2 space-y-0.5">{errors.map((e, i) => <li key={i}>{e}</li>)}</ul>}

            <label className="block">
                <span className="label-mono">node id</span>
                <input className={monoCls} disabled={readOnly} value={idDraft} onChange={(e) => setIdDraft(e.target.value)}
                    onBlur={() => { if (idDraft !== node.id && /^[a-z0-9_]+$/.test(idDraft)) { if (!onRename(node.id, idDraft)) setIdDraft(node.id); } else setIdDraft(node.id); }} />
                <span className="text-[11px] text-zinc-500">Reference outputs as <code className="font-mono">{`{{ steps.${node.id}.output }}`}</code></span>
            </label>

            {def?.fields.map((f) => (
                <label key={f.key} className="block">
                    <span className="label-mono">{f.label}{f.required && ' *'}</span>
                    <Field f={f} value={node.config[f.key]} disabled={readOnly} onChange={(v) => setCfg(f.key, v)}
                        secrets={node.type === 'http_request' && ['url', 'headers', 'body'].includes(f.key)} />
                    {f.help && <span className="text-[11px] text-zinc-500">{f.help}</span>}
                    {f.key === 'headers' && !readOnly && literalHeaders.map(([k, v]) => (
                        <div key={k} className="mt-1 flex items-center gap-2 text-[11px] text-amber-800 bg-amber-50 border border-amber-200 px-2 py-1">
                            <span className="flex-1"><span className="font-mono">{k}</span> holds a literal credential.</span>
                            <button type="button" className="px-2 py-0.5 border border-amber-300 bg-white hover:bg-amber-100"
                                onClick={(ev) => {
                                    ev.preventDefault();
                                    const m = k.toLowerCase() === 'authorization' ? SCHEME_RE.exec(v as string) : null;
                                    const host = urlHost(node.config.url);
                                    setMove({ key: k, prefix: m ? m[1] : '', prefill: { name: suggestName(host || 'secret', k), value: m ? m[2] : (v as string), host } });
                                }}>Move to secret</button>
                        </div>
                    ))}
                </label>
            ))}

            {node.type !== 'trigger' && (
                <div className="border-t border-zinc-200 pt-3 space-y-2">
                    <label className="flex items-center gap-2 text-xs text-zinc-700">
                        <input type="checkbox" disabled={readOnly} checked={!!node.continue_on_error}
                            onChange={(e) => onChange({ ...node, continue_on_error: e.target.checked })} /> Continue on error (output.error is set)
                    </label>
                    {node.type === 'http_request' && (
                        <>
                            <label className="block"><span className="label-mono">retries (0-2)</span>
                                <input type="number" min={0} max={2} className={inputCls} disabled={readOnly} value={node.retries ?? 2}
                                    onChange={(e) => onChange({ ...node, retries: Number(e.target.value) })} /></label>
                            <label className="flex items-center gap-2 text-xs text-zinc-700">
                                <input type="checkbox" disabled={readOnly} checked={!!node.execute_in_dry_run}
                                    onChange={(e) => onChange({ ...node, execute_in_dry_run: e.target.checked })} /> Really execute during dry runs (read-only lookups only)
                            </label>
                        </>
                    )}
                    {def?.sideEffect && (
                        <label className="block"><span className="label-mono">dry-run mock output (JSON)</span>
                            <JsonField value={node.mock_output} disabled={readOnly}
                                onChange={(v) => onChange({ ...node, mock_output: (v && typeof v === 'object') ? v as Record<string, unknown> : null })} />
                        </label>
                    )}
                </div>
            )}

            {move && (
                <SecretDialog prefill={move.prefill} tenantId={me?.active_tenant_id ?? null} onClose={() => setMove(null)}
                    onSaved={(name) => setCfg('headers', { ...headers, [move.key]: move.prefix + secretTemplate(name) })} />
            )}

            <div className="border-t border-zinc-200 pt-3 text-[11px] text-zinc-500 space-y-0.5">
                <p className="label-mono">available in templates</p>
                <p className="font-mono">case.* · alert.* · trigger.* · steps.&lt;id&gt;.output · loop.item · loop.index · dry_run</p>
            </div>
        </div>
    );
}
