import { Fragment, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '../../api/client';
import { ChevronDown, ChevronRight, Plus, Send, Trash2, X } from 'lucide-react';
import type { IOC } from '../../types';
import EnrichmentPanel from '../enrichment/EnrichmentPanel';
import { useCanRun } from '../enrichment/useCanRun';
import { Button, Input, SeverityStamp, Stamp, Skeleton, ErrorState, EmptyState, useConfirm, verdictTone, tlpTone, TONE_VAR, type Tone } from '../../components/ui';
import Modal, { modalInput, modalLabel, btnPrimary, btnSecondary } from '../../components/layout/Modal';

const IOC_TYPES = ['ip_address', 'domain', 'url', 'file_hash', 'email', 'registry_key', 'mutex', 'user_agent', 'other'];
const THREAT_LEVELS = ['critical', 'high', 'medium', 'low', 'info'];
const STATUSES = ['active', 'expired', 'whitelisted', 'false_positive'];
const TLP_LEVELS = ['white', 'green', 'amber', 'red'];

const iocStatusTone = (status: string): Tone => {
    switch (status) {
        case 'active': return 'accent';
        case 'whitelisted': return 'ok';
        default: return 'neutral';
    }
};

const SKIP_LABEL: Record<string, string> = {
    tlp_red: 'TLP:RED — not shared',
    unmappable: 'type not supported by MISP',
    unmappable_hash: 'type not supported by MISP',
};

type MispPushResult = { event_uuid: string; event_url: string; pushed: number[]; skipped: { id: number; reason: string }[] };

const defaultForm = {
    ioc_type: 'ip_address',
    value: '',
    threat_level: 'medium',
    confidence: 50,
    status: 'active',
    tlp: 'amber',
    source: '',
    description: '',
};

export default function IOCList() {
    const queryClient = useQueryClient();
    const [searchParams, setSearchParams] = useSearchParams();
    const [showModal, setShowModal] = useState(false);
    const [form, setForm] = useState(defaultForm);
    const [openId, setOpenId] = useState<number | null>(null);
    const canRun = useCanRun();
    const [selected, setSelected] = useState<Set<number>>(new Set());
    const [mispResult, setMispResult] = useState<MispPushResult | null>(null);
    const [mispError, setMispError] = useState<string | null>(null);

    const { confirm, dialog } = useConfirm();
    const q = searchParams.get('q') ?? '';
    const { data: iocs, isLoading, isError, refetch } = useQuery<IOC[]>({
        queryKey: ['iocs'],
        queryFn: async () => {
            const res = await api.get('/iocs/');
            return res.data;
        },
    });

    const createMutation = useMutation({
        mutationFn: async (data: typeof defaultForm) => {
            const res = await api.post('/iocs/', data);
            return res.data;
        },
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['iocs'] });
            queryClient.invalidateQueries({ queryKey: ['stats'] });
            setShowModal(false);
            setForm(defaultForm);
        },
    });

    const deleteMutation = useMutation({
        mutationFn: async (id: number) => {
            await api.delete(`/iocs/${id}`);
        },
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['iocs'] });
            queryClient.invalidateQueries({ queryKey: ['stats'] });
        },
    });

    const typeFilter = searchParams.get('type'); // set by the Overview "Indicators" chart
    const visible = (iocs ?? []).filter(i => i.value.toLowerCase().includes(q.toLowerCase()) && (!typeFilter || i.ioc_type === typeFilter));
    const selectedIocs = (iocs ?? []).filter(i => selected.has(i.id));
    const selectedCaseIds = new Set(selectedIocs.map(i => i.case_id));
    const singleCaseId = selectedIocs.length > 0 && selectedCaseIds.size === 1 ? selectedIocs[0].case_id : null;
    const allSelected = visible.length > 0 && visible.every(i => selected.has(i.id));
    const toggle = (id: number) => setSelected(prev => {
        const next = new Set(prev);
        if (next.has(id)) next.delete(id); else next.add(id);
        return next;
    });
    const toggleAll = () => setSelected(allSelected ? new Set() : new Set(visible.map(i => i.id)));

    const mispPush = useMutation({
        mutationFn: async ({ caseId, ids }: { caseId: number; ids: number[] }) =>
            (await api.post(`/cases/${caseId}/misp-push`, { ioc_ids: ids })).data as MispPushResult,
        onMutate: () => { setMispResult(null); setMispError(null); },
        onSuccess: (data) => { setMispResult(data); setSelected(new Set()); },
        onError: (err: any) => {
            const status = err?.response?.status;
            const detail = err?.response?.data?.detail;
            if (status === 409 && detail === 'misp_not_configured') {
                setMispError('Connect MISP under Settings → Integrations first.');
            } else if (status === 502 && detail && typeof detail === 'object') {
                const n = Array.isArray(detail.pushed_ids) ? detail.pushed_ids.length : 0;
                setMispError(`${detail.message || 'MISP push failed.'}${n > 0 ? ` ${n} were pushed before the failure.` : ''}`);
            } else {
                setMispError('Could not send to MISP.');
            }
        },
    });

    return (
        <div className="p-4 space-y-4">
            <div className="flex flex-wrap items-center gap-2">
                <p className="mr-auto text-sm text-fg-3">
                    {isLoading ? '\u00a0' : <><span className="num">{q || typeFilter ? `${visible.length} of ${iocs?.length ?? 0}` : iocs?.length ?? 0}</span> indicators tracked across all cases.</>}
                </p>
                {typeFilter && (
                    <button type="button" onClick={() => setSearchParams(p => { p.delete('type'); return p; }, { replace: true })}
                        className="inline-flex items-center gap-1 h-7 px-2 rounded-sm border border-accent/40 bg-accent/10 text-xs text-fg hover:border-accent"
                        aria-label={`Remove filter: type ${typeFilter}`}>
                        Type: {typeFilter.replace(/_/g, ' ')}<X size={12} aria-hidden />
                    </button>
                )}
                <Input type="search" inputSize="sm" className="w-56" aria-label="Filter by value" value={q} placeholder="Filter by value…"
                    onChange={e => setSearchParams(p => { if (e.target.value) p.set('q', e.target.value); else p.delete('q'); return p; }, { replace: true })} />
                <Button size="sm" onClick={() => setShowModal(true)}>
                    <Plus size={14} aria-hidden />
                    Add IOC
                </Button>
            </div>

            {canRun && selected.size > 0 && (
                <div className="flex flex-wrap items-center gap-2 rounded-md border border-line bg-raised px-3 py-2 text-sm">
                    <span className="text-fg-2 num">{selected.size} selected</span>
                    <Button
                        size="sm" variant="secondary"
                        onClick={() => singleCaseId != null && mispPush.mutate({ caseId: singleCaseId, ids: selectedIocs.map(i => i.id) })}
                        disabled={singleCaseId == null || mispPush.isPending}
                        title={singleCaseId == null ? 'Select IOCs from a single case' : 'Send selected IOCs to MISP'}
                    >
                        <Send size={12} aria-hidden />
                        {mispPush.isPending ? 'Sending…' : 'Send to MISP'}
                    </Button>
                    <Button size="sm" variant="ghost" className="ml-auto" onClick={() => setSelected(new Set())}>Clear</Button>
                </div>
            )}
            {mispError && <ErrorState message={mispError} />}
            {mispResult && (
                <div role="status" className="rounded-md border border-line bg-panel px-4 py-3 text-sm text-fg-2 space-y-1">
                    <div className="flex items-start gap-2">
                        <Stamp tone="ok">Sent</Stamp>
                        <p className="mr-auto">
                            <span className="num">{mispResult.pushed.length}</span> IOC{mispResult.pushed.length === 1 ? '' : 's'} pushed to MISP.{' '}
                            <a href={mispResult.event_url} target="_blank" rel="noopener noreferrer" className="text-accent underline">Open event</a>
                        </p>
                        <button type="button" onClick={() => setMispResult(null)} aria-label="Dismiss" className="text-fg-3 hover:text-fg"><X size={14} /></button>
                    </div>
                    {mispResult.skipped.length > 0 && (
                        <ul className="text-xs text-severity-medium list-disc pl-5">
                            {mispResult.skipped.map(s => (
                                <li key={s.id}>
                                    {iocs?.find(i => i.id === s.id)?.value ?? `IOC #${s.id}`} — {SKIP_LABEL[s.reason] ?? s.reason}
                                </li>
                            ))}
                        </ul>
                    )}
                </div>
            )}

            {isError ? <ErrorState message="Could not load IOCs." onRetry={() => refetch()} /> : isLoading ? <Skeleton rows={8} /> : !iocs || iocs.length === 0 ? (
                <EmptyState title="No IOCs recorded yet" body="Add one to get started, or promote an artifact from a case."
                    action={<Button size="sm" onClick={() => setShowModal(true)}><Plus size={14} aria-hidden />Add IOC</Button>} />
            ) : visible.length === 0 ? (
                <EmptyState title="No IOCs match this filter"
                    action={<Button size="sm" variant="secondary" onClick={() => setSearchParams({}, { replace: true })}>Clear filter</Button>} />
            ) : (
                    <div className="-mx-4 overflow-x-auto border-y border-line">
                        <table className="w-full text-sm">
                            <thead>
                                <tr className="border-b border-line bg-surface label-caps">
                                    {canRun && (
                                        <th className="px-3 py-2 w-8">
                                            <input type="checkbox" checked={allSelected} onChange={toggleAll} aria-label="Select all IOCs" />
                                        </th>
                                    )}
                                    <th className="px-3 py-2 w-8"><span className="sr-only">Threat intel</span></th>
                                    <th className="px-3 py-2 text-left">Type</th>
                                    <th className="px-3 py-2 text-left">Value</th>
                                    <th className="px-3 py-2 text-left">Threat level</th>
                                    <th className="px-3 py-2 text-left">Confidence</th>
                                    <th className="px-3 py-2 text-left">Status</th>
                                    <th className="px-3 py-2 text-left">TLP</th>
                                    <th className="px-3 py-2 text-left">Source</th>
                                    <th className="px-3 py-2 text-left">Created</th>
                                    <th className="px-3 py-2 text-right">Actions</th>
                                </tr>
                            </thead>
                            <tbody className="divide-y divide-line">
                                {visible.map((ioc) => (
                                    <Fragment key={ioc.id}>
                                    <tr className="hover:bg-raised transition-colors group">
                                        {canRun && (
                                            <td className="px-3 py-2.5">
                                                <input type="checkbox" checked={selected.has(ioc.id)} onChange={() => toggle(ioc.id)} aria-label={`Select ${ioc.value}`} />
                                            </td>
                                        )}
                                        <td className="px-3 py-2.5">
                                            <button onClick={() => setOpenId(openId === ioc.id ? null : ioc.id)} className="text-muted hover:text-fg-2" title="Threat intel" aria-expanded={openId === ioc.id}
                                                aria-label={`${openId === ioc.id ? 'Hide' : 'Show'} threat intel for ${ioc.value}`}>
                                                {openId === ioc.id ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                                            </button>
                                        </td>
                                        <td className="px-3 py-2.5">
                                            <span className="font-mono text-xs text-fg-3 bg-raised px-2 py-0.5 rounded-sm">
                                                {ioc.ioc_type.replace('_', ' ')}
                                            </span>
                                        </td>
                                        <td className="px-3 py-2.5 font-mono text-fg max-w-[200px] truncate" title={ioc.value}>
                                            {ioc.enrichment_verdict && verdictTone(ioc.enrichment_verdict) !== 'neutral' && (
                                                <span title={ioc.enrichment_verdict} className="inline-block w-2 h-2 rounded-full mr-2 align-middle"
                                                    style={{ background: TONE_VAR[verdictTone(ioc.enrichment_verdict)] }}>
                                                    <span className="sr-only">{ioc.enrichment_verdict}</span>
                                                </span>
                                            )}
                                            {ioc.value}
                                        </td>
                                        <td className="px-3 py-2.5">
                                            <SeverityStamp severity={ioc.threat_level} />
                                        </td>
                                        <td className="px-3 py-2.5">
                                            <div className="flex items-center gap-2">
                                                <div className="w-16 h-1.5 bg-raised rounded-full overflow-hidden">
                                                    <div
                                                        className="h-full bg-accent rounded-full"
                                                        style={{ width: `${ioc.confidence}%` }}
                                                    />
                                                </div>
                                                <span className="text-xs text-fg-3 num">{ioc.confidence}%</span>
                                            </div>
                                        </td>
                                        <td className="px-3 py-2.5">
                                            <Stamp tone={iocStatusTone(ioc.status)}>{ioc.status.replace('_', ' ')}</Stamp>
                                        </td>
                                        <td className="px-3 py-2.5">
                                            <Stamp tone={tlpTone(ioc.tlp)}>TLP:{ioc.tlp}</Stamp>
                                        </td>
                                        <td className="px-3 py-2.5 text-fg-3 text-xs">{ioc.source || '—'}</td>
                                        <td className="px-3 py-2.5 text-fg-3 text-xs num">
                                            {new Date(ioc.created_at).toLocaleDateString()}
                                        </td>
                                        <td className="px-3 py-2.5 text-right">
                                            <Button
                                                variant="ghost" size="icon"
                                                onClick={async () => {
                                                    if (await confirm({ title: 'Delete this IOC?', body: <>“<span className="font-mono">{ioc.value}</span>” will be deleted. This can't be undone.</>, danger: true, confirmLabel: 'Delete IOC' })) {
                                                        deleteMutation.mutate(ioc.id);
                                                    }
                                                }}
                                                className="hover:text-severity-critical opacity-0 group-hover:opacity-100 group-focus-within:opacity-100 [@media(hover:none)]:opacity-100"
                                                aria-label={`Delete IOC ${ioc.value}`} title="Delete IOC"
                                            >
                                                <Trash2 size={14} />
                                            </Button>
                                        </td>
                                    </tr>
                                    {openId === ioc.id && (
                                        <tr className="bg-surface">
                                            <td colSpan={canRun ? 2 : 1}></td>
                                            <td colSpan={9} className="px-4 py-3"><EnrichmentPanel kind="ioc" id={ioc.id} canRun={canRun} ioc={ioc} /></td>
                                        </tr>
                                    )}
                                    </Fragment>
                                ))}
                            </tbody>
                        </table>
                    </div>
            )}

            {/* Add IOC Modal */}
            <Modal
                open={showModal}
                onClose={() => setShowModal(false)}
                title="Add indicator of compromise"
                size="lg"
                footer={<>
                    <button onClick={() => setShowModal(false)} className={btnSecondary}>
                        Cancel
                    </button>
                    <button
                        onClick={() => createMutation.mutate(form)}
                        disabled={!form.value.trim() || createMutation.isPending}
                        className={btnPrimary}
                    >
                        {createMutation.isPending ? 'Adding...' : 'Add IOC'}
                    </button>
                </>}
            >
                <div className="space-y-4">
                    <div className="grid grid-cols-2 gap-3">
                        <div>
                            <label htmlFor="ioc-type" className={modalLabel}>Type</label>
                            <select
                            id="ioc-type"
                                value={form.ioc_type}
                                onChange={(e) => setForm({ ...form, ioc_type: e.target.value })}
                                className={modalInput}
                            >
                                {IOC_TYPES.map(t => <option key={t} value={t}>{t.replace(/_/g, ' ')}</option>)}
                            </select>
                        </div>
                        <div>
                            <label htmlFor="ioc-threat" className={modalLabel}>Threat level</label>
                            <select
                            id="ioc-threat"
                                value={form.threat_level}
                                onChange={(e) => setForm({ ...form, threat_level: e.target.value })}
                                className={modalInput}
                            >
                                {THREAT_LEVELS.map(t => <option key={t} value={t}>{t}</option>)}
                            </select>
                        </div>
                    </div>

                    <div>
                        <label htmlFor="ioc-value" className={modalLabel}>Value</label>
                        <input
                            id="ioc-value"
                            type="text"
                            value={form.value}
                            onChange={(e) => setForm({ ...form, value: e.target.value })}
                            placeholder="e.g. 192.168.1.1, malicious.com, sha256..."
                            className={`${modalInput} font-mono`}
                        />
                    </div>

                    <div className="grid grid-cols-3 gap-3">
                        <div>
                            <label htmlFor="ioc-confidence" className={modalLabel}>Confidence ({form.confidence}%)</label>
                            <input
                            id="ioc-confidence"
                                type="number"
                                min={0}
                                max={100}
                                value={form.confidence}
                                onChange={(e) => setForm({ ...form, confidence: Number(e.target.value) })}
                                className={modalInput}
                            />
                        </div>
                        <div>
                            <label htmlFor="ioc-status" className={modalLabel}>Status</label>
                            <select
                            id="ioc-status"
                                value={form.status}
                                onChange={(e) => setForm({ ...form, status: e.target.value })}
                                className={modalInput}
                            >
                                {STATUSES.map(s => <option key={s} value={s}>{s.replace('_', ' ')}</option>)}
                            </select>
                        </div>
                        <div>
                            <label htmlFor="ioc-tlp" className={modalLabel}>TLP</label>
                            <select
                            id="ioc-tlp"
                                value={form.tlp}
                                onChange={(e) => setForm({ ...form, tlp: e.target.value })}
                                className={modalInput}
                            >
                                {TLP_LEVELS.map(t => <option key={t} value={t}>TLP:{t}</option>)}
                            </select>
                        </div>
                    </div>

                    <div>
                        <label htmlFor="ioc-source" className={modalLabel}>Source</label>
                        <input
                            id="ioc-source"
                            type="text"
                            value={form.source}
                            onChange={(e) => setForm({ ...form, source: e.target.value })}
                            placeholder="e.g. VirusTotal, internal, OSINT..."
                            className={modalInput}
                        />
                    </div>

                    <div>
                        <label htmlFor="ioc-desc" className={modalLabel}>Description</label>
                        <textarea
                            id="ioc-desc"
                            value={form.description}
                            onChange={(e) => setForm({ ...form, description: e.target.value })}
                            placeholder="Additional context..."
                            rows={2}
                            className={`${modalInput} resize-none`}
                        />
                    </div>
                </div>
            </Modal>
            {dialog}
        </div>
    );
}
