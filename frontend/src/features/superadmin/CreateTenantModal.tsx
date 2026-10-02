import { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '../../api/client';
import Modal, { modalInput, modalLabel, btnPrimary, btnSecondary } from '../../components/layout/Modal';

interface CreateTenantModalProps {
    isOpen: boolean;
    onClose: () => void;
}

export default function CreateTenantModal({ isOpen, onClose }: CreateTenantModalProps) {
    const [name, setName] = useState('');
    const [slug, setSlug] = useState('');
    const [error, setError] = useState('');
    const queryClient = useQueryClient();

    const createMutation = useMutation({
        mutationFn: async (data: { name: string; slug: string }) => {
            const response = await api.post('/tenants/', data);
            return response.data;
        },
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['tenants'] });
            handleClose();
        },
        onError: (err: any) => {
            setError(err.response?.data?.detail || 'Failed to create tenant.');
        },
    });

    const handleClose = () => {
        setName('');
        setSlug('');
        setError('');
        onClose();
    };

    const handleNameChange = (value: string) => {
        setName(value);
        setSlug(value.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, ''));
    };

    const handleSubmit = (e: React.FormEvent) => {
        e.preventDefault();
        createMutation.mutate({ name, slug });
    };

    return (
        <Modal
            open={isOpen}
            onClose={handleClose}
            title="Create Tenant"
            size="lg"
            footer={<>
                <button type="button" onClick={handleClose} className={btnSecondary}>Cancel</button>
                <button
                    type="submit"
                    form="create-tenant-form"
                    disabled={createMutation.isPending}
                    className={btnPrimary}
                >
                    {createMutation.isPending ? 'Creating...' : 'Create Tenant'}
                </button>
            </>}
        >
            <form id="create-tenant-form" onSubmit={handleSubmit} className="space-y-4">
                <div>
                    <label className={modalLabel}>Name</label>
                    <input
                        type="text"
                        required
                        className={modalInput}
                        placeholder="Acme Corp"
                        value={name}
                        onChange={e => handleNameChange(e.target.value)}
                    />
                </div>

                <div>
                    <label className={modalLabel}>Slug</label>
                    <input
                        type="text"
                        required
                        pattern="[a-z0-9-]+"
                        className={`${modalInput} font-mono`}
                        placeholder="acme-corp"
                        value={slug}
                        onChange={e => setSlug(e.target.value)}
                    />
                    <p className="text-xs text-zinc-400 mt-1">URL-friendly identifier. Lowercase, numbers, hyphens only.</p>
                </div>

                {error && <p className="text-red-700 text-sm">{error}</p>}
            </form>
        </Modal>
    );
}
