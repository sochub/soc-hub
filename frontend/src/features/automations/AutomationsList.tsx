import { useState } from 'react';
import { isAxiosError } from 'axios';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Link, useNavigate } from 'react-router-dom';
import { KeyRound, Plus, Workflow as WorkflowIcon } from 'lucide-react';
import { api } from '../../api/client';
import { cn } from '../../lib/utils';
import type { SecretScanItem, User } from '../../types';
import Modal, { btnPrimary, btnSecondary, modalInput } from '../../components/layout/Modal';
import { NAME_RE, errText } from '../integrations/secretsUtil';
import type { WorkflowSummary } from './types';
import { TRIGGER_LABEL } from './nodeCatalog';
import RunsTable, { StatusBadge } from './RunsTable';
import PageContainer from '../../components/layout/PageContainer';

const itemKey = (i: SecretScanItem) => `${i.node_id}|${i.location}|${i.key}`;

function ScanReview({ items, onClose, onDone }: { items: SecretScanItem[]; onClose: () => void; onDone: () => void }) {
    const qc = useQueryClient();
    const [names, setNames] = useState<Record<string, string>>(
        Object.fromEntries(items.map((i) => [`${i.workflow_id}|${itemKey(i)}`, i.suggested_name])));
    const [busy, setBusy] = useState<number | null>(null);
    const [res, setRes] = useState<Record<number, { ok: boolean; text: string }>>({});
    const byWf = new Map<number, SecretScanItem[]>();
    items.forEach((i) => byWf.set(i.workflow_id, [...(byWf.get(i.workflow_id) ?? []), i]));

    const convert = async (wid: number, its: SecretScanItem[]) => {
        setBusy(wid);
        try {
            const r = (await api.post(`/secrets/convert/${wid}`, {
                items: its.map((i) => ({ node_id: i.node_id, location: i.location, key: i.key, secret_name: names[`${wid}|${itemKey(i)}`] })),
            })).data as { created: string[]; reused: string[] };
            setRes((p) => ({ ...p, [wid]: { ok: true, text: `Converted. Created ${r.created.length}, reused ${r.reused.length}.` } }));
            qc.invalidateQueries({ queryKey: ['secrets-scan'] });
            qc.invalidateQueries({ queryKey: ['secrets'] });
            qc.invalidateQueries({ queryKey: ['secret-names'] });
            qc.invalidateQueries({ queryKey: ['workflows'] });
            qc.invalidateQueries({ queryKey: ['workflow', wid] });
            onDone();
        } catch (e) {
            setRes((p) => ({ ...p, [wid]: { ok: false, text: errText(e, 'Convert failed') } }));
        } finally { setBusy(null); }
    };

    return (
        <Modal open onClose={onClose} size="lg" title="Plaintext credentials in workflows"
            footer={<button type="button" className={btnSecondary} onClick={onClose}>Close</button>}>
            <p className="text-xs text-zinc-500 mb-3">Each credential becomes a secret restricted to the request host, and the workflow is rewritten to reference it. Pick the names, then convert per workflow.</p>
            <div className="space-y-4">
                {[...byWf.entries()].map(([wid, its]) => {
                    const valid = its.every((i) => NAME_RE.test(names[`${wid}|${itemKey(i)}`] ?? ''));
                    return (
                        <div key={wid} className="border border-zinc-200">
                            <div className="flex items-center gap-2 px-3 py-2 bg-zinc-50 border-b border-zinc-200">
                                <span className="font-medium text-sm text-zinc-900">{its[0].workflow_name}</span>
                                <button type="button" className={`${btnPrimary} ml-auto !h-8`} disabled={busy !== null || !valid} onClick={() => convert(wid, its)}>
                                    {busy === wid ? 'Converting…' : 'Convert'}</button>
                            </div>
                            <ul className="divide-y divide-zinc-100">
                                {its.map((i) => {
                                    const k = `${wid}|${itemKey(i)}`;
                                    return (
                                        <li key={k} className="px-3 py-2 grid grid-cols-1 sm:grid-cols-[1fr_1fr] gap-2 items-center">
                                            <div className="text-xs text-zinc-600"><span className="font-mono">{i.node_id}</span> · {i.location} <span className="font-mono">{i.key}</span> · <span className="font-mono">{i.host}</span></div>
                                            <input aria-label="Secret name" className={`${modalInput} font-mono !py-1 !text-xs`} value={names[k] ?? ''}
                                                onChange={(e) => setNames((p) => ({ ...p, [k]: e.target.value.toUpperCase().replace(/[^A-Z0-9_]/g, '_') }))} />
                                        </li>
                                    );
                                })}
                            </ul>
                            {res[wid] && <p role="status" className={`px-3 py-2 text-xs ${res[wid].ok ? 'text-emerald-700' : 'text-red-700'}`}>{res[wid].text}</p>}
                        </div>
                    );
                })}
                {items.length === 0 && <p className="text-sm text-zinc-500">Nothing left to convert.</p>}
            </div>
        </Modal>
    );
}

