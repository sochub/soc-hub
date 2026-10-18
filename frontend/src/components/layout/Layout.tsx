import { Suspense, useEffect, useState } from 'react';
import PageSkeleton from './PageSkeleton';
import Avatar from '../Avatar';
import { Outlet, Link, useLocation, useNavigate } from 'react-router-dom';
import {
    LayoutDashboard, ShieldAlert, Settings, Bell, Database, Share2, LogOut,
    Inbox, Menu, X, PanelLeftClose, PanelLeftOpen, BookText, Code, Workflow, Search,
} from 'lucide-react';
import { cn } from '../../lib/utils';
import { useQuery } from '@tanstack/react-query';
import { api } from '../../api/client';
import type { User } from '../../types';
import CopilotWidget from '../../features/copilot/CopilotWidget';
import NotificationBell from '../../features/notifications/NotificationBell';
import TenantSwitcher from '../../features/tenants/TenantSwitcher';
import CommandPalette from './CommandPalette';

const NAV_GROUPS = [
    { label: 'operate', items: [
        { icon: LayoutDashboard, label: 'Overview', path: '/' },
        { icon: Inbox, label: 'My Queue', path: '/queue' },
        { icon: ShieldAlert, label: 'Cases', path: '/cases' },
        { icon: Bell, label: 'Alerts', path: '/alerts' },
    ] },
    { label: 'intel', items: [
        { icon: Database, label: 'Observables', path: '/observables' },
        { icon: Share2, label: 'Graph', path: '/graph' },
    ] },
    { label: 'automate', items: [
        { icon: BookText, label: 'Playbooks', path: '/playbooks' },
        { icon: Workflow, label: 'Workflows', path: '/workflows' },
    ] },
    { label: 'admin', items: [
        { icon: Settings, label: 'Settings', path: '/settings' },
    ] },
];
const ALL_NAV = NAV_GROUPS.flatMap(g => g.items);

const COLLAPSE_KEY = 'sidebar:collapsed';

