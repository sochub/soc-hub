import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '../../api/client';
import { AlertOctagon, Plus, Trash2, X } from 'lucide-react';
import { motion, AnimatePresence } from 'framer-motion';
import { cn } from '../../lib/utils';
import type { IOC } from '../../types';

const IOC_TYPES = ['ip_address', 'domain', 'url', 'file_hash', 'email', 'registry_key', 'mutex', 'user_agent', 'other'];
const THREAT_LEVELS = ['critical', 'high', 'medium', 'low', 'info'];
const STATUSES = ['active', 'expired', 'whitelisted', 'false_positive'];
const TLP_LEVELS = ['white', 'green', 'amber', 'red'];

const threatLevelBadge = (level: string) => {
    const map: Record<string, string> = {
        critical: 'bg-red-50 text-red-700 border-red-200',
        high: 'bg-orange-50 text-orange-700 border-orange-200',
        medium: 'bg-yellow-50 text-yellow-700 border-yellow-200',
        low: 'bg-blue-50 text-accent-600 border-blue-200',
        info: 'bg-zinc-100 text-zinc-500 border-zinc-200',
    };
    return map[level] ?? map.info;
};

const statusBadge = (status: string) => {
    const map: Record<string, string> = {
        active: 'bg-emerald-50 text-emerald-700 border-emerald-200',
        expired: 'bg-zinc-100 text-zinc-500 border-zinc-200',
        whitelisted: 'bg-blue-50 text-accent-600 border-blue-200',
        false_positive: 'bg-yellow-50 text-yellow-700 border-yellow-200',
    };
    return map[status] ?? map.expired;
};

