import { useQuery } from '@tanstack/react-query';
import { api } from '../../api/client';
import type { AppNotification, User } from '../../types';

export const PAGE_SIZE = 50;

export async function fetchNotifications(p: { unreadOnly?: boolean; limit?: number; beforeId?: number }) {
    const params: Record<string, unknown> = { limit: p.limit ?? PAGE_SIZE };
    if (p.unreadOnly) params.unread_only = true;
    if (p.beforeId != null) params.before_id = p.beforeId;
    return (await api.get('/notifications/', { params })).data as AppNotification[];
}

export const markRead = (id: number) => api.post(`/notifications/${id}/read`);
export const markAllRead = async () => (await api.post('/notifications/read-all')).data as { updated: number };

export function notificationHref(n: AppNotification) {
    return `/cases/${n.case_id}${n.timeline_event_id != null ? `#event-${n.timeline_event_id}` : ''}`;
}

export function useUnreadCount() {
    const { data: me } = useQuery({
        queryKey: ['currentUser'],
        queryFn: async () => (await api.get('/users/me')).data as User,
        staleTime: 300_000,
    });
    const tenantId = me?.active_tenant_id ?? null;
    return useQuery({
        queryKey: ['notifications', 'unread', tenantId],
        queryFn: async () => (await api.get('/notifications/unread-count')).data.count as number,
        enabled: !!me,
        refetchInterval: 30000,
        refetchOnWindowFocus: true,
    });
}

export function relativeTime(iso: string) {
    const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
    if (s < 60) return 'now';
    if (s < 3600) return `${Math.floor(s / 60)}m`;
    if (s < 86400) return `${Math.floor(s / 3600)}h`;
    if (s < 604800) return `${Math.floor(s / 86400)}d`;
    return new Date(iso).toLocaleDateString();
}