export default function Layout() {
    const location = useLocation();
    const navigate = useNavigate();
    const [mobileOpen, setMobileOpen] = useState(false);
    const [collapsed, setCollapsed] = useState(() => localStorage.getItem(COLLAPSE_KEY) === '1');

    useEffect(() => setMobileOpen(false), [location.pathname]);
    useEffect(() => {
        localStorage.setItem(COLLAPSE_KEY, collapsed ? '1' : '0');
    }, [collapsed]);

    const { data: currentUser } = useQuery({
        queryKey: ['currentUser'],
        queryFn: async () => (await api.get('/users/me')).data as User,
        staleTime: 5 * 60 * 1000,
    });

    const handleLogout = () => {
        // Record the sign-out (best effort, never blocks): POST /auth/logout, 2s timeout.
        api.post('/auth/logout', null, { timeout: 2000 }).catch(() => undefined).finally(() => {
            localStorage.removeItem('token');
            navigate('/login', { replace: true });
        });
    };

    const displayName = currentUser?.full_name || currentUser?.email || 'User';
    const displayRole = currentUser?.role ? currentUser.role.replace('_', ' ') : '';

    const section = location.pathname === '/'
        ? 'overview'
        : location.pathname.split('/').slice(1, 3).join(' / ');

    const showLabels = !collapsed; // desktop labels; mobile drawer always expanded

    return (
        <div className="flex h-screen bg-canvas text-fg font-sans antialiased overflow-hidden">
            {/* Mobile overlay */}
            {mobileOpen && (
                <div className="fixed inset-0 z-30 bg-black/60 backdrop-blur-[2px] lg:hidden"
                    onClick={() => setMobileOpen(false)} aria-hidden="true" />
            )}

            {/* Sidebar */}
            <aside
                className={cn(
                    'fixed lg:relative z-40 h-full flex flex-col bg-surface border-r border-line',
                    'transition-[width,transform] duration-200 ease-out lg:translate-x-0',
                    collapsed ? 'lg:w-16' : 'lg:w-60',
                    'w-60',
                    mobileOpen ? 'translate-x-0' : '-translate-x-full',
                )}
                aria-label="Primary navigation"
            >
                {/* Brand + collapse */}
                <div className={cn('h-14 flex items-center border-b border-line shrink-0',
                    collapsed ? 'lg:justify-center px-3' : 'px-4 justify-between')}>
                    <Link to="/" className="flex items-center gap-2.5 min-w-0">
                        <span className="w-7 h-7 flex items-center justify-center rounded-md bg-brand-gradient font-mono text-[11px] font-bold shrink-0">
                            SH
                        </span>
                        {(showLabels || mobileOpen) && (
                            <span className="font-display font-semibold text-[17px] tracking-tight text-fg truncate">sochub</span>
                        )}
                    </Link>
                    <button onClick={() => setMobileOpen(false)}
                        className="lg:hidden p-1.5 text-muted hover:text-fg" aria-label="Close menu">
                        <X size={18} />
                    </button>
                    {!collapsed && (
                        <button onClick={() => setCollapsed(true)}
                            className="hidden lg:inline-flex p-1.5 text-muted hover:text-fg hover:bg-raised"
                            aria-label="Collapse sidebar" title="Collapse sidebar">
                            <PanelLeftClose size={16} />
                        </button>
                    )}
                </div>

                {/* Tenant switcher (hidden when collapsed on desktop) */}
                {(showLabels || mobileOpen) && (
                    <div className="px-3 py-3 border-b border-line">
                        <TenantSwitcher />
                    </div>
                )}

                {/* Nav */}
                <nav className="flex-1 overflow-y-auto py-2">
                    {NAV_GROUPS.map(group => (
                        <div key={group.label} className="mb-2">
                            {(showLabels || mobileOpen)
                                ? <p className="label-mono px-4 pt-2 pb-1.5">{group.label}</p>
                                : <div className="mx-4 my-2 border-t border-line hidden lg:block" />}
                            {group.items.map((item) => {
                                const Icon = item.icon;
                                const isActive = location.pathname === item.path
                                    || (item.path !== '/' && location.pathname.startsWith(item.path));
                                return (
                                    <Link key={item.path} to={item.path}
                                        aria-current={isActive ? 'page' : undefined}
                                        title={collapsed ? item.label : undefined}
                                        className={cn(
                                            'relative flex items-center gap-3 h-8 mx-2 px-3 text-sm rounded-md transition-colors',
                                            collapsed ? 'lg:justify-center lg:px-0 lg:mx-2' : '',
                                            isActive
                                                ? 'bg-accent/10 text-fg font-medium'
                                                : 'text-fg-3 hover:bg-raised hover:text-fg',
                                        )}>
                                        <Icon size={16} className={cn('shrink-0', isActive && 'text-accent')} />
                                        {(showLabels || mobileOpen) && <span className="truncate">{item.label}</span>}
                                    </Link>
                                );
                            })}
                        </div>
                    ))}
                </nav>

                {/* Expand button when collapsed */}
                {collapsed && (
                    <button onClick={() => setCollapsed(false)}
                        className="hidden lg:flex items-center justify-center h-10 border-t border-line text-muted hover:text-fg hover:bg-raised"
                        aria-label="Expand sidebar" title="Expand sidebar">
                        <PanelLeftOpen size={16} />
                    </button>
                )}

                {/* User */}
                <div className={cn('border-t border-line shrink-0',
                    collapsed ? 'lg:px-2 px-3 py-3' : 'px-3 py-3')}>
                    <div className={cn('flex items-center gap-2.5', collapsed ? 'lg:justify-center' : '')}>
                        <Link to="/settings/profile" className="flex items-center gap-2.5 min-w-0 flex-1 hover:opacity-80"
                            aria-label="Your profile" title="Your profile">
                            <Avatar userId={currentUser?.id} name={displayName}
                                hasAvatar={currentUser?.has_avatar} version={currentUser?.avatar_version} size={32} />
                            {(showLabels || mobileOpen) && (
                                <div className="flex-1 min-w-0">
                                    <p className="text-sm font-medium text-fg truncate leading-tight">{displayName}</p>
                                    <p className="label-mono leading-tight mt-0.5">{displayRole}</p>
                                </div>
                            )}
                        </Link>
                        {(showLabels || mobileOpen) && (
                            <>
                                <Link to="/docs" className="p-1.5 text-muted hover:text-fg hover:bg-raised rounded-md"
                                    aria-label="API docs" title="API docs">
                                    <Code size={15} />
                                </Link>
                                <button onClick={handleLogout}
                                    className="p-1.5 text-muted hover:text-severity-critical hover:bg-raised"
                                    aria-label="Logout" title="Logout">
                                    <LogOut size={15} />
                                </button>
                            </>
                        )}
                    </div>
                </div>
            </aside>

            {/* Main */}
            <div className="flex-1 flex flex-col h-full overflow-hidden">
                <header className="h-14 px-4 sm:px-6 flex items-center justify-between border-b border-line bg-canvas/80 backdrop-blur shrink-0">
                    <div className="flex items-center gap-3 min-w-0">
                        <button onClick={() => setMobileOpen(true)}
                            className="lg:hidden p-2 -ml-2 text-fg-3 hover:text-fg" aria-label="Open menu">
                            <Menu size={18} />
                        </button>
                        <nav aria-label="Breadcrumb" className="flex items-center gap-2 font-mono text-xs">
                            <span className="text-muted">~/</span>
                            <span className="text-fg font-medium">{section}</span>
                        </nav>
                    </div>
                    <div className="flex items-center gap-2">
                        <button type="button" onClick={() => window.dispatchEvent(new Event('palette:open'))}
                            className="flex items-center gap-2 h-8 pl-3 pr-2 text-sm text-muted bg-surface border border-line rounded-full hover:border-line-strong hover:text-fg-2 transition-colors">
                            <Search size={14} />
                            <span className="hidden sm:inline">Search…</span>
                            <kbd className="hidden sm:inline font-mono text-[10px] border border-line rounded px-1.5 py-0.5">⌘K</kbd>
                        </button>
                        <NotificationBell />
                    </div>
                </header>

                <main className="flex-1 overflow-y-auto console-grid">
                    <Suspense fallback={<PageSkeleton />}>
                        <Outlet />
                    </Suspense>
                </main>
            </div>

            <CopilotWidget />
            <CommandPalette nav={ALL_NAV} />
        </div>
    );
}
