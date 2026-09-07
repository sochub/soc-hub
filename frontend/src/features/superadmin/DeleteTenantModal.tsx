import { useState } from 'react';
import { X, AlertTriangle } from 'lucide-react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import { api } from '../../api/client';
import type { Tenant } from '../../types';

interface DeleteTenantModalProps {
    tenant: Tenant;
    isOpen: boolean;
    onClose: () => void;
}

export default function DeleteTenantModal({ tenant, isOpen, onClose }: DeleteTenantModalProps) {
    const [confirmText, setConfirmText] = useState('');
    const [error, setError] = useState('');
    const queryClient = useQueryClient();
    const navigate = useNavigate();

    const deleteMutation = useMutation({
        mutationFn: async () => {
            await api.delete(`/tenants/${tenant.id}/permanent`);
        },
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['tenants'] });
            navigate('/superadmin/tenants');
        },
        onError: (err: any) => {
            setError(err.response?.data?.detail || 'Failed to delete tenant.');
        },
    });

    const handleClose = () => {
        setConfirmText('');
        setError('');
        onClose();
    };

    if (!isOpen) return null;

    const canDelete = confirmText === tenant.slug;

    return (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm">
            <div className="w-full max-w-md bg-white rounded-xl border border-zinc-200 shadow-2xl">
                <div className="flex items-center justify-between p-6 border-b border-zinc-200">
                    <h3 className="text-lg font-semibold text-red-700 flex items-center gap-2">
                        <AlertTriangle size={18} />
                        Delete Tenant Permanently
                    </h3>
                    <button onClick={handleClose} className="text-zinc-500 hover:text-zinc-800">
                        <X size={20} />
                    </button>
                </div>

                <div className="p-6 space-y-4">
                    <p className="text-sm text-zinc-600">
                        This permanently deletes <span className="font-medium text-zinc-900">{tenant.name}</span> and
                        all of its cases, artifacts, audit history, IOCs, invitations, copilot conversations,
                        playbooks, and webhooks. This cannot be undone.
                    </p>

                    <div className="space-y-2">
                        <label className="text-sm font-medium text-zinc-700">
                            Type <span className="font-mono text-red-700">{tenant.slug}</span> to confirm
                        </label>
                        <input
                            type="text"
                            autoFocus
                            className="w-full bg-white border border-zinc-200 rounded-md px-3 py-2 text-sm font-mono text-zinc-900 focus:outline-none focus:ring-2 focus:ring-red-500"
                            value={confirmText}
                            onChange={e => setConfirmText(e.target.value)}
                        />
                    </div>

                    {error && <p className="text-red-700 text-sm">{error}</p>}

                    <button
                        onClick={() => deleteMutation.mutate()}
                        disabled={!canDelete || deleteMutation.isPending}
                        className="w-full bg-red-600 hover:bg-red-700 text-white font-medium py-2 rounded-md transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                    >
                        {deleteMutation.isPending ? 'Deleting...' : 'Delete Permanently'}
                    </button>
                </div>
            </div>
        </div>
    );
}
