import { useState } from 'react';
import { useInfiniteQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import PageContainer from '../../components/layout/PageContainer';
import { Button, FolderTabs, PageHeader, Skeleton, ErrorState, EmptyState } from '../../components/ui';
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
    const markAll = (
        <Button size="sm" variant="ghost" onClick={() => readAll.mutate()} disabled={count === 0 || readAll.isPending}>Mark all read</Button>
    );

    const body = (
        <>
            {bare && (
                <div className="flex items-center justify-between">
                    <h2 className="text-sm font-semibold text-fg">Inbox</h2>
                    {markAll}
                </div>
            )}
            <FolderTabs label="Notification filter" idPrefix="notif" value={unreadOnly ? 'unread' : 'all'}
                onChange={v => setUnreadOnly(v === 'unread')}
                items={[{ value: 'all', label: 'All' }, { value: 'unread', label: 'Unread', count: count || undefined }]} />
            <div id="notif-panel" role="tabpanel" aria-labelledby={`notif-${unreadOnly ? 'unread' : 'all'}`} className="bg-panel border border-line rounded-md overflow-hidden">
                {q.isLoading ? (
                    <Skeleton rows={5} className="p-3" />
                ) : q.isError ? (
                    <ErrorState message="Could not load notifications." onRetry={() => q.refetch()} className="m-3" />
                ) : items.length === 0 ? (
                    <EmptyState title="You're all caught up" />
                ) : items.map(n => <NotificationRow key={n.id} n={n} onOpen={open} />)}
            </div>
            {q.hasNextPage && (
                <div className="flex justify-center">
                    <Button size="sm" variant="secondary" onClick={() => q.fetchNextPage()} disabled={q.isFetchingNextPage}>
                        {q.isFetchingNextPage ? 'Loading more…' : 'Load more'}
                    </Button>
                </div>
            )}
        </>
    );
    return bare ? <div className="space-y-3">{body}</div> : (
        <PageContainer width="narrow">
            <PageHeader title="Notifications" actions={markAll} />
            <div className="space-y-3">{body}</div>
        </PageContainer>
    );
}
