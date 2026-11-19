import { useState } from 'react';
import { isAxiosError } from 'axios';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import { KeyRound, Plus } from 'lucide-react';
import { api } from '../../api/client';
import type { SecretScanItem, User } from '../../types';
import Modal, { btnPrimary, btnSecondary, modalInput } from '../../components/layout/Modal';
import { NAME_RE, errText } from '../integrations/secretsUtil';
import type { WorkflowSummary } from './types';
import { TRIGGER_LABEL } from './nodeCatalog';
import TemplateGallery from './TemplateGallery';
import RunsTable, { RelTime, StatusBadge } from './RunsTable';
import EnabledSwitch from './EnabledSwitch';
import PageContainer from '../../components/layout/PageContainer';
import { Button, EmptyState, ErrorState, FolderTabs, PageHeader, Skeleton, TONE_TEXT } from '../../components/ui';

const itemKey = (i: SecretScanItem) => `${i.node_id}|${i.location}|${i.key}`;

function ScanReview({ items, onClose, onDone }: { items: SecretScanItem[]; onClose: () => void; onDone: () => void }) {
    const qc = useQueryClient();
    const [names, setNames] = useState<Record<string, string>>(
        Object.fromEntries(items.map((i) => [`${i.workflow_id}|${itemKey(i)}`, i.suggested_name])));
    const [busy, setBusy] = useState<number | null>(null);
    const [res, setRes] = useState<Record<number, { ok: boolean; text: string }>>({});
    const [status, setStatus] = useState<{ ok: boolean; text: string } | null>(null);
    const byWf = new Map<number, SecretScanItem[]>();
    items.forEach((i) => byWf.set(i.workflow_id, [...(byWf.get(i.workflow_id) ?? []), i]));

    const convert = async (wid: number, its: SecretScanItem[]) => {
        setBusy(wid);
        try {
            const r = (await api.post(`/secrets/convert/${wid}`, {
                items: its.map((i) => ({ node_id: i.node_id, location: i.location, key: i.key, secret_name: names[`${wid}|${itemKey(i)}`] })),
            })).data as { created: string[]; reused: string[] };
            const text = `Converted ${its[0].workflow_name}: created ${r.created.length}, reused ${r.reused.length}.`;
            setRes((p) => ({ ...p, [wid]: { ok: true, text } })); setStatus({ ok: true, text });
            qc.invalidateQueries({ queryKey: ['secrets-scan'] });
            qc.invalidateQueries({ queryKey: ['secrets'] });
            qc.invalidateQueries({ queryKey: ['secret-names'] });
            qc.invalidateQueries({ queryKey: ['workflows'] });
            qc.invalidateQueries({ queryKey: ['workflow', wid] });
            onDone();
        } catch (e) {
            const text = errText(e, 'Convert failed');
            setRes((p) => ({ ...p, [wid]: { ok: false, text } })); setStatus({ ok: false, text: `${its[0].workflow_name}: ${text}` });
        } finally { setBusy(null); }
    };

    return (
        <Modal open onClose={onClose} size="lg" title="Plaintext credentials in workflows"
            footer={<button type="button" className={btnSecondary} onClick={onClose}>Close</button>}>
            <p className="text-xs text-fg-3 mb-3">Each credential becomes a secret restricted to the request host, and the workflow is rewritten to reference it. Pick the names, then convert per workflow.</p>
            {status && <p role="status" className={`mb-3 text-xs ${status.ok ? TONE_TEXT.ok : 'text-severity-critical'}`}>{status.text}</p>}
            <div className="space-y-4">
                {[...byWf.entries()].map(([wid, its]) => {
                    const todo = its.filter((i) => i.convertible);
                    const valid = todo.length > 0 && todo.every((i) => NAME_RE.test(names[`${wid}|${itemKey(i)}`] ?? ''));
                    return (
                        <div key={wid} className="rounded-md border border-line overflow-hidden">
                            <div className="flex items-center gap-2 px-3 py-2 bg-surface border-b border-line">
                                <span className="font-medium text-sm text-fg">{its[0].workflow_name}</span>
                                <button type="button" className={`${btnPrimary} ml-auto h-8!`} disabled={busy !== null || !valid} onClick={() => convert(wid, todo)}>
                                    {busy === wid ? 'Converting…' : 'Convert'}</button>
                            </div>
                            <ul className="divide-y divide-line">
                                {its.map((i) => {
                                    const k = `${wid}|${itemKey(i)}`;
                                    return (
                                        <li key={k} className={`px-3 py-2 grid grid-cols-1 sm:grid-cols-[1fr_1fr] gap-2 items-center ${i.convertible ? '' : 'bg-surface'}`}>
                                            <div className="text-xs text-fg-2"><span className="font-mono">{i.node_id}</span> · {i.location} <span className="font-mono">{i.key}</span> · <span className="font-mono">{i.host}</span>
                                                {!i.convertible && <div className="mt-0.5 text-severity-medium">Not convertible: {i.reason ?? 'host not supported'}</div>}</div>
                                            <input aria-label="Secret name" disabled={!i.convertible} title={i.convertible ? undefined : i.reason}
                                                className={`${modalInput} font-mono py-1! text-xs! disabled:opacity-50 disabled:cursor-not-allowed`} value={names[k] ?? ''}
                                                onChange={(e) => setNames((p) => ({ ...p, [k]: e.target.value.toUpperCase().replace(/[^A-Z0-9_]/g, '_') }))} />
                                        </li>
                                    );
                                })}
                            </ul>
                            {res[wid] && <p role="status" className={`px-3 py-2 text-xs ${res[wid].ok ? TONE_TEXT.ok : 'text-severity-critical'}`}>{res[wid].text}</p>}
                        </div>
                    );
                })}
                {items.length === 0 && <p className="text-sm text-fg-3">Nothing left to convert.</p>}
            </div>
        </Modal>
    );
}

