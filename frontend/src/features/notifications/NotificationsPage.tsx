import { useState } from 'react';
import { useInfiniteQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import PageContainer from '../../components/layout/PageContainer';
import { cn } from '../../lib/utils';
import { NotificationRow } from './NotificationBell';
import { useOpenNotification } from './useOpenNotification';
import { PAGE_SIZE, fetchNotifications, markAllRead, useTenantId, useUnreadCount } from './api';

/** `bare` drops the page wrapper so My Queue can embed the inbox. */
export default function NotificationsPage({ bare = false }: { bare?: boolean }) {
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
        active ? 'border-accent text-fg font-medium' : 'border-transparent text-fg-3 hover:text-fg');

    const body = (
        <>
            <div className="flex items-center justify-between">
                <h1 className={bare ? 'label-mono' : 'text-lg font-semibold text-fg'}>{bare ? '// inbox' : 'Notifications'}</h1>
                <button type="button" onClick={() => readAll.mutate()} disabled={count === 0 || readAll.isPending}
                    className="text-sm text-accent hover:underline disabled:text-muted disabled:no-underline">Mark all read</button>
            </div>
            <div className="flex gap-1 border-b border-line">
                <button aria-pressed={!unreadOnly} className={tab(!unreadOnly)} onClick={() => setUnreadOnly(false)}>All</button>
                <button aria-pressed={unreadOnly} className={tab(unreadOnly)} onClick={() => setUnreadOnly(true)}>Unread</button>
            </div>
            <div className="bg-panel border border-line">
                {q.isLoading ? (
                    <p className="px-3 py-8 text-center text-sm text-muted">Loading…</p>
                ) : q.isError ? (
                    <p className="px-3 py-8 text-center text-sm text-fg-3">Could not load notifications</p>
                ) : items.length === 0 ? (
                    <p className="px-3 py-8 text-center text-sm text-fg-3">You're all caught up</p>
                ) : items.map(n => <NotificationRow key={n.id} n={n} onOpen={open} />)}
            </div>
            {q.hasNextPage && (
                <div className="flex justify-center">
                    <button type="button" onClick={() => q.fetchNextPage()} disabled={q.isFetchingNextPage}
                        className="px-4 py-1.5 text-sm border border-line bg-panel hover:bg-surface disabled:text-muted">
                        {q.isFetchingNextPage ? 'Loading…' : 'Load more'}
                    </button>
                </div>
            )}
        </>
    );
    return bare ? <div className="space-y-3">{body}</div> : <PageContainer width="narrow">{body}</PageContainer>;
}
