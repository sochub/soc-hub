import { Fragment, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '../../api/client';
import { AlertOctagon, ChevronDown, ChevronRight, Plus, Trash2 } from 'lucide-react';
import { cn } from '../../lib/utils';
import type { IOC } from '../../types';
import EnrichmentPanel from '../enrichment/EnrichmentPanel';
import { useCanRun } from '../enrichment/useCanRun';
import PageContainer from '../../components/layout/PageContainer';
import Modal, { modalInput, modalLabel, btnPrimary, btnSecondary } from '../../components/layout/Modal';

const VERDICT_DOT: Record<string, string> = { malicious: 'bg-red-600', suspicious: 'bg-amber-500', harmless: 'bg-green-600' };

const IOC_TYPES = ['ip_address', 'domain', 'url', 'file_hash', 'email', 'registry_key', 'mutex', 'user_agent', 'other'];
const THREAT_LEVELS = ['critical', 'high', 'medium', 'low', 'info'];
const STATUSES = ['active', 'expired', 'whitelisted', 'false_positive'];
const TLP_LEVELS = ['white', 'green', 'amber', 'red'];

const threatLevelBadge = (level: string) => {
    const map: Record<string, string> = {
        critical: 'bg-red-500/10 text-red-300 border-red-500/30',
        high: 'bg-orange-500/10 text-orange-300 border-orange-500/30',
        medium: 'bg-yellow-500/10 text-yellow-300 border-yellow-500/30',
        low: 'bg-accent/10 text-accent border-accent/40',
        info: 'bg-raised text-fg-3 border-line',
    };
    return map[level] ?? map.info;
};

const statusBadge = (status: string) => {
    const map: Record<string, string> = {
        active: 'bg-emerald-500/10 text-emerald-300 border-emerald-500/30',
        expired: 'bg-raised text-fg-3 border-line',
        whitelisted: 'bg-accent/10 text-accent border-accent/40',
        false_positive: 'bg-yellow-500/10 text-yellow-300 border-yellow-500/30',
    };
    return map[status] ?? map.expired;
};

const tlpBadge = (tlp: string) => {
    const map: Record<string, string> = {
        white: 'bg-raised text-fg-2 border-line-strong',
        green: 'bg-green-500/10 text-green-300 border-green-500/30',
        amber: 'bg-amber-500/10 text-amber-300 border-amber-500/30',
        red: 'bg-red-500/10 text-red-300 border-red-500/30',
    };
    return map[tlp] ?? map.amber;
};

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

    const { data: iocs, isLoading } = useQuery<IOC[]>({
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

    return (
        <PageContainer>
            {/* Header */}
            <div className="flex items-center justify-between">
                <div>
                    <h1 className="text-2xl font-bold text-fg tracking-tight flex items-center gap-3">
                        <AlertOctagon size={24} className="text-magenta" />
                        Indicators of Compromise
                    </h1>
                    <p className="text-fg-3 text-sm mt-1">Track and manage threat indicators across all cases.</p>
                </div>
                <div className="flex items-center gap-2">
                <input type="search" value={searchParams.get('q') ?? ''} placeholder="Filter by value…"
                    onChange={e => setSearchParams(e.target.value ? { q: e.target.value } : {}, { replace: true })}
                    className="w-56 bg-surface border border-line rounded-full px-3 py-1.5 text-sm text-fg placeholder:text-muted focus:outline-hidden focus:ring-1 focus:ring-accent" />
                <button
                    onClick={() => setShowModal(true)}
                    className="flex items-center gap-2 bg-brand-gradient hover:opacity-90 px-4 py-2 rounded-full text-sm font-semibold transition-all"
                >
                    <Plus size={16} />
                    Add IOC
                </button>
                </div>
            </div>

            {/* Table */}
            <div className="glass-panel rounded-xl border border-line overflow-hidden">
                {isLoading ? (
                    <div className="flex items-center justify-center py-20">
                        <div className="w-8 h-8 rounded-full border-4 border-line border-t-accent animate-spin" />
                    </div>
                ) : !iocs || iocs.length === 0 ? (
                    <div className="flex flex-col items-center justify-center py-20 text-muted">
                        <AlertOctagon size={40} className="mb-3 opacity-30" />
                        <p className="text-sm">No IOCs recorded yet. Add one to get started.</p>
                    </div>
                ) : (
                    <div className="overflow-x-auto">
                        <table className="w-full text-sm">
                            <thead>
                                <tr className="border-b border-line text-xs font-semibold text-muted uppercase tracking-wider">
                                    <th className="px-2 py-3 w-8"></th>
                                    <th className="px-4 py-3 text-left">Type</th>
                                    <th className="px-4 py-3 text-left">Value</th>
                                    <th className="px-4 py-3 text-left">Threat Level</th>
                                    <th className="px-4 py-3 text-left">Confidence</th>
                                    <th className="px-4 py-3 text-left">Status</th>
                                    <th className="px-4 py-3 text-left">TLP</th>
                                    <th className="px-4 py-3 text-left">Source</th>
                                    <th className="px-4 py-3 text-left">Created</th>
                                    <th className="px-4 py-3 text-right">Actions</th>
                                </tr>
                            </thead>
                            <tbody className="divide-y divide-line">
                                {iocs.filter(i => i.value.toLowerCase().includes((searchParams.get('q') ?? '').toLowerCase())).map((ioc) => (
                                    <Fragment key={ioc.id}>
                                    <tr className="hover:bg-panel transition-colors group">
                                        <td className="px-2 py-3">
                                            <button onClick={() => setOpenId(openId === ioc.id ? null : ioc.id)} className="text-muted hover:text-fg-2" title="Threat intel" aria-expanded={openId === ioc.id}>
                                                {openId === ioc.id ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                                            </button>
                                        </td>
                                        <td className="px-4 py-3">
                                            <span className="font-mono text-xs text-fg-3 bg-raised px-2 py-0.5 rounded-sm">
                                                {ioc.ioc_type.replace('_', ' ')}
                                            </span>
                                        </td>
                                        <td className="px-4 py-3 font-mono text-fg max-w-[200px] truncate" title={ioc.value}>
                                            {ioc.enrichment_verdict && VERDICT_DOT[ioc.enrichment_verdict] && (
                                                <span title={ioc.enrichment_verdict} className={cn('inline-block w-2 h-2 rounded-full mr-2 align-middle', VERDICT_DOT[ioc.enrichment_verdict])} />
                                            )}
                                            {ioc.value}
                                        </td>
                                        <td className="px-4 py-3">
                                            <span className={cn('px-2 py-0.5 rounded-sm text-[11px] font-bold border uppercase', threatLevelBadge(ioc.threat_level))}>
                                                {ioc.threat_level}
                                            </span>
                                        </td>
                                        <td className="px-4 py-3">
                                            <div className="flex items-center gap-2">
                                                <div className="w-16 h-1.5 bg-raised rounded-full overflow-hidden">
                                                    <div
                                                        className="h-full bg-purple-500 rounded-full"
                                                        style={{ width: `${ioc.confidence}%` }}
                                                    />
                                                </div>
                                                <span className="text-xs text-muted">{ioc.confidence}%</span>
                                            </div>
                                        </td>
                                        <td className="px-4 py-3">
                                            <span className={cn('px-2 py-0.5 rounded-sm text-[11px] font-bold border', statusBadge(ioc.status))}>
                                                {ioc.status.replace('_', ' ')}
                                            </span>
                                        </td>
                                        <td className="px-4 py-3">
                                            <span className={cn('px-2 py-0.5 rounded-sm text-[11px] font-bold border uppercase', tlpBadge(ioc.tlp))}>
                                                TLP:{ioc.tlp}
                                            </span>
                                        </td>
                                        <td className="px-4 py-3 text-fg-3 text-xs">{ioc.source || '—'}</td>
                                        <td className="px-4 py-3 text-muted text-xs">
                                            {new Date(ioc.created_at).toLocaleDateString()}
                                        </td>
                                        <td className="px-4 py-3 text-right">
                                            <button
                                                onClick={() => {
                                                    if (confirm('Delete this IOC?')) {
                                                        deleteMutation.mutate(ioc.id);
                                                    }
                                                }}
                                                className="p-1.5 text-muted hover:text-red-300 hover:bg-raised rounded-sm transition-colors opacity-0 group-hover:opacity-100"
                                                title="Delete IOC"
                                            >
                                                <Trash2 size={14} />
                                            </button>
                                        </td>
                                    </tr>
                                    {openId === ioc.id && (
                                        <tr className="bg-surface">
                                            <td></td>
                                            <td colSpan={9} className="px-4 py-3"><EnrichmentPanel kind="ioc" id={ioc.id} canRun={canRun} ioc={ioc} /></td>
                                        </tr>
                                    )}
                                    </Fragment>
                                ))}
                            </tbody>
                        </table>
                    </div>
                )}
            </div>

            {/* Add IOC Modal */}
            <Modal
                open={showModal}
                onClose={() => setShowModal(false)}
                title="Add Indicator of Compromise"
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
                            <label className={modalLabel}>Type</label>
                            <select
                                value={form.ioc_type}
                                onChange={(e) => setForm({ ...form, ioc_type: e.target.value })}
                                className={modalInput}
                            >
                                {IOC_TYPES.map(t => <option key={t} value={t}>{t.replace(/_/g, ' ')}</option>)}
                            </select>
                        </div>
                        <div>
                            <label className={modalLabel}>Threat Level</label>
                            <select
                                value={form.threat_level}
                                onChange={(e) => setForm({ ...form, threat_level: e.target.value })}
                                className={modalInput}
                            >
                                {THREAT_LEVELS.map(t => <option key={t} value={t}>{t}</option>)}
                            </select>
                        </div>
                    </div>

                    <div>
                        <label className={modalLabel}>Value</label>
                        <input
                            type="text"
                            value={form.value}
                            onChange={(e) => setForm({ ...form, value: e.target.value })}
                            placeholder="e.g. 192.168.1.1, malicious.com, sha256..."
                            className={`${modalInput} font-mono`}
                        />
                    </div>

                    <div className="grid grid-cols-3 gap-3">
                        <div>
                            <label className={modalLabel}>Confidence ({form.confidence}%)</label>
                            <input
                                type="number"
                                min={0}
                                max={100}
                                value={form.confidence}
                                onChange={(e) => setForm({ ...form, confidence: Number(e.target.value) })}
                                className={modalInput}
                            />
                        </div>
                        <div>
                            <label className={modalLabel}>Status</label>
                            <select
                                value={form.status}
                                onChange={(e) => setForm({ ...form, status: e.target.value })}
                                className={modalInput}
                            >
                                {STATUSES.map(s => <option key={s} value={s}>{s.replace('_', ' ')}</option>)}
                            </select>
                        </div>
                        <div>
                            <label className={modalLabel}>TLP</label>
                            <select
                                value={form.tlp}
                                onChange={(e) => setForm({ ...form, tlp: e.target.value })}
                                className={modalInput}
                            >
                                {TLP_LEVELS.map(t => <option key={t} value={t}>TLP:{t}</option>)}
                            </select>
                        </div>
                    </div>

                    <div>
                        <label className={modalLabel}>Source</label>
                        <input
                            type="text"
                            value={form.source}
                            onChange={(e) => setForm({ ...form, source: e.target.value })}
                            placeholder="e.g. VirusTotal, internal, OSINT..."
                            className={modalInput}
                        />
                    </div>

                    <div>
                        <label className={modalLabel}>Description</label>
                        <textarea
                            value={form.description}
                            onChange={(e) => setForm({ ...form, description: e.target.value })}
                            placeholder="Additional context..."
                            rows={2}
                            className={`${modalInput} resize-none`}
                        />
                    </div>
                </div>
            </Modal>
        </PageContainer>
    );
}