type ListTab = 'workflows' | 'templates' | 'runs';

export default function AutomationsList() {
    const qc = useQueryClient();
    const navigate = useNavigate();
    const [params, setParams] = useSearchParams();
    const [toggleError, setToggleError] = useState<string | null>(null);

    const { data: me } = useQuery({ queryKey: ['currentUser'], queryFn: async () => (await api.get('/users/me')).data as User, staleTime: 300_000 });
    const isAdmin = me?.role === 'admin' || !!me?.is_super_admin;
    const tabs: ListTab[] = isAdmin ? ['workflows', 'templates', 'runs'] : ['workflows', 'runs'];
    const rawTab = params.get('tab') as ListTab | null;
    const tab: ListTab = rawTab && tabs.includes(rawTab) ? rawTab : 'workflows';
    const setTab = (t: ListTab) => {
        const next = new URLSearchParams(params);
        if (t === 'workflows') next.delete('tab'); else next.set('tab', t);
        setParams(next, { replace: true });
    };

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

    const { data: workflows = [], isLoading, isError, refetch } = useQuery({
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

    const tabLabel: Record<ListTab, string> = { workflows: 'Workflows', templates: 'Templates', runs: 'Runs' };

    return (
        <PageContainer className="space-y-0">
            <PageHeader title="Workflows" description="Automations that run on cases and alerts."
                actions={isAdmin && <Button onClick={() => navigate('/workflows/new')}><Plus size={15} aria-hidden /> New workflow</Button>} />

            <FolderTabs items={tabs.map((t) => ({ value: t, label: tabLabel[t] }))} value={tab} onChange={setTab}
                label="Workflow views" idPrefix="workflows-tab" />

            <div id="workflows-tab-panel" role="tabpanel" aria-labelledby={`workflows-tab-${tab}`} className="pt-4 space-y-3">
            {isAdmin && scan.length > 0 && !dismissed && (
                <div className="rounded-md px-3 py-2 text-sm border border-severity-medium/30 bg-severity-medium/5 text-severity-medium flex items-center gap-3 flex-wrap">
                    <KeyRound size={15} aria-hidden />
                    <span>{scanWfCount} workflow{scanWfCount === 1 ? '' : 's'} contain{scanWfCount === 1 ? 's' : ''} plaintext credentials. Move them to Secrets.</span>
                    <Button variant="secondary" size="sm" className="ml-auto" onClick={() => setReview(true)}>Review</Button>
                    <Button variant="ghost" size="sm"
                        onClick={() => { try { localStorage.setItem(dismissKey, '1'); } catch { /* storage unavailable */ } setDismissedKey(dismissKey); }}>Dismiss</Button>
                </div>
            )}
            {review && <ScanReview items={scan} onClose={() => setReview(false)} onDone={() => undefined} />}

            {toggleError && <ErrorState message={toggleError} />}

            {tab === 'runs' ? <RunsTable /> : tab === 'templates' && isAdmin ? <TemplateGallery /> : isLoading ? <Skeleton rows={4} /> : isError ? (
                <ErrorState message="Could not load workflows." onRetry={() => refetch()} />
            ) : workflows.length === 0 ? (
                <EmptyState title="No workflows yet"
                    body="Workflows run steps automatically when a case or alert changes, on a schedule, or on demand."
                    action={isAdmin ? <>
                        <Button variant="secondary" onClick={() => setTab('templates')}>Start from a template</Button>
                        <Button onClick={() => navigate('/workflows/new')}><Plus size={15} aria-hidden /> New workflow</Button>
                    </> : undefined} />
            ) : (
                <div className="rounded-md bg-panel border border-line overflow-x-auto">
                    <table className="w-full text-sm">
                        <thead className="text-left label-caps">
                            <tr><th className="px-3 py-2">Name</th><th className="px-3 py-2">Trigger</th><th className="px-3 py-2">Enabled</th>
                                <th className="px-3 py-2">Last run</th><th className="px-3 py-2">Runs</th></tr>
                        </thead>
                        <tbody className="divide-y divide-line">
                            {workflows.map((w) => (
                                <tr key={w.id} className="hover:bg-raised">
                                    <td className="px-3 py-2.5">
                                        <Link to={`/workflows/${w.id}`} className="font-medium text-fg hover:text-accent">{w.name}</Link>
                                        {w.description && <div className="text-xs text-fg-3">{w.description}</div>}
                                    </td>
                                    <td className="px-3 py-2.5 text-xs text-fg-2">{TRIGGER_LABEL[w.trigger_type]}</td>
                                    <td className="px-3 py-2.5">
                                        <EnabledSwitch checked={w.enabled} label={`${w.name} enabled`}
                                            title={isAdmin ? undefined : 'Only admins can enable workflows'}
                                            disabled={!isAdmin || toggle.isPending} onClick={() => toggle.mutate(w)} />
                                    </td>
                                    <td className="px-3 py-2.5 text-xs text-fg-3">
                                        {w.last_run_status ? <span className="flex items-center gap-2"><StatusBadge status={w.last_run_status} />
                                            {w.last_run_at && <RelTime iso={w.last_run_at} />}</span> : '—'}
                                    </td>
                                    <td className="px-3 py-2.5 num">{w.run_count}</td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            )}
            </div>
        </PageContainer>
    );
}
