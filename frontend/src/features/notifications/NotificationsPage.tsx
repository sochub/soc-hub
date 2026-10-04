import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import PageContainer from '../../components/layout/PageContainer';
import { cn } from '../../lib/utils';
import type { AppNotification } from '../../types';
import { NotificationRow } from './NotificationBell';
import { useOpenNotification } from './useOpenNotification';
import { PAGE_SIZE, fetchNotifications, markAllRead } from './api';

export default function NotificationsPage() {
    const [unreadOnly, setUnreadOnly] = useState(false);
    const [extra, setExtra] = useState<AppNotification[]>([]);
    const [exhausted, setExhausted] = useState(false);
    const [loadingMore, setLoadingMore] = useState(false);
    const qc = useQueryClient();
    const open = useOpenNotification();

    const first = useQuery({
        queryKey: ['notifications', 'list', { unreadOnly }],
        queryFn: () => fetchNotifications({ unreadOnly }),
    });
    const readAll = useMutation({
        mutationFn: markAllRead,
        onSuccess: () => { setExtra([]); setExhausted(false); qc.invalidateQueries({ queryKey: ['notifications'] }); },
    });

    const head = first.data ?? [];
    const items = [...head, ...extra.filter(e => !head.some(h => h.id === e.id))];
    const done = exhausted || (first.isSuccess && head.length < PAGE_SIZE && extra.length === 0);

    const switchTab = (v: boolean) => { setUnreadOnly(v); setExtra([]); setExhausted(false); };
    const loadMore = async () => {
        const last = items[items.length - 1];
        if (!last) return;
        setLoadingMore(true);
        try {
            const page = await fetchNotifications({ unreadOnly, beforeId: last.id });
            setExtra(e => [...e, ...page]);
            if (page.length < PAGE_SIZE) setExhausted(true);
        } finally { setLoadingMore(false); }
    };

    const tab = (active: boolean) => cn('px-3 py-1.5 text-sm border-b-2 -mb-px',
        active ? 'border-accent-600 text-zinc-900 font-medium' : 'border-transparent text-zinc-500 hover:text-zinc-900');

    return (
        <PageContainer width="narrow">
            <div className="flex items-center justify-between">
                <h1 className="text-lg font-semibold text-zinc-900">Notifications</h1>
                <button type="button" onClick={() => readAll.mutate()} disabled={readAll.isPending}
                    className="text-sm text-accent-600 hover:underline disabled:text-zinc-400">Mark all read</button>
            </div>
            <div role="tablist" className="flex gap-1 border-b border-zinc-200">
                <button role="tab" aria-selected={!unreadOnly} className={tab(!unreadOnly)} onClick={() => switchTab(false)}>All</button>
                <button role="tab" aria-selected={unreadOnly} className={tab(unreadOnly)} onClick={() => switchTab(true)}>Unread</button>
            </div>
            <div role="menu" className="bg-white border border-zinc-200">
                {first.isLoading ? (
                    <p className="px-3 py-8 text-center text-sm text-zinc-400">Loading…</p>
                ) : first.isError ? (
                    <p className="px-3 py-8 text-center text-sm text-zinc-500">Could not load notifications</p>
                ) : items.length === 0 ? (
                    <p className="px-3 py-8 text-center text-sm text-zinc-500">You're all caught up</p>
                ) : items.map(n => <NotificationRow key={n.id} n={n} onOpen={open} />)}
            </div>
            {!done && items.length > 0 && (
                <div className="flex justify-center">
                    <button type="button" onClick={loadMore} disabled={loadingMore}
                        className="px-4 py-1.5 text-sm border border-zinc-200 bg-white hover:bg-zinc-50 disabled:text-zinc-400">
                        {loadingMore ? 'Loading…' : 'Load more'}
                    </button>
                </div>
            )}
        </PageContainer>
    );
}
