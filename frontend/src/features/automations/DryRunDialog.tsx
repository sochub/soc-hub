import { useState } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import { FlaskConical } from 'lucide-react';
import { api } from '../../api/client';
import { cn } from '../../lib/utils';
import Modal, { modalInput, btnPrimary, btnSecondary } from '../../components/layout/Modal';
import { NODE_DEF } from './nodeCatalog';
import type { Workflow } from './types';

type Source = 'case' | 'alert' | 'payload';

export default function DryRunDialog({ workflow, onClose }: { workflow: Workflow; onClose: () => void }) {
    const navigate = useNavigate();
    const [source, setSource] = useState<Source>(workflow.trigger_type === 'alert.ingested' ? 'alert' : 'case');
    const [caseId, setCaseId] = useState('');
    const [alertId, setAlertId] = useState('');
    const [payload, setPayload] = useState('{\n  "event": "manual"\n}');
    const [filter, setFilter] = useState('');
    const [mocks, setMocks] = useState<Record<string, string>>({});
    const [answers, setAnswers] = useState<Record<string, string>>({});
    const [error, setError] = useState<string | null>(null);

    const { data: cases = [] } = useQuery({ queryKey: ['cases', 'dry-run-picker'], queryFn: async () => (await api.get('/cases/')).data as { id: number; title: string }[], enabled: source === 'case' });
    const { data: alerts = [] } = useQuery({ queryKey: ['alerts', 'dry-run-picker'], queryFn: async () => (await api.get('/alerts/')).data as { id: number; title: string; status: string }[], enabled: source === 'alert' });

    const sideEffectNodes = workflow.graph.nodes.filter((n) => NODE_DEF[n.type]?.sideEffect && n.type !== 'slack_ask_user');
    const askNodes = workflow.graph.nodes.filter((n) => n.type === 'slack_ask_user');

    const run = useMutation({
        mutationFn: async () => {
            const body: Record<string, unknown> = { mocks: {}, ask_user_answers: Object.fromEntries(Object.entries(answers).filter(([, v]) => v !== '')) };
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
    const input = modalInput;

    return (
        <Modal
            open
            onClose={onClose}
            size="lg"
            title={<span className="flex items-center gap-2"><FlaskConical size={16} />Dry run “{workflow.name}”</span>}
            footer={<>
                <button onClick={onClose} className={btnSecondary}>Cancel</button>
                <button disabled={!ready || run.isPending} onClick={() => { setError(null); run.mutate(); }}
                    className={cn(btnPrimary, 'bg-violet-600 hover:bg-violet-700')}>Start dry run</button>
            </>}
        >
            <div className="space-y-4 text-sm">
                <p className="text-zinc-600">Conditions, loops and case search run for real. Everything else is simulated — nothing is changed, sent or called.</p>
                <div className="flex gap-4">
                    {(['case', 'alert', 'payload'] as const).map((s) => (
                        <label key={s} className="flex items-center gap-1.5 capitalize">
                            <input type="radio" name="src" checked={source === s} onChange={() => { setSource(s); setFilter(''); }} />{s === 'payload' ? 'JSON payload' : s}
                        </label>
                    ))}
                </div>
                {source !== 'payload' && (() => {
                    const isCase = source === 'case';
                    const label = isCase ? 'cases' : 'alerts';
                    const q = filter.trim().toLowerCase();
                    const items: { id: number; title: string; status?: string }[] = isCase ? cases : alerts;
                    const shown = items.filter((x) => !q || `#${x.id}`.includes(q) || String(x.id) === q.replace('#', '') || x.title.toLowerCase().includes(q));
                    const value = isCase ? caseId : alertId;
                    const setValue = isCase ? setCaseId : setAlertId;
                    return (
                        <div className="space-y-2">
                            <div className="flex gap-2">
                                <input className={input} aria-label={`Filter ${label}`} placeholder={`Filter ${label}…`} value={filter} onChange={(e) => setFilter(e.target.value)} />
                                <input type="number" min={1} className={input + ' w-32'} aria-label={isCase ? 'Case ID' : 'Alert ID'} placeholder="or enter ID" value={value} onChange={(e) => setValue(e.target.value)} />
                            </div>
                            <select className={input} value={value} onChange={(e) => setValue(e.target.value)}>
                                <option value="">Select {isCase ? 'a case' : 'an alert'}…</option>
                                {shown.map((x) => <option key={x.id} value={x.id}>#{x.id} {x.title}{x.status ? ` (${x.status})` : ''}</option>)}
                            </select>
                        </div>
                    );
                })()}
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
        </Modal>
    );
}
