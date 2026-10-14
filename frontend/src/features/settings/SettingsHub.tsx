import { useQuery } from '@tanstack/react-query';
import { api } from '../../api/client';
import HubLayout from '../../components/layout/HubLayout';
import type { User } from '../../types';

export default function SettingsHub() {
    const { data: me } = useQuery({
        queryKey: ['currentUser'],
        queryFn: async () => (await api.get('/users/me')).data as User,
        staleTime: 300_000,
    });
    const isAdmin = me?.role === 'admin' || !!me?.is_super_admin;
    return (
        <HubLayout title="settings" tabs={[
            { label: 'Tenant', to: '/settings', end: true },
            { label: 'Profile', to: '/settings/profile' },
            { label: 'Integrations', to: '/settings/integrations' },
            { label: 'Users', to: '/settings/users', show: isAdmin },
            { label: 'Tenants', to: '/settings/tenants', show: !!me?.is_super_admin },
        ]} />
    );
}
