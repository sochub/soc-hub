import { useState } from 'react';
import { Trash2 } from 'lucide-react';
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

function JsonField({ value, disabled, onChange }: { value: unknown; disabled: boolean; onChange: (v: unknown) => void }) {
    const [text, setText] = useState(value === undefined || value === null ? '' : typeof value === 'string' ? value : JSON.stringify(value, null, 2));
    const [err, setErr] = useState<string | null>(null);
    return (
        <>
            <textarea rows={4} className={monoCls} disabled={disabled} value={text} onChange={(e) => setText(e.target.value)}
                onBlur={() => {
                    if (!text.trim()) { setErr(null); onChange(undefined); return; }
                    try { onChange(JSON.parse(text)); setErr(null); }
                    catch { onChange(text); setErr('Not valid JSON — saved as a text template'); }
                }} />
            {err && <p className="text-[11px] text-amber-700 mt-0.5">{err}</p>}
        </>
    );
}

function Field({ f, value, disabled, onChange }: { f: FieldDef; value: unknown; disabled: boolean; onChange: (v: unknown) => void }) {
    const str = value === undefined || value === null ? '' : String(value);
    switch (f.kind) {
        case 'select':
            return <select className={inputCls} disabled={disabled} value={str} onChange={(e) => onChange(e.target.value || undefined)}>
                {(f.options ?? []).map((o) => <option key={o} value={o}>{o || '—'}</option>)}
            </select>;
        case 'textarea':
            return <textarea rows={4} className={monoCls} disabled={disabled} value={str} placeholder={f.placeholder} onChange={(e) => onChange(e.target.value)} />;
        case 'json':
            return <JsonField value={value} disabled={disabled} onChange={onChange} />;
        default:
            return <input className={f.kind === 'number' ? inputCls : monoCls} disabled={disabled} value={str} placeholder={f.placeholder}
                onChange={(e) => onChange(e.target.value === '' ? undefined : e.target.value)} />;
    }
}

export default function NodeInspector({ node, readOnly, errors, onChange, onRename, onDelete }: Props) {
    const def = NODE_DEF[node.type];
    const [idDraft, setIdDraft] = useState(node.id);
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
                    <Field f={f} value={node.config[f.key]} disabled={readOnly} onChange={(v) => setCfg(f.key, v)} />
                    {f.help && <span className="text-[11px] text-zinc-500">{f.help}</span>}
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

            <div className="border-t border-zinc-200 pt-3 text-[11px] text-zinc-500 space-y-0.5">
                <p className="label-mono">available in templates</p>
                <p className="font-mono">case.* · alert.* · trigger.* · steps.&lt;id&gt;.output · loop.item · loop.index · dry_run</p>
            </div>
        </div>
    );
}
