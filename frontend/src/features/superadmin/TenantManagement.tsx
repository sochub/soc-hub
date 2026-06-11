import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import { Building2, Plus } from 'lucide-react';
import { api } from '../../api/client';
import type { Tenant } from '../../types';
import CreateTenantModal from './CreateTenantModal';

export default function TenantManagement() {
    const [showCreateModal, setShowCreateModal] = useState(false);
    const navigate = useNavigate();

    const { data: tenants = [], isLoading } = useQuery({
        queryKey: ['tenants'],
        queryFn: async () => {
            const response = await api.get('/tenants/');
            return response.data as Tenant[];
        },
    });

    return (
        <div className="space-y-6">
            <div className="flex items-center justify-between">
                <div>
                    <h1 className="text-2xl font-bold text-zinc-900">Tenants</h1>
                    <p className="text-sm text-zinc-500 mt-1">Manage platform tenants</p>
                </div>
                <button
                    onClick={() => setShowCreateModal(true)}
                    className="flex items-center gap-2 px-4 py-2 bg-accent-600 hover:bg-accent-700 text-white rounded-lg text-sm font-medium transition-colors"
                >
                    <Plus size={16} />
                    Create Tenant
                </button>
            </div>

            <div className="grid gap-4">
                {isLoading ? (
                    <div className="flex justify-center py-8">
                        <div className="animate-spin rounded-full h-6 w-6 border-t-2 border-b-2 border-blue-500" />
                    </div>
                ) : tenants.map(tenant => (
                    <button
                        key={tenant.id}
                        onClick={() => navigate(`/superadmin/tenants/${tenant.id}`)}
                        className="flex items-center gap-4 p-4 bg-white border border-zinc-200 rounded-xl hover:bg-zinc-100 transition-colors text-left w-full"
                    >
                        <div className="w-10 h-10 rounded-lg bg-zinc-100 flex items-center justify-center shrink-0">
                            <Building2 size={20} className="text-zinc-500" />
                        </div>
                        <div className="flex-1 min-w-0">
                            <p className="text-sm font-medium text-zinc-800">{tenant.name}</p>
                            <p className="text-xs text-zinc-400">{tenant.slug}</p>
                        </div>
                        <span className={`px-2.5 py-1 rounded-full text-xs font-medium ${
                            tenant.is_active
                                ? 'bg-green-50 text-green-700 border border-green-200'
                                : 'bg-red-50 text-red-700 border border-red-200'
                        }`}>
                            {tenant.is_active ? 'Active' : 'Inactive'}
                        </span>
                    </button>
                ))}
            </div>

            <CreateTenantModal isOpen={showCreateModal} onClose={() => setShowCreateModal(false)} />
        </div>
    );
}
