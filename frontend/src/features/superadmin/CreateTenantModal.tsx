import { useState } from 'react';
import { X } from 'lucide-react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '../../api/client';

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

    if (!isOpen) return null;

    return (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm">
            <div className="w-full max-w-md bg-white rounded-xl border border-zinc-200 shadow-2xl">
                <div className="flex items-center justify-between p-6 border-b border-zinc-200">
                    <h3 className="text-lg font-semibold text-zinc-900">Create Tenant</h3>
                    <button onClick={handleClose} className="text-zinc-500 hover:text-zinc-800">
                        <X size={20} />
                    </button>
                </div>

                <form onSubmit={handleSubmit} className="p-6 space-y-4">
                    <div className="space-y-2">
                        <label className="text-sm font-medium text-zinc-700">Name</label>
                        <input
                            type="text"
                            required
                            className="w-full bg-white border border-zinc-200 rounded-md px-3 py-2 text-sm text-zinc-900 focus:outline-none focus:ring-2 focus:ring-accent-500"
                            placeholder="Acme Corp"
                            value={name}
                            onChange={e => handleNameChange(e.target.value)}
                        />
                    </div>

                    <div className="space-y-2">
                        <label className="text-sm font-medium text-zinc-700">Slug</label>
                        <input
                            type="text"
                            required
                            pattern="[a-z0-9-]+"
                            className="w-full bg-white border border-zinc-200 rounded-md px-3 py-2 text-sm text-zinc-900 focus:outline-none focus:ring-2 focus:ring-accent-500"
                            placeholder="acme-corp"
                            value={slug}
                            onChange={e => setSlug(e.target.value)}
                        />
                        <p className="text-xs text-zinc-400">URL-friendly identifier. Lowercase, numbers, hyphens only.</p>
                    </div>

                    {error && <p className="text-red-700 text-sm">{error}</p>}

                    <button
                        type="submit"
                        disabled={createMutation.isPending}
                        className="w-full bg-accent-600 hover:bg-accent-700 text-white font-medium py-2 rounded-md transition-colors disabled:opacity-50"
                    >
                        {createMutation.isPending ? 'Creating...' : 'Create Tenant'}
                    </button>
                </form>
            </div>
        </div>
    );
}
