import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Activity, AlertTriangle, AtSign, Bell, Clock, MessageSquare, UserPlus } from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import { cn } from '../../lib/utils';
import type { AppNotification } from '../../types';
import { useOpenNotification } from './useOpenNotification';
import { fetchNotifications, markAllRead, relativeTime, useTenantId, useUnreadCount } from './api';

const ICONS: Record<string, LucideIcon> = {
    mention: AtSign, assigned: UserPlus, comment: MessageSquare,
    status_change: Activity, severity_change: AlertTriangle, sla_breach: Clock,
};

export function NotificationRow({ n, onOpen, role }: { n: AppNotification; onOpen: (n: AppNotification) => void; role?: 'menuitem' }) {
    const Icon = ICONS[n.type] ?? Bell;
    const unread = !n.read_at;
    return (
        <button type="button" role={role} onClick={() => onOpen(n)}
            className={cn('w-full text-left flex items-start gap-3 px-3 py-2.5 border-b border-zinc-100 hover:bg-zinc-50 focus:bg-zinc-50 focus:outline-none',
                unread && 'bg-accent-50/40')}>
            <Icon size={15} className="mt-0.5 shrink-0 text-zinc-500" aria-hidden />
            <span className="flex-1 min-w-0">
                <span className={cn('block text-sm text-zinc-900 break-words', unread && 'font-medium')}>{n.summary}</span>
                <span className="block label-mono truncate mt-0.5">#{n.case_id} · {n.case_title ?? ''}</span>
            </span>
            <span className="shrink-0 flex items-center gap-2">
                <span className="num text-xs text-zinc-400">{relativeTime(n.created_at)}</span>
                {unread
                    ? <span className="w-1.5 h-1.5 bg-accent-600"><span className="sr-only">Unread</span></span>
                    : <span className="w-1.5 h-1.5" aria-hidden />}
            </span>
        </button>
    );
}

export default function NotificationBell() {
    const [open, setOpen] = useState(false);
    const ref = useRef<HTMLDivElement>(null);
    const btnRef = useRef<HTMLButtonElement>(null);
    const qc = useQueryClient();
    const { tenantId } = useTenantId();
    const { data: count = 0 } = useUnreadCount();
    const menuRef = useRef<HTMLDivElement>(null);
    const list = useQuery({
        queryKey: ['notifications', 'list', { tenantId, unreadOnly: false, panel: true }],
        queryFn: () => fetchNotifications({ limit: 20 }),
        enabled: open,
    });
    const readAll = useMutation({
        mutationFn: markAllRead,
        onSuccess: () => qc.invalidateQueries({ queryKey: ['notifications'] }),
    });
    const openNotification = useOpenNotification(() => setOpen(false));

    useEffect(() => {
        if (!open) return;
        const onDown = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false); };
        const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') { setOpen(false); btnRef.current?.focus(); } };
        document.addEventListener('mousedown', onDown);
        document.addEventListener('keydown', onKey);
        return () => { document.removeEventListener('mousedown', onDown); document.removeEventListener('keydown', onKey); };
    }, [open]);

    const items = list.data ?? [];
    // focus the first item once rows are rendered
    const firstId = items[0]?.id;
    useEffect(() => {
        if (open && firstId != null) menuRef.current?.querySelector<HTMLElement>('[role="menuitem"]')?.focus();
    }, [open, firstId]);
    const onMenuKey = (e: React.KeyboardEvent) => {
        if (e.key !== 'ArrowDown' && e.key !== 'ArrowUp') return;
        const els = Array.from(menuRef.current?.querySelectorAll<HTMLElement>('[role="menuitem"]') ?? []);
        if (!els.length) return;
        e.preventDefault();
        const i = els.indexOf(document.activeElement as HTMLElement);
        const next = e.key === 'ArrowDown' ? (i + 1) % els.length : (i <= 0 ? els.length - 1 : i - 1);
        els[next].focus();
    };
    return (
        <div ref={ref} className="relative">
            <button ref={btnRef} type="button" onClick={() => setOpen(o => !o)}
                aria-label={`Notifications, ${count} unread`} aria-haspopup="menu" aria-expanded={open}
                className="relative p-2 text-zinc-500 hover:text-zinc-900">
                <Bell size={17} />
                {count > 0 && (
                    <span className="absolute top-0.5 right-0 min-w-[16px] h-4 px-1 bg-accent-600 text-white num text-[10px] leading-4 text-center">
                        {count > 99 ? '99+' : count}
                    </span>
                )}
            </button>
            {open && (
                <div className="absolute right-0 top-full mt-1 w-[360px] max-w-[calc(100vw-2rem)] bg-white border border-zinc-200 shadow-lg z-50">
                    <div className="flex items-center justify-between px-3 py-2 border-b border-zinc-200">
                        <button type="button" onClick={() => readAll.mutate()} disabled={count === 0 || readAll.isPending}
                            className="text-xs text-accent-600 hover:underline disabled:text-zinc-400 disabled:no-underline">
                            Mark all read
                        </button>
                        <Link to="/notifications" onClick={() => setOpen(false)} className="text-xs text-accent-600 hover:underline">
                            View all
                        </Link>
                    </div>
                    {list.isLoading ? (
                        <p className="px-3 py-6 text-center text-sm text-zinc-400">Loading…</p>
                    ) : list.isError ? (
                        <p className="px-3 py-6 text-center text-sm text-zinc-500">Could not load notifications</p>
                    ) : items.length === 0 ? (
                        <p className="px-3 py-6 text-center text-sm text-zinc-500">You're all caught up</p>
                    ) : (
                        <div ref={menuRef} role="menu" aria-label="Notifications" onKeyDown={onMenuKey}
                            className="max-h-[420px] overflow-y-auto">
                            {items.map(n => <NotificationRow key={n.id} n={n} onOpen={openNotification} role="menuitem" />)}
                        </div>
                    )}
                </div>
            )}
        </div>
    );
}
