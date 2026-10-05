import { useQuery } from '@tanstack/react-query';
import { api } from '../api/client';
import type { User } from '../types';

/** The signed-in user's chosen IANA timezone (null = browser default). Shares the ['currentUser'] query. */
export function useMyTimezone(): string | null {
    const { data } = useQuery({
        queryKey: ['currentUser'],
        queryFn: async () => (await api.get('/users/me')).data as User,
    });
    return data?.timezone ?? null;
}