export default function AutomationsList() {
    const qc = useQueryClient();
    const navigate = useNavigate();
    const [tab, setTab] = useState<'workflows' | 'runs'>('workflows');
    const [toggleError, setToggleError] = useState<string | null>(null);

    const { data: me } = useQuery({ queryKey: ['currentUser'], queryFn: async () => (await api.get('/users/me')).data as User, staleTime: 300_000 });
    const isAdmin = me?.role === 'admin' || !!me?.is_super_admin;

    const tenantId = me?.active_tenant_id ?? null;
    const dismissKey = `secrets-scan-dismissed:${tenantId}`;
    const [dismissedKey, setDismissedKey] = useState<string | null>(null);
    const [review, setReview] = useState(false);
    const dismissed = dismissedKey === dismissKey || (() => { try { return localStorage.getItem(dismissKey) === '1'; } catch { return false; } })();
    const { data: scan = [] } = useQuery({
        queryKey: ['secrets-scan', tenantId],
        queryFn: async () => (await api.get('/secrets/scan')).data as SecretScanItem[],
        enabled: isAdmin && !!me,
    });
    const scanWfCount = new Set(scan.map((i) => i.workflow_id)).size;

    const { data: workflows = [], isLoading } = useQuery({
        queryKey: ['workflows'],
        queryFn: async () => (await api.get('/workflows/')).data as WorkflowSummary[],
    });

    const toggle = useMutation({
        mutationFn: async (w: WorkflowSummary) => api.post(`/workflows/${w.id}/${w.enabled ? 'disable' : 'enable'}`),
        onMutate: () => setToggleError(null),
        onSuccess: () => { qc.invalidateQueries({ queryKey: ['workflows'] }); },
        onError: (err, w) => {
            const detail = isAxiosError(err) ? err.response?.data?.detail : undefined;
            const errors = err && isAxiosError(err) && err.response?.status === 422 ? detail?.errors : undefined;
            const action = w.enabled ? 'disable' : 'enable';
            if (Array.isArray(errors)) {
                setToggleError(`Can't ${action} "${w.name}": ${errors.map((e: { message: string }) => e.message).join('; ')}`);
            } else {
                setToggleError(typeof detail === 'string' ? detail : `Could not update "${w.name}"`);
            }
        },
    });

    return (
        <PageContainer className="space-y-0">
            <div className="flex items-end justify-between mb-5 flex-wrap gap-3">
                <div>
                    <h1 className="text-xl font-semibold tracking-tight text-zinc-900">Automations</h1>
                    <p className="label-mono mt-1">workflows for cases and alerts</p>
                </div>
                {isAdmin && (
                    <button onClick={() => navigate('/automations/new')}
                        className="inline-flex items-center gap-1.5 h-9 px-3.5 bg-accent-600 text-white text-sm font-medium hover:bg-accent-700">
                        <Plus size={15} /> New workflow
                    </button>
                )}
            </div>

            <div className="flex gap-4 border-b border-zinc-200 mb-4">
                {(['workflows', 'runs'] as const).map((t) => (
                    <button key={t} onClick={() => setTab(t)}
                        className={cn('pb-2 text-sm capitalize', tab === t ? 'text-accent-700 border-b-2 border-accent-600' : 'text-zinc-500 hover:text-zinc-800')}>{t}</button>
                ))}
            </div>

            {isAdmin && scan.length > 0 && !dismissed && (
                <div className="mb-3 px-3 py-2 text-sm border border-amber-200 bg-amber-50 text-amber-900 flex items-center gap-3 flex-wrap">
                    <KeyRound size={15} />
                    <span>{scanWfCount} workflow{scanWfCount === 1 ? '' : 's'} contain{scanWfCount === 1 ? 's' : ''} plaintext credentials — move them to Secrets</span>
                    <button onClick={() => setReview(true)} className="ml-auto h-8 px-3 border border-amber-300 bg-white text-sm hover:bg-amber-100">Review</button>
                    <button onClick={() => { try { localStorage.setItem(dismissKey, '1'); } catch { /* storage unavailable */ } setDismissedKey(dismissKey); }}
                        className="h-8 px-3 text-sm text-amber-800 hover:underline">Dismiss</button>
                </div>
            )}
            {review && <ScanReview items={scan} onClose={() => setReview(false)} onDone={() => undefined} />}

            {toggleError && <div role="alert" className="mb-3 px-3 py-2 text-sm border border-red-200 bg-red-50 text-red-700">{toggleError}</div>}

            {tab === 'runs' ? <RunsTable /> : (
                <div className="bg-white border border-zinc-200">
                    <table className="w-full text-sm">
                        <thead className="bg-zinc-50 text-left label-mono">
                            <tr><th className="px-4 py-2">Name</th><th className="px-4 py-2">Trigger</th><th className="px-4 py-2">Enabled</th>
                                <th className="px-4 py-2">Last run</th><th className="px-4 py-2">Runs</th></tr>
                        </thead>
                        <tbody className="divide-y divide-zinc-100">
                            {isLoading && <tr><td colSpan={5} className="px-4 py-8 text-center text-zinc-400">Loading…</td></tr>}
                            {!isLoading && workflows.length === 0 && (
                                <tr><td colSpan={5} className="px-4 py-12 text-center text-zinc-400">
                                    <WorkflowIcon className="mx-auto mb-2 opacity-50" />No workflows yet
                                </td></tr>
                            )}
                            {workflows.map((w) => (
                                <tr key={w.id} className="hover:bg-zinc-50">
                                    <td className="px-4 py-2.5">
                                        <Link to={`/automations/${w.id}`} className="font-medium text-zinc-900 hover:text-accent-700">{w.name}</Link>
                                        {w.description && <div className="text-xs text-zinc-500">{w.description}</div>}
                                    </td>
                                    <td className="px-4 py-2.5 font-mono text-xs text-zinc-600">{TRIGGER_LABEL[w.trigger_type]}</td>
                                    <td className="px-4 py-2.5">
                                        <button type="button" role="switch" aria-checked={w.enabled} aria-label={`${w.name} enabled`}
                                            title={isAdmin ? undefined : 'Only admins can enable workflows'}
                                            disabled={!isAdmin || toggle.isPending} onClick={() => toggle.mutate(w)}
                                            className={cn('w-9 h-5 relative transition-colors disabled:opacity-50', w.enabled ? 'bg-accent-600' : 'bg-zinc-300')}>
                                            <span className={cn('absolute top-0.5 w-4 h-4 bg-white transition-all', w.enabled ? 'left-[18px]' : 'left-0.5')} />
                                        </button>
                                    </td>
                                    <td className="px-4 py-2.5 text-xs text-zinc-500">
                                        {w.last_run_status ? <span className="flex items-center gap-2"><StatusBadge status={w.last_run_status} />
                                            {w.last_run_at && new Date(w.last_run_at).toLocaleString()}</span> : '—'}
                                    </td>
                                    <td className="px-4 py-2.5 num">{w.run_count}</td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            )}
        </PageContainer>
    );
}
