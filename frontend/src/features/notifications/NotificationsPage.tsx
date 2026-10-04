import { useState } from 'react';
import { useInfiniteQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import PageContainer from '../../components/layout/PageContainer';
import { cn } from '../../lib/utils';
import { NotificationRow } from './NotificationBell';
import { useOpenNotification } from './useOpenNotification';
import { PAGE_SIZE, fetchNotifications, markAllRead, useTenantId, useUnreadCount } from './api';

export default function NotificationsPage() {
    const [unreadOnly, setUnreadOnly] = useState(false);
    const qc = useQueryClient();
    const open = useOpenNotification();
    const { ready, tenantId } = useTenantId();
    const { data: count = 0 } = useUnreadCount();

    const q = useInfiniteQuery({
        queryKey: ['notifications', 'list', { tenantId, unreadOnly }],
        queryFn: ({ pageParam }) => fetchNotifications({ unreadOnly, beforeId: pageParam }),
        initialPageParam: undefined as number | undefined,
        getNextPageParam: last => (last.length < PAGE_SIZE ? undefined : last[last.length - 1].id),
        refetchOnWindowFocus: false,
        enabled: ready,
    });
    const readAll = useMutation({
        mutationFn: markAllRead,
        onSuccess: () => qc.invalidateQueries({ queryKey: ['notifications'] }),
    });

    const items = q.data?.pages.flat() ?? [];
    const tab = (active: boolean) => cn('px-3 py-1.5 text-sm border-b-2 -mb-px',
        active ? 'border-accent-600 text-zinc-900 font-medium' : 'border-transparent text-zinc-500 hover:text-zinc-900');

    return (
        <PageContainer width="narrow">
            <div className="flex items-center justify-between">
                <h1 className="text-lg font-semibold text-zinc-900">Notifications</h1>
                <button type="button" onClick={() => readAll.mutate()} disabled={count === 0 || readAll.isPending}
                    className="text-sm text-accent-600 hover:underline disabled:text-zinc-400 disabled:no-underline">Mark all read</button>
            </div>
            <div className="flex gap-1 border-b border-zinc-200">
                <button aria-pressed={!unreadOnly} className={tab(!unreadOnly)} onClick={() => setUnreadOnly(false)}>All</button>
                <button aria-pressed={unreadOnly} className={tab(unreadOnly)} onClick={() => setUnreadOnly(true)}>Unread</button>
            </div>
            <div className="bg-white border border-zinc-200">
                {q.isLoading ? (
                    <p className="px-3 py-8 text-center text-sm text-zinc-400">Loading…</p>
                ) : q.isError ? (
                    <p className="px-3 py-8 text-center text-sm text-zinc-500">Could not load notifications</p>
                ) : items.length === 0 ? (
                    <p className="px-3 py-8 text-center text-sm text-zinc-500">You're all caught up</p>
                ) : items.map(n => <NotificationRow key={n.id} n={n} onOpen={open} />)}
            </div>
            {q.hasNextPage && (
                <div className="flex justify-center">
                    <button type="button" onClick={() => q.fetchNextPage()} disabled={q.isFetchingNextPage}
                        className="px-4 py-1.5 text-sm border border-zinc-200 bg-white hover:bg-zinc-50 disabled:text-zinc-400">
                        {q.isFetchingNextPage ? 'Loading…' : 'Load more'}
                    </button>
                </div>
            )}
        </PageContainer>
    );
}
