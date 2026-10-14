import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '../../api/client';
import { Link } from 'react-router-dom';
import { FileText, Globe, Mail, Server, Database, ArrowRight, Calendar, Hash, Link2, Pencil, Trash2, Check, X } from 'lucide-react';
import { motion, AnimatePresence } from 'framer-motion';
import PageContainer from '../../components/layout/PageContainer';
import { useArtifactTypes } from './useArtifactTypes';

const ARTIFACT_ICONS: Record<string, any> = {
    hash: FileText,
    file_hash: FileText,
    ip: Server,
    domain: Globe,
    url: Globe,
    email: Mail,
};

export default function ArtifactsList() {
    const queryClient = useQueryClient();
    const { options: ARTIFACT_TYPE_OPTIONS } = useArtifactTypes();
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
            <PageContainer>
                <div className="flex items-center justify-center h-[50vh]">
                    <div className="flex flex-col items-center gap-4">
                        <div className="w-10 h-10 rounded-full border-4 border-line border-t-accent animate-spin" />
                    </div>
                </div>
            </PageContainer>
        );
    }

    return (
        <PageContainer>
            <div className="flex items-center justify-between">
                <div>
                    <h1 className="text-2xl font-bold text-fg tracking-tight mb-1">Artifact Repository</h1>
                    <p className="text-fg-3 text-sm">Centralized view of all Indicators of Compromise (IOCs) and evidence.</p>
                </div>
            </div>

            {artifacts && artifacts.length > 0 ? (
                <div className="glass-panel rounded-xl overflow-hidden border border-line">
                    <table className="w-full text-left border-collapse">
                        <thead>
                            <tr className="border-b border-line bg-panel">
                                <th className="p-4 text-xs font-semibold text-muted uppercase tracking-wider w-32">Type</th>
                                <th className="p-4 text-xs font-semibold text-muted uppercase tracking-wider">Value</th>
                                <th className="p-4 text-xs font-semibold text-muted uppercase tracking-wider w-64">Related Cases</th>
                                <th className="p-4 text-xs font-semibold text-muted uppercase tracking-wider w-40">Created</th>
                                <th className="p-4 text-xs font-semibold text-muted uppercase tracking-wider w-24 text-right">Actions</th>
                            </tr>
                        </thead>
                        <tbody className="divide-y divide-line">
                            <AnimatePresence>
                                {artifacts.map((artifact: any) => {
                                    const isEditing = editingId === artifact.id;
                                    const Icon = ARTIFACT_ICONS[artifact.artifact_type] || FileText;
                                    const caseIds: number[] = artifact.case_ids || [];

                                    return (
                                        <motion.tr
                                            key={artifact.id}
                                            layout
                                            className={`group transition-colors ${isEditing ? 'bg-raised' : 'hover:bg-raised'}`}
                                        >
                                            {isEditing ? (
                                                <>
                                                    <td className="p-3">
                                                        <select
                                                            value={editData.artifact_type}
                                                            onChange={(e) => setEditData({ ...editData, artifact_type: e.target.value })}
                                                            className="w-full bg-panel border border-line rounded-lg px-2 py-1.5 text-xs text-fg focus:outline-hidden focus:ring-1 focus:ring-white/50"
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
                                                                className="w-full bg-panel border border-line rounded-lg px-3 py-1.5 text-sm text-fg font-mono focus:outline-hidden focus:ring-1 focus:ring-white/50"
                                                            />
                                                            <input
                                                                type="text"
                                                                value={editData.description}
                                                                onChange={(e) => setEditData({ ...editData, description: e.target.value })}
                                                                placeholder="Description (optional)"
                                                                className="w-full bg-panel border border-line rounded-lg px-3 py-1.5 text-xs text-fg-3 focus:outline-hidden focus:ring-1 focus:ring-white/50"
                                                            />
                                                        </div>
                                                    </td>
                                                    <td className="p-3">
                                                        <div className="flex flex-wrap gap-2">
                                                            {caseIds.map((cid: number) => (
                                                                <span key={cid} className="flex items-center gap-1 text-xs text-muted">
                                                                    <Hash size={10} />
                                                                    {String(cid).padStart(4, '0')}
                                                                </span>
                                                            ))}
                                                        </div>
                                                    </td>
                                                    <td className="p-3">
                                                        <div className="flex items-center gap-2 text-xs text-muted">
                                                            <Calendar size={12} />
                                                            {new Date(artifact.created_at).toLocaleDateString()}
                                                        </div>
                                                    </td>
                                                    <td className="p-3">
                                                        <div className="flex items-center justify-end gap-1">
                                                            <button
                                                                onClick={() => saveEdit(artifact.id)}
                                                                disabled={!editData.value.trim() || updateMutation.isPending}
                                                                className="p-1.5 text-green-300 hover:bg-green-500/10 rounded-sm transition-colors disabled:opacity-50"
                                                                title="Save"
                                                            >
                                                                <Check size={14} />
                                                            </button>
                                                            <button
                                                                onClick={cancelEdit}
                                                                className="p-1.5 text-fg-3 hover:bg-raised-2 rounded-sm transition-colors"
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
                                                            <div className="p-1.5 bg-raised rounded-sm text-accent">
                                                                <Icon size={14} />
                                                            </div>
                                                            <span className="text-xs font-medium text-fg-3 uppercase">{artifact.artifact_type}</span>
                                                        </div>
                                                    </td>
                                                    <td className="p-4">
                                                        <div className="flex items-center gap-2">
                                                            <span className="font-mono text-sm text-fg">{artifact.value}</span>
                                                            {!artifact.isolated && caseIds.length > 1 && (
                                                                <span className="text-[10px] bg-accent/10 text-accent border border-accent/40 px-1.5 py-0.5 rounded-sm uppercase font-bold flex items-center gap-1">
                                                                    <Link2 size={10} />
                                                                    Shared
                                                                </span>
                                                            )}
                                                            {artifact.isolated && (
                                                                <span className="text-[10px] bg-amber-500/10 text-amber-300 border border-amber-500/30 px-1.5 py-0.5 rounded-sm uppercase font-bold">
                                                                    Isolated
                                                                </span>
                                                            )}
                                                        </div>
                                                        {artifact.description && (
                                                            <p className="text-xs text-muted mt-1">{artifact.description}</p>
                                                        )}
                                                    </td>
                                                    <td className="p-4">
                                                        <div className="flex flex-wrap gap-2">
                                                            {caseIds.length > 0 ? caseIds.map((cid: number) => (
                                                                <Link
                                                                    key={cid}
                                                                    to={`/cases/${cid}`}
                                                                    className="flex items-center gap-1 text-sm text-fg-3 hover:text-fg transition-colors group/link"
                                                                >
                                                                    <Hash size={12} />
                                                                    <span>{String(cid).padStart(4, '0')}</span>
                                                                    <ArrowRight size={12} className="opacity-0 group-hover/link:opacity-100 transition-opacity" />
                                                                </Link>
                                                            )) : (
                                                                <span className="text-xs text-muted">No cases</span>
                                                            )}
                                                        </div>
                                                    </td>
                                                    <td className="p-4">
                                                        <div className="flex items-center gap-2 text-xs text-muted">
                                                            <Calendar size={12} />
                                                            {new Date(artifact.created_at).toLocaleDateString()}
                                                        </div>
                                                    </td>
                                                    <td className="p-4">
                                                        <div className="flex items-center justify-end gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
                                                            <button
                                                                onClick={() => startEdit(artifact)}
                                                                className="p-1.5 text-muted hover:text-accent hover:bg-raised rounded-sm transition-colors"
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
                                                                className="p-1.5 text-muted hover:text-red-300 hover:bg-raised rounded-sm transition-colors"
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
                <div className="glass-panel p-12 rounded-xl border-dashed border-2 border-line flex flex-col items-center justify-center text-muted">
                    <Database size={48} className="mb-4 opacity-20" />
                    <h3 className="text-lg font-medium text-fg-3 mb-2">No Artifacts Found</h3>
                    <p className="text-sm">Artifacts added to cases will appear here automatically.</p>
                </div>
            )}
        </PageContainer>
    );
}
