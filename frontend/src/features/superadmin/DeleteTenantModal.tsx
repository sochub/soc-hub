import { useState } from 'react';
import { AlertTriangle } from 'lucide-react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import { api } from '../../api/client';
import type { Tenant } from '../../types';
import Modal, { modalInput, modalLabel, btnDanger, btnSecondary } from '../../components/layout/Modal';

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

    const canDelete = confirmText === tenant.slug;

    return (
        <Modal
            open={isOpen}
            onClose={handleClose}
            size="md"
            title={<span className="text-red-700 flex items-center gap-2"><AlertTriangle size={16} />Delete Tenant Permanently</span>}
            footer={<>
                <button onClick={handleClose} className={btnSecondary}>Cancel</button>
                <button
                    onClick={() => deleteMutation.mutate()}
                    disabled={!canDelete || deleteMutation.isPending}
                    className={btnDanger}
                >
                    {deleteMutation.isPending ? 'Deleting...' : 'Delete Permanently'}
                </button>
            </>}
        >
            <div className="space-y-4">
                <p className="text-sm text-zinc-600">
                    This permanently deletes <span className="font-medium text-zinc-900">{tenant.name}</span> and
                    all of its cases, artifacts, audit history, IOCs, invitations, copilot conversations,
                    playbooks, and webhooks. This cannot be undone.
                </p>

                <div>
                    <label className={modalLabel}>
                        Type <span className="font-mono text-red-700">{tenant.slug}</span> to confirm
                    </label>
                    <input
                        type="text"
                        className={`${modalInput} font-mono focus:border-red-500 focus:ring-red-500`}
                        value={confirmText}
                        onChange={e => setConfirmText(e.target.value)}
                    />
                </div>

                {error && <p className="text-red-700 text-sm">{error}</p>}
            </div>
        </Modal>
    );
}