const tlpBadge = (tlp: string) => {
    const map: Record<string, string> = {
        white: 'bg-zinc-100 text-zinc-700 border-zinc-300',
        green: 'bg-green-50 text-green-700 border-green-200',
        amber: 'bg-amber-50 text-amber-700 border-amber-200',
        red: 'bg-red-50 text-red-700 border-red-200',
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
    const [showModal, setShowModal] = useState(false);
    const [form, setForm] = useState(defaultForm);

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
        <div className="space-y-6">
            {/* Header */}
            <div className="flex items-center justify-between">
                <div>
                    <h1 className="text-2xl font-bold text-zinc-900 tracking-tight flex items-center gap-3">
                        <AlertOctagon size={24} className="text-purple-700" />
                        Indicators of Compromise
                    </h1>
                    <p className="text-zinc-500 text-sm mt-1">Track and manage threat indicators across all cases.</p>
                </div>
                <button
                    onClick={() => setShowModal(true)}
                    className="flex items-center gap-2 bg-purple-600 hover:bg-purple-500 text-white px-4 py-2 rounded-lg font-medium transition-all shadow-lg shadow-purple-200"
                >
                    <Plus size={16} />
                    Add IOC
                </button>
            </div>

            {/* Table */}
            <div className="glass-panel rounded-xl border border-zinc-200 overflow-hidden">
                {isLoading ? (
                    <div className="flex items-center justify-center py-20">
                        <div className="w-8 h-8 rounded-full border-4 border-zinc-200 border-t-accent-600 animate-spin" />
                    </div>
                ) : !iocs || iocs.length === 0 ? (
                    <div className="flex flex-col items-center justify-center py-20 text-zinc-400">
                        <AlertOctagon size={40} className="mb-3 opacity-30" />
                        <p className="text-sm">No IOCs recorded yet. Add one to get started.</p>
                    </div>
                ) : (
                    <div className="overflow-x-auto">
                        <table className="w-full text-sm">
                            <thead>
                                <tr className="border-b border-zinc-200 text-xs font-semibold text-zinc-400 uppercase tracking-wider">
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
                            <tbody className="divide-y divide-zinc-200">
                                {iocs.map((ioc) => (
                                    <tr key={ioc.id} className="hover:bg-white transition-colors group">
                                        <td className="px-4 py-3">
                                            <span className="font-mono text-xs text-zinc-500 bg-zinc-100 px-2 py-0.5 rounded">
                                                {ioc.ioc_type.replace('_', ' ')}
                                            </span>
                                        </td>
                                        <td className="px-4 py-3 font-mono text-zinc-800 max-w-[200px] truncate" title={ioc.value}>
                                            {ioc.value}
                                        </td>
                                        <td className="px-4 py-3">
                                            <span className={cn('px-2 py-0.5 rounded text-[11px] font-bold border uppercase', threatLevelBadge(ioc.threat_level))}>
                                                {ioc.threat_level}
                                            </span>
                                        </td>
                                        <td className="px-4 py-3">
                                            <div className="flex items-center gap-2">
                                                <div className="w-16 h-1.5 bg-zinc-100 rounded-full overflow-hidden">
                                                    <div
                                                        className="h-full bg-purple-500 rounded-full"
                                                        style={{ width: `${ioc.confidence}%` }}
                                                    />
                                                </div>
                                                <span className="text-xs text-zinc-400">{ioc.confidence}%</span>
                                            </div>
                                        </td>
                                        <td className="px-4 py-3">
                                            <span className={cn('px-2 py-0.5 rounded text-[11px] font-bold border', statusBadge(ioc.status))}>
                                                {ioc.status.replace('_', ' ')}
                                            </span>
                                        </td>
                                        <td className="px-4 py-3">
                                            <span className={cn('px-2 py-0.5 rounded text-[11px] font-bold border uppercase', tlpBadge(ioc.tlp))}>
                                                TLP:{ioc.tlp}
                                            </span>
                                        </td>
                                        <td className="px-4 py-3 text-zinc-500 text-xs">{ioc.source || '—'}</td>
                                        <td className="px-4 py-3 text-zinc-400 text-xs">
                                            {new Date(ioc.created_at).toLocaleDateString()}
                                        </td>
                                        <td className="px-4 py-3 text-right">
                                            <button
                                                onClick={() => {
                                                    if (confirm('Delete this IOC?')) {
                                                        deleteMutation.mutate(ioc.id);
                                                    }
                                                }}
                                                className="p-1.5 text-zinc-400 hover:text-red-700 hover:bg-zinc-100 rounded transition-colors opacity-0 group-hover:opacity-100"
                                                title="Delete IOC"
                                            >
                                                <Trash2 size={14} />
                                            </button>
                                        </td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                )}
            </div>

            {/* Add IOC Modal */}
            <AnimatePresence>
                {showModal && (
                    <motion.div
                        initial={{ opacity: 0 }}
                        animate={{ opacity: 1 }}
                        exit={{ opacity: 0 }}
                        className="fixed inset-0 bg-black/80 backdrop-blur-sm z-[100] flex items-center justify-center p-4"
                        onClick={() => setShowModal(false)}
                    >
                        <motion.div
                            initial={{ scale: 0.95, opacity: 0 }}
                            animate={{ scale: 1, opacity: 1 }}
                            exit={{ scale: 0.95, opacity: 0 }}
                            onClick={(e) => e.stopPropagation()}
                            className="glass-panel p-6 rounded-xl max-w-lg w-full border border-zinc-200 space-y-4"
                        >
                            <div className="flex items-center justify-between">
                                <h2 className="text-xl font-bold text-zinc-900">Add Indicator of Compromise</h2>
                                <button
                                    onClick={() => setShowModal(false)}
                                    className="p-2 text-zinc-500 hover:text-zinc-900 hover:bg-zinc-100 rounded-lg transition-colors"
                                >
                                    <X size={20} />
                                </button>
                            </div>

                            <div className="grid grid-cols-2 gap-3">
                                <div>
                                    <label className="block text-xs font-medium text-zinc-500 mb-1">Type</label>
                                    <select
                                        value={form.ioc_type}
                                        onChange={(e) => setForm({ ...form, ioc_type: e.target.value })}
                                        className="w-full bg-white border border-zinc-200 rounded-lg px-3 py-2 text-sm text-zinc-900 focus:outline-none focus:ring-1 focus:ring-white/50"
                                    >
                                        {IOC_TYPES.map(t => <option key={t} value={t}>{t.replace(/_/g, ' ')}</option>)}
                                    </select>
                                </div>
                                <div>
                                    <label className="block text-xs font-medium text-zinc-500 mb-1">Threat Level</label>
                                    <select
                                        value={form.threat_level}
                                        onChange={(e) => setForm({ ...form, threat_level: e.target.value })}
                                        className="w-full bg-white border border-zinc-200 rounded-lg px-3 py-2 text-sm text-zinc-900 focus:outline-none focus:ring-1 focus:ring-white/50"
                                    >
                                        {THREAT_LEVELS.map(t => <option key={t} value={t}>{t}</option>)}
                                    </select>
                                </div>
                            </div>

                            <div>
                                <label className="block text-xs font-medium text-zinc-500 mb-1">Value</label>
                                <input
                                    type="text"
                                    value={form.value}
                                    onChange={(e) => setForm({ ...form, value: e.target.value })}
                                    placeholder="e.g. 192.168.1.1, malicious.com, sha256..."
                                    className="w-full bg-white border border-zinc-200 rounded-lg px-3 py-2 text-sm text-zinc-900 font-mono focus:outline-none focus:ring-1 focus:ring-white/50"
                                />
                            </div>

                            <div className="grid grid-cols-3 gap-3">
                                <div>
                                    <label className="block text-xs font-medium text-zinc-500 mb-1">Confidence ({form.confidence}%)</label>
                                    <input
                                        type="number"
                                        min={0}
                                        max={100}
                                        value={form.confidence}
                                        onChange={(e) => setForm({ ...form, confidence: Number(e.target.value) })}
                                        className="w-full bg-white border border-zinc-200 rounded-lg px-3 py-2 text-sm text-zinc-900 focus:outline-none focus:ring-1 focus:ring-white/50"
                                    />
                                </div>
                                <div>
                                    <label className="block text-xs font-medium text-zinc-500 mb-1">Status</label>
                                    <select
                                        value={form.status}
                                        onChange={(e) => setForm({ ...form, status: e.target.value })}
                                        className="w-full bg-white border border-zinc-200 rounded-lg px-3 py-2 text-sm text-zinc-900 focus:outline-none focus:ring-1 focus:ring-white/50"
                                    >
                                        {STATUSES.map(s => <option key={s} value={s}>{s.replace('_', ' ')}</option>)}
                                    </select>
                                </div>
                                <div>
                                    <label className="block text-xs font-medium text-zinc-500 mb-1">TLP</label>
                                    <select
                                        value={form.tlp}
                                        onChange={(e) => setForm({ ...form, tlp: e.target.value })}
                                        className="w-full bg-white border border-zinc-200 rounded-lg px-3 py-2 text-sm text-zinc-900 focus:outline-none focus:ring-1 focus:ring-white/50"
                                    >
                                        {TLP_LEVELS.map(t => <option key={t} value={t}>TLP:{t}</option>)}
                                    </select>
                                </div>
                            </div>

                            <div>
                                <label className="block text-xs font-medium text-zinc-500 mb-1">Source</label>
                                <input
                                    type="text"
                                    value={form.source}
                                    onChange={(e) => setForm({ ...form, source: e.target.value })}
                                    placeholder="e.g. VirusTotal, internal, OSINT..."
                                    className="w-full bg-white border border-zinc-200 rounded-lg px-3 py-2 text-sm text-zinc-900 focus:outline-none focus:ring-1 focus:ring-white/50"
                                />
                            </div>

                            <div>
                                <label className="block text-xs font-medium text-zinc-500 mb-1">Description</label>
                                <textarea
                                    value={form.description}
                                    onChange={(e) => setForm({ ...form, description: e.target.value })}
                                    placeholder="Additional context..."
                                    rows={2}
                                    className="w-full bg-white border border-zinc-200 rounded-lg px-3 py-2 text-sm text-zinc-900 focus:outline-none focus:ring-1 focus:ring-white/50 resize-none"
                                />
                            </div>

                            <div className="flex gap-3 pt-2">
                                <button
                                    onClick={() => setShowModal(false)}
                                    className="flex-1 px-4 py-2 bg-zinc-100 hover:bg-zinc-200 text-zinc-700 rounded-lg font-medium transition-colors"
                                >
                                    Cancel
                                </button>
                                <button
                                    onClick={() => createMutation.mutate(form)}
                                    disabled={!form.value.trim() || createMutation.isPending}
                                    className="flex-1 px-4 py-2 bg-purple-600 hover:bg-purple-500 text-white rounded-lg font-medium transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                                >
                                    {createMutation.isPending ? 'Adding...' : 'Add IOC'}
                                </button>
                            </div>
                        </motion.div>
                    </motion.div>
                )}
            </AnimatePresence>
        </div>
    );
}
