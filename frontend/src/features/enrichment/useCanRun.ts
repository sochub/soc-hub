import { useQuery } from '@tanstack/react-query';
import { api } from '../../api/client';
import type { User } from '../../types';

/** Admins, analysts and super admins may trigger lookups; viewers may not. */
export function useCanRun(): boolean {
    const { data: me } = useQuery({ queryKey: ['currentUser'], queryFn: async () => (await api.get('/users/me')).data as User });
    return !!me && (me.role === 'admin' || me.role === 'analyst' || !!me.is_super_admin);
}
