import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Link, useNavigate } from 'react-router-dom';
import { Plus } from 'lucide-react';
import { api } from '../../api/client';
import type { Tenant } from '../../types';
import CreateTenantModal from './CreateTenantModal';
import { Button, EmptyState, ErrorState, Skeleton, Stamp } from '../../components/ui';
import SettingsPageHeader from '../settings/SettingsPageHeader';

export default function TenantManagement() {
    const [showCreateModal, setShowCreateModal] = useState(false);
    const navigate = useNavigate();

    const { data: tenants = [], isLoading, isError, refetch } = useQuery({
        queryKey: ['tenants'],
        queryFn: async () => {
            const response = await api.get('/tenants/');
            return response.data as Tenant[];
        },
    });

    return (
        <div className="space-y-6">
            <SettingsPageHeader title="Organizations" description="Every organization on this platform."
                actions={<Button onClick={() => setShowCreateModal(true)}><Plus size={16} />Create organization</Button>} />

            {isError ? <ErrorState message="Could not load organizations." onRetry={() => refetch()} /> : (
                <div className="bg-panel border border-line rounded-md overflow-x-auto">
                    {isLoading ? <Skeleton rows={4} className="p-3" /> : tenants.length === 0 ? (
                        <EmptyState title="No organizations yet" />
                    ) : (
                        <table className="w-full text-sm">
                            <thead>
                                <tr className="border-b border-line label-caps text-left">
                                    <th className="px-3 py-2">Name</th>
                                    <th className="px-3 py-2">Slug</th>
                                    <th className="px-3 py-2">Status</th>
                                </tr>
                            </thead>
                            <tbody className="divide-y divide-line">
                                {tenants.map(tenant => (
                                    <tr key={tenant.id} className="hover:bg-raised cursor-pointer" onClick={() => navigate(`/settings/tenants/${tenant.id}`)}>
                                        <td className="px-3 py-2.5">
                                            <Link to={`/settings/tenants/${tenant.id}`} onClick={(e) => e.stopPropagation()} className="font-medium text-fg hover:underline">
                                                {tenant.name}
                                            </Link>
                                        </td>
                                        <td className="px-3 py-2.5 font-mono text-xs text-fg-3">{tenant.slug}</td>
                                        <td className="px-3 py-2.5">
                                            <Stamp tone={tenant.is_active ? 'ok' : 'critical'}>{tenant.is_active ? 'Active' : 'Inactive'}</Stamp>
                                        </td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    )}
                </div>
            )}

            <CreateTenantModal isOpen={showCreateModal} onClose={() => setShowCreateModal(false)} />
        </div>
    );
}
