import { useState } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import { X, FlaskConical } from 'lucide-react';
import { api } from '../../api/client';
import { NODE_DEF } from './nodeCatalog';
import type { Workflow } from './types';

type Source = 'case' | 'alert' | 'payload';

export default function DryRunDialog({ workflow, onClose }: { workflow: Workflow; onClose: () => void }) {
    const navigate = useNavigate();
    const [source, setSource] = useState<Source>(workflow.trigger_type === 'alert.ingested' ? 'alert' : 'case');
    const [caseId, setCaseId] = useState('');
    const [alertId, setAlertId] = useState('');
    const [payload, setPayload] = useState('{\n  "event": "manual"\n}');
    const [mocks, setMocks] = useState<Record<string, string>>({});
    const [answers, setAnswers] = useState<Record<string, string>>({});
    const [error, setError] = useState<string | null>(null);

    const { data: cases = [] } = useQuery({ queryKey: ['cases', 'dry-run-picker'], queryFn: async () => (await api.get('/cases/')).data as { id: number; title: string }[], enabled: source === 'case' });
    const { data: alerts = [] } = useQuery({ queryKey: ['alerts', 'dry-run-picker'], queryFn: async () => (await api.get('/alerts/')).data as { id: number; title: string; status: string }[], enabled: source === 'alert' });

    const sideEffectNodes = workflow.graph.nodes.filter((n) => NODE_DEF[n.type]?.sideEffect && n.type !== 'slack_ask_user');
    const askNodes = workflow.graph.nodes.filter((n) => n.type === 'slack_ask_user');

    const run = useMutation({
        mutationFn: async () => {
            const body: Record<string, unknown> = { mocks: {}, ask_user_answers: answers };
            for (const [nid, text] of Object.entries(mocks)) {
                if (text.trim()) (body.mocks as Record<string, unknown>)[nid] = JSON.parse(text);
            }
            if (source === 'case') body.case_id = Number(caseId);
            if (source === 'alert') body.alert_id = Number(alertId);
            if (source === 'payload') body.payload = JSON.parse(payload);
            return (await api.post(`/workflows/${workflow.id}/dry-run`, body)).data as { run_id: number };
        },
        onSuccess: ({ run_id }) => navigate(`/automations/runs/${run_id}`),
        onError: (err: { response?: { data?: { detail?: { errors?: unknown } | string } } }) => setError(err instanceof SyntaxError ? `Invalid JSON: ${err.message}` :
            (err?.response?.data?.detail as { errors?: unknown } | undefined)?.errors ? 'Workflow has validation errors — fix them first' :
            (typeof err?.response?.data?.detail === 'string' ? err.response.data.detail : 'Dry run failed')),
    });

    const ready = (source === 'case' && caseId) || (source === 'alert' && alertId) || source === 'payload';
    const input = 'w-full border border-zinc-300 px-2 py-1.5 text-sm bg-white';

    return (
        <div className="fixed inset-0 z-50 bg-zinc-900/40 flex items-center justify-center p-4" role="dialog" aria-modal="true" aria-label="Dry run">
            <div className="bg-white border border-zinc-200 w-full max-w-lg max-h-[90vh] overflow-y-auto">
                <div className="flex items-center justify-between px-4 py-3 border-b border-zinc-200">
                    <h2 className="font-semibold text-zinc-900 flex items-center gap-2"><FlaskConical size={16} />Dry run “{workflow.name}”</h2>
                    <button onClick={onClose} aria-label="Close"><X size={16} /></button>
                </div>
                <div className="p-4 space-y-4 text-sm">
                    <p className="text-zinc-600">Conditions, loops and case search run for real. Everything else is simulated — nothing is changed, sent or called.</p>
                    <div className="flex gap-4">
                        {(['case', 'alert', 'payload'] as const).map((s) => (
                            <label key={s} className="flex items-center gap-1.5 capitalize">
                                <input type="radio" name="src" checked={source === s} onChange={() => setSource(s)} />{s === 'payload' ? 'JSON payload' : s}
                            </label>
                        ))}
                    </div>
                    {source === 'case' && <select className={input} value={caseId} onChange={(e) => setCaseId(e.target.value)}>
                        <option value="">Select a case…</option>
                        {cases.map((c) => <option key={c.id} value={c.id}>#{c.id} {c.title}</option>)}
                    </select>}
                    {source === 'alert' && <select className={input} value={alertId} onChange={(e) => setAlertId(e.target.value)}>
                        <option value="">Select an alert…</option>
                        {alerts.map((a) => <option key={a.id} value={a.id}>#{a.id} {a.title} ({a.status})</option>)}
                    </select>}
                    {source === 'payload' && <textarea rows={6} className={input + ' font-mono text-xs'} value={payload} onChange={(e) => setPayload(e.target.value)} />}

                    {askNodes.length > 0 && (
                        <div className="space-y-2">
                            <p className="label-mono">slack answers</p>
                            {askNodes.map((n) => {
                                const raw = n.config.buttons;
                                const buttons = Array.isArray(raw) ? (raw as string[])
                                    : typeof raw === 'string' && raw.trim() ? raw.split(',').map((x) => x.trim()).filter(Boolean) : ['Yes', 'No'];
                                return (
                                    <label key={n.id} className="flex items-center gap-2">
                                        <span className="font-mono text-xs w-32 truncate">{n.id}</span>
                                        <select className={input} value={answers[n.id] ?? ''} onChange={(e) => setAnswers({ ...answers, [n.id]: e.target.value })}>
                                            <option value="">{buttons[0]} (default)</option>
                                            {buttons.slice(1).map((b) => <option key={b} value={b}>{b}</option>)}
                                            <option value="timeout">— times out —</option>
                                        </select>
                                    </label>
                                );
                            })}
                        </div>
                    )}

                    {sideEffectNodes.length > 0 && (
                        <details>
                            <summary className="label-mono cursor-pointer">mock outputs (optional)</summary>
                            <div className="mt-2 space-y-2">
                                {sideEffectNodes.map((n) => (
                                    <label key={n.id} className="block">
                                        <span className="font-mono text-xs">{n.id}</span>
                                        <textarea rows={2} className={input + ' font-mono text-xs'} placeholder='{"status": 200, "body": {}}'
                                            value={mocks[n.id] ?? ''} onChange={(e) => setMocks({ ...mocks, [n.id]: e.target.value })} />
                                    </label>
                                ))}
                            </div>
                        </details>
                    )}
                    {error && <p className="text-red-700 text-xs">{error}</p>}
                </div>
                <div className="flex justify-end gap-2 px-4 py-3 border-t border-zinc-200">
                    <button onClick={onClose} className="h-8 px-3 border border-zinc-300 text-sm">Cancel</button>
                    <button disabled={!ready || run.isPending} onClick={() => { setError(null); run.mutate(); }}
                        className="h-8 px-3 bg-violet-600 text-white text-sm hover:bg-violet-700 disabled:opacity-50">Start dry run</button>
                </div>
            </div>
        </div>
    );
}
