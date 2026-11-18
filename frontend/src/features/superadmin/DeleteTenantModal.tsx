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
            navigate('/settings/tenants');
        },
        onError: (err: any) => {
            setError(err.response?.data?.detail || 'Could not delete the organization.');
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
            title={<span className="text-severity-critical flex items-center gap-2"><AlertTriangle size={16} />Delete organization permanently</span>}
            footer={<>
                <button onClick={handleClose} className={btnSecondary}>Cancel</button>
                <button
                    onClick={() => deleteMutation.mutate()}
                    disabled={!canDelete || deleteMutation.isPending}
                    className={btnDanger}
                >
                    {deleteMutation.isPending ? 'Deleting…' : 'Delete permanently'}
                </button>
            </>}
        >
            <div className="space-y-4">
                <p className="text-sm text-fg-2">
                    This permanently deletes <span className="font-medium text-fg">{tenant.name}</span> and
                    all of its cases, artifacts, audit history, IOCs, invitations, SOC-bot conversations,
                    playbooks, and webhooks. This cannot be undone.
                </p>

                <div>
                    <label htmlFor="delete-tenant-confirm" className={modalLabel}>
                        Type <span className="font-mono text-severity-critical">{tenant.slug}</span> to confirm
                    </label>
                    <input
                        id="delete-tenant-confirm"
                        type="text"
                        className={`${modalInput} font-mono`}
                        value={confirmText}
                        onChange={e => setConfirmText(e.target.value)}
                    />
                </div>

                {error && <p role="alert" className="text-severity-critical text-sm">{error}</p>}
            </div>
        </Modal>
    );
}
