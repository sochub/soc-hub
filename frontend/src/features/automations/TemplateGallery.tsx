import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Link, useNavigate } from 'react-router-dom';
import ReactMarkdown from 'react-markdown';
import { api } from '../../api/client';
import Modal, { btnPrimary, btnSecondary, modalInput, modalLabel } from '../../components/layout/Modal';
import { errText } from '../integrations/secretsUtil';
import type { TemplateInstallResult, WorkflowTemplate } from './types';

const PARAM_RE = /^[A-Za-z0-9][A-Za-z0-9._:/-]{0,252}$/;

function InstallDialog({ t, onClose }: { t: WorkflowTemplate; onClose: () => void }) {
    const qc = useQueryClient();
    const navigate = useNavigate();
    const [vals, setVals] = useState<Record<string, string>>({});
    const [result, setResult] = useState<TemplateInstallResult | null>(null);
    const valid = t.params.every((p) => PARAM_RE.test(vals[p.key] ?? ''));
    const install = useMutation({
        mutationFn: async () => (await api.post(`/workflows/templates/${t.key}/install`, { params: vals })).data as TemplateInstallResult,
        onSuccess: (r) => {
            qc.invalidateQueries({ queryKey: ['workflows'] });
            if (r.missing_secrets.length === 0) navigate(`/workflows/${r.workflow.id}`); else setResult(r);
        },
    });

    return (
        <Modal open onClose={onClose} size="lg" title={`Install: ${t.name}`}
            footer={result ? <button type="button" className={btnSecondary} onClick={onClose}>Close</button> : <>
                <button type="button" className={btnSecondary} onClick={onClose}>Cancel</button>
                <button type="button" className={btnPrimary} disabled={!valid || install.isPending} onClick={() => install.mutate()}>
                    {install.isPending ? 'Installing…' : 'Install (disabled)'}</button></>}>
            {result ? (
                <div className="space-y-3 text-sm text-fg-2">
                    <p>Installed as a disabled workflow. Create these secrets before enabling it:</p>
                    <ul className="space-y-1">
                        {result.missing_secrets.map((n) => (
                            <li key={n}><span className="font-mono text-fg">{n}</span> <span className="text-xs text-fg-3">restrict to: {(result.secret_hosts[n] ?? []).join(', ') || 'any host'}</span></li>
                        ))}
                    </ul>
                    <div className="flex gap-4">
                        <Link className="text-accent hover:underline" to="/settings/integrations">Add secrets</Link>
                        <Link className="text-accent hover:underline" to={`/workflows/${result.workflow.id}`}>Open workflow</Link>
                    </div>
                </div>
            ) : (
                <div className="space-y-4">
                    <p className="text-sm text-fg-2">{t.description}</p>
                    {t.params.map((p) => (
                        <div key={p.key}>
                            <label className={modalLabel}>{p.label}
                                <input className={`${modalInput} mt-1.5 font-mono`} placeholder={p.placeholder} value={vals[p.key] ?? ''}
                                    onChange={(e) => setVals((v) => ({ ...v, [p.key]: e.target.value.trim() }))} />
                            </label>
                            {p.help && <p className="text-xs text-fg-3 mt-1">{p.help}</p>}
                        </div>
                    ))}
                    {t.secrets.length > 0 && (
                        <div>
                            <div className={modalLabel}>Required secrets</div>
                            <ul className="text-xs text-fg-2 space-y-0.5">
                                {t.secrets.map((s) => <li key={s.name}><span className="font-mono">{s.name}</span>{s.description && ` — ${s.description}`}</li>)}
                            </ul>
                        </div>
                    )}
                    {t.setup && <div className="copilot-markdown text-sm text-fg-2 border-t border-line pt-3"><ReactMarkdown>{t.setup}</ReactMarkdown></div>}
                    {install.isError && <p role="alert" className="text-xs text-red-300">{errText(install.error, 'Install failed')}</p>}
                </div>
            )}
        </Modal>
    );
}

export default function TemplateGallery() {
    const [picked, setPicked] = useState<WorkflowTemplate | null>(null);
    const { data = [], isLoading } = useQuery({
        queryKey: ['workflow-templates'],
        queryFn: async () => (await api.get('/workflows/templates')).data as WorkflowTemplate[],
    });
    const byVendor = new Map<string, WorkflowTemplate[]>();
    data.forEach((t) => byVendor.set(t.vendor, [...(byVendor.get(t.vendor) ?? []), t]));

    if (isLoading) return <p className="text-sm text-muted">Loading…</p>;
    if (data.length === 0) return <p className="text-sm text-muted py-8 text-center">No templates available</p>;
    return (
        <div className="space-y-6">
            {[...byVendor.entries()].map(([vendor, ts]) => (
                <section key={vendor}>
                    <h2 className="label-mono mb-2">{vendor}</h2>
                    <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
                        {ts.map((t) => (
                            <div key={t.key} className="bg-panel border border-line p-4 flex flex-col gap-2">
                                <div className="flex items-start justify-between gap-2">
                                    <span className="font-medium text-sm text-fg">{t.name}</span>
                                    <span className="label-mono shrink-0">{t.kind}</span>
                                </div>
                                <p className="text-xs text-fg-3 grow">{t.description}</p>
                                <button type="button" className={`${btnSecondary} self-start`} onClick={() => setPicked(t)}>Install</button>
                            </div>
                        ))}
                    </div>
                </section>
            ))}
            {picked && <InstallDialog t={picked} onClose={() => setPicked(null)} />}
        </div>
    );
}
