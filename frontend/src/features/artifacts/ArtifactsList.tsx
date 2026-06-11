import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '../../api/client';
import { Link } from 'react-router-dom';
import { FileText, Globe, Mail, Server, Database, ArrowRight, Calendar, Hash, Link2, Pencil, Trash2, Check, X } from 'lucide-react';
import { motion, AnimatePresence } from 'framer-motion';

const ARTIFACT_ICONS: Record<string, any> = {
    hash: FileText,
    file_hash: FileText,
    ip: Server,
    domain: Globe,
    url: Globe,
    email: Mail,
};

const ARTIFACT_TYPE_OPTIONS = [
    { value: 'ip', label: 'IP Address' },
    { value: 'domain', label: 'Domain' },
    { value: 'url', label: 'URL' },
    { value: 'file_hash', label: 'File Hash' },
    { value: 'email', label: 'Email' },
    { value: 'other', label: 'Other' },
];

export default function ArtifactsList() {
    const queryClient = useQueryClient();
    const [editingId, setEditingId] = useState<number | null>(null);
    const [editData, setEditData] = useState({ artifact_type: '', value: '', description: '' });

    const { data: artifacts, isLoading } = useQuery({
        queryKey: ['artifacts', 'all'],
        queryFn: async () => {
            const response = await api.get('/artifacts/');
            return response.data;
        }
    });

    const updateMutation = useMutation({
        mutationFn: async ({ id, data }: { id: number; data: { artifact_type?: string; value?: string; description?: string } }) => {
            const response = await api.put(`/artifacts/${id}`, data);
            return response.data;
        },
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['artifacts'] });
            setEditingId(null);
        }
    });

    const deleteMutation = useMutation({
        mutationFn: async (id: number) => {
            await api.delete(`/artifacts/${id}`);
        },
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['artifacts'] });
        }
    });

    const startEdit = (artifact: any) => {
        setEditingId(artifact.id);
        setEditData({
            artifact_type: artifact.artifact_type,
            value: artifact.value,
            description: artifact.description || '',
        });
    };

    const cancelEdit = () => {
        setEditingId(null);
    };

    const saveEdit = (id: number) => {
        updateMutation.mutate({ id, data: editData });
    };

    if (isLoading) {
        return (
            <div className="flex items-center justify-center h-[50vh]">
                <div className="flex flex-col items-center gap-4">
                    <div className="w-10 h-10 rounded-full border-4 border-zinc-200 border-t-accent-600 animate-spin" />
                </div>
            </div>
        );
    }

    return (
        <div className="space-y-6">
            <div className="flex items-center justify-between">
                <div>
                    <h1 className="text-2xl font-bold text-zinc-900 tracking-tight mb-1">Artifact Repository</h1>
                    <p className="text-zinc-500 text-sm">Centralized view of all Indicators of Compromise (IOCs) and evidence.</p>
                </div>
            </div>

            {artifacts && artifacts.length > 0 ? (
                <div className="glass-panel rounded-xl overflow-hidden border border-zinc-200">
                    <table className="w-full text-left border-collapse">
                        <thead>
                            <tr className="border-b border-zinc-200 bg-white">
                                <th className="p-4 text-xs font-semibold text-zinc-400 uppercase tracking-wider w-32">Type</th>
                                <th className="p-4 text-xs font-semibold text-zinc-400 uppercase tracking-wider">Value</th>
                                <th className="p-4 text-xs font-semibold text-zinc-400 uppercase tracking-wider w-64">Related Cases</th>
                                <th className="p-4 text-xs font-semibold text-zinc-400 uppercase tracking-wider w-40">Created</th>
                                <th className="p-4 text-xs font-semibold text-zinc-400 uppercase tracking-wider w-24 text-right">Actions</th>
                            </tr>
                        </thead>
                        <tbody className="divide-y divide-zinc-200">
                            <AnimatePresence>
                                {artifacts.map((artifact: any) => {
                                    const isEditing = editingId === artifact.id;
                                    const Icon = ARTIFACT_ICONS[artifact.artifact_type] || FileText;
                                    const caseIds: number[] = artifact.case_ids || [];

                                    return (
                                        <motion.tr
                                            key={artifact.id}
                                            layout
                                            className={`group transition-colors ${isEditing ? 'bg-zinc-100' : 'hover:bg-zinc-100'}`}
                                        >
                                            {isEditing ? (
                                                <>
                                                    <td className="p-3">
                                                        <select
                                                            value={editData.artifact_type}
                                                            onChange={(e) => setEditData({ ...editData, artifact_type: e.target.value })}
                                                            className="w-full bg-white border border-zinc-200 rounded-lg px-2 py-1.5 text-xs text-zinc-900 focus:outline-none focus:ring-1 focus:ring-white/50"
                                                        >
                                                            {ARTIFACT_TYPE_OPTIONS.map(opt => (
                                                                <option key={opt.value} value={opt.value}>{opt.label}</option>
                                                            ))}
                                                        </select>
                                                    </td>
                                                    <td className="p-3">
                                                        <div className="space-y-2">
                                                            <input
                                                                type="text"
                                                                value={editData.value}
                                                                onChange={(e) => setEditData({ ...editData, value: e.target.value })}
                                                                className="w-full bg-white border border-zinc-200 rounded-lg px-3 py-1.5 text-sm text-zinc-900 font-mono focus:outline-none focus:ring-1 focus:ring-white/50"
                                                            />
                                                            <input
                                                                type="text"
                                                                value={editData.description}
                                                                onChange={(e) => setEditData({ ...editData, description: e.target.value })}
                                                                placeholder="Description (optional)"
                                                                className="w-full bg-white border border-zinc-200 rounded-lg px-3 py-1.5 text-xs text-zinc-500 focus:outline-none focus:ring-1 focus:ring-white/50"
                                                            />
                                                        </div>
                                                    </td>
                                                    <td className="p-3">
                                                        <div className="flex flex-wrap gap-2">
                                                            {caseIds.map((cid: number) => (
                                                                <span key={cid} className="flex items-center gap-1 text-xs text-zinc-400">
                                                                    <Hash size={10} />
                                                                    {String(cid).padStart(4, '0')}
                                                                </span>
                                                            ))}
                                                        </div>
                                                    </td>
                                                    <td className="p-3">
                                                        <div className="flex items-center gap-2 text-xs text-zinc-400">
                                                            <Calendar size={12} />
                                                            {new Date(artifact.created_at).toLocaleDateString()}
                                                        </div>
                                                    </td>
                                                    <td className="p-3">
                                                        <div className="flex items-center justify-end gap-1">
                                                            <button
                                                                onClick={() => saveEdit(artifact.id)}
                                                                disabled={!editData.value.trim() || updateMutation.isPending}
                                                                className="p-1.5 text-green-700 hover:bg-green-50 rounded transition-colors disabled:opacity-50"
                                                                title="Save"
                                                            >
                                                                <Check size={14} />
                                                            </button>
                                                            <button
                                                                onClick={cancelEdit}
                                                                className="p-1.5 text-zinc-500 hover:bg-zinc-200 rounded transition-colors"
                                                                title="Cancel"
                                                            >
                                                                <X size={14} />
                                                            </button>
                                                        </div>
                                                    </td>
                                                </>
                                            ) : (
                                                <>
                                                    <td className="p-4">
                                                        <div className="flex items-center gap-2">
                                                            <div className="p-1.5 bg-zinc-100 rounded text-accent-600">
                                                                <Icon size={14} />
                                                            </div>
                                                            <span className="text-xs font-medium text-zinc-500 uppercase">{artifact.artifact_type}</span>
                                                        </div>
                                                    </td>
                                                    <td className="p-4">
                                                        <div className="flex items-center gap-2">
                                                            <span className="font-mono text-sm text-zinc-800">{artifact.value}</span>
                                                            {!artifact.isolated && caseIds.length > 1 && (
                                                                <span className="text-[10px] bg-blue-50 text-accent-600 border border-blue-200 px-1.5 py-0.5 rounded uppercase font-bold flex items-center gap-1">
                                                                    <Link2 size={10} />
                                                                    Shared
                                                                </span>
                                                            )}
                                                            {artifact.isolated && (
                                                                <span className="text-[10px] bg-amber-50 text-amber-700 border border-amber-200 px-1.5 py-0.5 rounded uppercase font-bold">
                                                                    Isolated
                                                                </span>
                                                            )}
                                                        </div>
                                                        {artifact.description && (
                                                            <p className="text-xs text-zinc-400 mt-1">{artifact.description}</p>
                                                        )}
                                                    </td>
                                                    <td className="p-4">
                                                        <div className="flex flex-wrap gap-2">
                                                            {caseIds.length > 0 ? caseIds.map((cid: number) => (
                                                                <Link
                                                                    key={cid}
                                                                    to={`/cases/${cid}`}
                                                                    className="flex items-center gap-1 text-sm text-zinc-500 hover:text-zinc-900 transition-colors group/link"
                                                                >
                                                                    <Hash size={12} />
                                                                    <span>{String(cid).padStart(4, '0')}</span>
                                                                    <ArrowRight size={12} className="opacity-0 group-hover/link:opacity-100 transition-opacity" />
                                                                </Link>
                                                            )) : (
                                                                <span className="text-xs text-zinc-400">No cases</span>
                                                            )}
                                                        </div>
                                                    </td>
                                                    <td className="p-4">
                                                        <div className="flex items-center gap-2 text-xs text-zinc-400">
                                                            <Calendar size={12} />
                                                            {new Date(artifact.created_at).toLocaleDateString()}
                                                        </div>
                                                    </td>
                                                    <td className="p-4">
                                                        <div className="flex items-center justify-end gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
                                                            <button
                                                                onClick={() => startEdit(artifact)}
                                                                className="p-1.5 text-zinc-400 hover:text-accent-600 hover:bg-zinc-100 rounded transition-colors"
                                                                title="Edit artifact"
                                                            >
                                                                <Pencil size={14} />
                                                            </button>
                                                            <button
                                                                onClick={() => {
                                                                    if (confirm(`Delete artifact "${artifact.value}"? This will remove it from all linked cases.`)) {
                                                                        deleteMutation.mutate(artifact.id);
                                                                    }
                                                                }}
                                                                className="p-1.5 text-zinc-400 hover:text-red-700 hover:bg-zinc-100 rounded transition-colors"
                                                                title="Delete artifact"
                                                            >
                                                                <Trash2 size={14} />
                                                            </button>
                                                        </div>
                                                    </td>
                                                </>
                                            )}
                                        </motion.tr>
                                    );
                                })}
                            </AnimatePresence>
                        </tbody>
                    </table>
                </div>
            ) : (
                <div className="glass-panel p-12 rounded-xl border-dashed border-2 border-zinc-200 flex flex-col items-center justify-center text-zinc-400">
                    <Database size={48} className="mb-4 opacity-20" />
                    <h3 className="text-lg font-medium text-zinc-500 mb-2">No Artifacts Found</h3>
                    <p className="text-sm">Artifacts added to cases will appear here automatically.</p>
                </div>
            )}
        </div>
    );
}
