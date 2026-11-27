import { Suspense, useEffect, useRef, useState } from 'react';
import PageSkeleton from './PageSkeleton';
import Avatar from '../Avatar';
import { Outlet, Link, useLocation, useNavigate } from 'react-router-dom';
import {
    LayoutDashboard, ShieldAlert, Settings, Bell, Database, Share2, LogOut,
    Inbox, Menu, X, Sun, Moon, Monitor, PanelLeftClose, PanelLeftOpen, BookText, Code, Workflow, Search, Building2, Plus, Sparkles, Newspaper, Crosshair,
} from 'lucide-react';
import { cn } from '../../lib/utils';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { api, session } from '../../api/client';
import type { Tenant, User } from '../../types';
import CopilotWidget from '../../features/copilot/CopilotWidget';
import NotificationBell from '../../features/notifications/NotificationBell';
import TenantSwitcher from '../../features/tenants/TenantSwitcher';
import CommandPalette from './CommandPalette';
import { DEMO, DEMO_REPO_URL } from '../../demo';

const NAV_GROUPS = [
    { label: 'Operate', items: [
        { icon: LayoutDashboard, label: 'Overview', path: '/' },
        { icon: Inbox, label: 'My queue', path: '/queue' },
        { icon: Building2, label: 'Organizations', path: '/portfolio', multiTenant: true },
        { icon: ShieldAlert, label: 'Cases', path: '/cases' },
        { icon: Bell, label: 'Alerts', path: '/alerts' },
    ] },
    { label: 'Intel', items: [
        { icon: Database, label: 'Observables', path: '/observables' },
        { icon: Share2, label: 'Graph', path: '/graph' },
        { icon: Newspaper, label: 'Briefs', path: '/briefs', briefs: true },
    ] },
    { label: 'Automate', items: [
        { icon: BookText, label: 'Playbooks', path: '/playbooks' },
        { icon: Workflow, label: 'Workflows', path: '/workflows' },
    ] },
    { label: 'Admin', items: [
        { icon: Settings, label: 'Settings', path: '/settings' },
    ] },
];
const ALL_NAV = NAV_GROUPS.flatMap(g => g.items);

const COLLAPSE_KEY = 'sidebar:collapsed';
const THEME_KEY = 'theme'; // per-viewer convenience: 'system' | 'light' | 'dark'
const THEMES = ['system', 'light', 'dark'] as const;
type Theme = typeof THEMES[number];
function applyTheme(t: Theme) {
    if (t === 'system') document.documentElement.removeAttribute('data-theme');
    else document.documentElement.setAttribute('data-theme', t);
}

export default function Layout() {
    const location = useLocation();
    const navigate = useNavigate();
    const [mobileOpen, setMobileOpen] = useState(false);
    const [collapsed, setCollapsed] = useState(() => localStorage.getItem(COLLAPSE_KEY) === '1');
    const [theme, setTheme] = useState<Theme>(() => {
        try { const v = localStorage.getItem(THEME_KEY); return (THEMES as readonly string[]).includes(v ?? '') ? v as Theme : 'system'; } catch { return 'system'; }
    });
    useEffect(() => { applyTheme(theme); try { localStorage.setItem(THEME_KEY, theme); } catch { /* storage blocked: theme still applies this session */ } }, [theme]);
    const ThemeIcon = theme === 'light' ? Sun : theme === 'dark' ? Moon : Monitor;

    useEffect(() => setMobileOpen(false), [location.pathname]);
    useEffect(() => {
        localStorage.setItem(COLLAPSE_KEY, collapsed ? '1' : '0');
    }, [collapsed]);

    const { data: currentUser } = useQuery({
        queryKey: ['currentUser'],
        queryFn: async () => (await api.get('/users/me')).data as User,
        staleTime: 5 * 60 * 1000,
        refetchOnWindowFocus: 'always',  // another tab may have switched organization
    });

    // The active organization changed under this tab (switched in another one): its cached data is stale.
    // Security briefs is per organization and off by default: the nav entry and its unread badge exist only when on.
    const briefsOn = !!currentUser?.briefs_enabled;
    const { data: briefsUnread } = useQuery({
        queryKey: ['briefs', 'unread'], enabled: briefsOn, refetchInterval: 5 * 60 * 1000,
        queryFn: async () => (await api.get('/briefs/unread-count')).data as { count: number; capped: boolean; matched_unread?: number },
    });
    const qc = useQueryClient();
    const seenTenant = useRef<number | null | undefined>(undefined);
    const activeTenant = currentUser?.active_tenant_id;
    useEffect(() => {
        const prev = seenTenant.current;
        seenTenant.current = activeTenant;
        if (prev != null && activeTenant != null && prev !== activeTenant) {
            qc.clear();
            navigate('/');
        }
    }, [activeTenant, qc, navigate]);

    const handleLogout = () => {
        if (DEMO) { window.location.assign('/'); return; }  // demo: back to the website
        // Record the sign-out (best effort, never blocks): POST /auth/logout, 2s timeout.
        api.post('/auth/logout', null, { timeout: 2000 }).catch(() => undefined).finally(() => {
            session.clear();
            navigate('/login', { replace: true });
        });
    };

    // Super admins may sit in a tenant they're not a member of: same cached list the TenantSwitcher loads.
    const { data: allTenants } = useQuery({
        queryKey: ['tenants', 'all'],
        queryFn: async () => (await api.get('/tenants/')).data as Tenant[],
        enabled: !!currentUser?.is_super_admin,
        staleTime: 5 * 60 * 1000,
    });
    const activeId = currentUser?.active_tenant_id;
    const tenantName = currentUser?.memberships?.find((m) => m.tenant_id === activeId)?.tenant_name
        ?? allTenants?.find((t) => t.id === activeId)?.name;
    const appName = tenantName ? `sochub - ${tenantName}` : 'sochub';
    useEffect(() => { document.title = tenantName ? `sochub - ${tenantName}` : 'sochub'; }, [tenantName]);

    const displayName = currentUser?.full_name || currentUser?.email || 'User';
    const displayRole = currentUser?.role ? currentUser.role.replace('_', ' ') : '';

    // Path segments whose capitalised form would be wrong (acronyms).
    const CRUMB_LABELS: Record<string, string> = { iocs: 'IOCs', api: 'API', sso: 'SSO', mfa: 'MFA', sla: 'SLA' };
    // Breadcrumb: the nav label for the section, then the record id or sub-page.
    const parts = location.pathname.split('/').filter(Boolean);
    const navItem = ALL_NAV.find((i) => i.path !== '/' && location.pathname.startsWith(i.path));
    const crumbs = parts.length === 0 ? ['Overview']
        : [navItem?.label ?? parts[0], ...parts.slice(1, 3).map((p) => (/^\d+$/.test(p) ? `#${p}` : CRUMB_LABELS[p] ?? p.replace(/[-_]/g, ' ')))];
    const orgInitials = (tenantName ?? 'sochub').split(/\s+/).map((w) => w[0]).join('').slice(0, 2).toUpperCase();

    const showLabels = !collapsed; // desktop labels; mobile drawer always expanded

    return (
        <div className="flex h-screen bg-canvas text-fg font-sans antialiased overflow-hidden">
            <a href="#main" onClick={(e) => { e.preventDefault(); document.getElementById('main')?.focus(); }}
                className="sr-only focus:not-sr-only focus:fixed focus:left-3 focus:top-3 focus:z-[100] focus:px-3 focus:py-2 focus:rounded-md focus:bg-accent focus:text-on-accent focus:text-sm focus:font-semibold">
                Skip to content
            </a>
            {/* Mobile overlay */}
            {mobileOpen && (
                <div className="fixed inset-0 z-30 bg-fg/40 lg:hidden"
                    onClick={() => setMobileOpen(false)} aria-hidden="true" />
            )}

            {/* Sidebar */}
            <aside
                className={cn(
                    // Ink rail in both themes: the white logo sits on black, as on sochub.io.
                    'fixed lg:relative z-40 h-full flex flex-col bg-rail text-rail-ink border-r border-rail-line',
                    'transition-[width,transform] duration-200 ease-out lg:translate-x-0',
                    collapsed ? 'lg:w-16' : 'lg:w-60',
                    'w-60',
                    mobileOpen ? 'translate-x-0' : '-translate-x-full',
                )}
                aria-label="Primary navigation"
            >
                {/* Brand + collapse */}
                <div className={cn('h-14 flex items-center border-b border-rail-line shrink-0',
                    collapsed ? 'lg:justify-center px-3' : 'px-4 justify-between')}>
                    <Link to="/" className="flex items-center gap-2.5 min-w-0">
                        <img src={`${import.meta.env.BASE_URL}logo.png`} alt="" className="w-7 h-7 shrink-0" />
                        {(showLabels || mobileOpen) && (
                            <span className="font-semibold text-[17px] tracking-tight text-rail-ink truncate" title={appName}>{appName}</span>
                        )}
                    </Link>
                    <button onClick={() => setMobileOpen(false)}
                        className="lg:hidden p-1.5 rounded-sm text-rail-ink/70 hover:text-rail-ink" aria-label="Close menu">
                        <X size={18} />
                    </button>
                    {!collapsed && (
                        <button onClick={() => setCollapsed(true)}
                            className="hidden lg:inline-flex p-1.5 rounded-sm text-rail-ink/70 hover:text-rail-ink hover:bg-rail-2"
                            aria-label="Collapse sidebar" title="Collapse sidebar">
                            <PanelLeftClose size={16} />
                        </button>
                    )}
                </div>

                {/* Tenant switcher (hidden when collapsed on desktop) */}
                {(showLabels || mobileOpen) ? <TenantSwitcher /> : (
                    <div className="hidden lg:flex justify-center py-2 border-b border-rail-line" title={tenantName ?? undefined}>
                        <span className="grid place-items-center w-8 h-8 rounded-sm border border-rail-line bg-rail-2 text-[11px] font-bold" aria-label={`Organization: ${tenantName ?? 'sochub'}`}>{orgInitials}</span>
                    </div>
                )}

                {/* Nav */}
                <nav className="flex-1 overflow-y-auto py-2">
                    {NAV_GROUPS.map(group => (
                        <div key={group.label} className="mb-2">
                            {(showLabels || mobileOpen)
                                ? <p className="label-caps !text-rail-ink/50 px-4 pt-2 pb-1.5">{group.label}</p>
                                : <div className="mx-4 my-2 border-t border-rail-line hidden lg:block" />}
                            {group.items.filter((i) => !('multiTenant' in i) || !!currentUser?.is_super_admin || (currentUser?.memberships?.length ?? 0) > 1)
                                .filter((i) => !('briefs' in i) || briefsOn).map((item) => {
                                const Icon = item.icon;
                                const unread = 'briefs' in item && briefsUnread?.count ? `${briefsUnread.capped ? '99+' : briefsUnread.count}` : null;
                                // Unread briefs that match this organization's observables: the badge inverts and says so.
                                const matched = unread ? briefsUnread?.matched_unread ?? 0 : 0;
                                const unreadLabel = matched > 0 ? `${matched >= 100 ? '99+' : matched} unread brief${matched === 1 ? '' : 's'} match${matched === 1 ? 'es' : ''} your observables` : `${unread} unread`;
                                const isActive = location.pathname === item.path
                                    || (item.path !== '/' && location.pathname.startsWith(item.path));
                                return (
                                    <Link key={item.path} to={item.path}
                                        aria-current={isActive ? 'page' : undefined}
                                        title={unread && (collapsed || matched > 0) ? `${item.label} (${unreadLabel})` : collapsed ? item.label : undefined}
                                        className={cn(
                                            // Active item: lifted row, lavender icon and a lime signal bar on the left edge (the website's dot).
                                            'relative flex items-center gap-3 h-8 mx-2 px-3 rounded-md text-[13.5px] transition-colors focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-lavender',
                                            collapsed ? 'lg:justify-center lg:px-0' : '',
                                            isActive
                                                ? "bg-rail-2 text-rail-ink font-medium before:absolute before:left-0 before:top-1.5 before:bottom-1.5 before:w-0.5 before:rounded-full before:bg-signal before:content-['']"
                                                : 'text-rail-ink/70 hover:bg-rail-2 hover:text-rail-ink',
                                        )}>
                                        <Icon size={16} className={cn('shrink-0', isActive && 'text-lavender')} aria-hidden />
                                        {(showLabels || mobileOpen) && <span className="truncate">{item.label}</span>}
                                        {unread && (showLabels || mobileOpen
                                            ? <span className={cn('num ml-auto inline-flex items-center gap-1 rounded-sm px-1.5 text-[11px] font-semibold', matched > 0 ? 'bg-rail-ink text-rail' : 'bg-rail-2 text-rail-ink')}>
                                                {matched > 0 && <Crosshair size={10} aria-hidden />}<span aria-hidden>{unread}</span><span className="sr-only">{unreadLabel}</span></span>
                                            : <span className="sr-only">{unreadLabel}</span>)}
                                    </Link>
                                );
                            })}
                        </div>
                    ))}
                </nav>

                {/* Expand button when collapsed */}
                {collapsed && (
                    <button onClick={() => setCollapsed(false)}
                        className="hidden lg:flex items-center justify-center h-10 border-t border-rail-line text-rail-ink/70 hover:text-rail-ink hover:bg-rail-2"
                        aria-label="Expand sidebar" title="Expand sidebar">
                        <PanelLeftOpen size={16} />
                    </button>
                )}

                {/* User */}
                <div className={cn('border-t border-rail-line shrink-0',
                    collapsed ? 'lg:px-2 px-3 py-3' : 'px-3 py-3')}>
                    <div className={cn('flex items-center gap-2.5', collapsed ? 'lg:justify-center' : '')}>
                        <Link to="/settings/profile" className="flex items-center gap-2.5 min-w-0 flex-1 hover:opacity-80"
                            aria-label="Your profile" title="Your profile">
                            <Avatar userId={currentUser?.id} name={displayName}
                                hasAvatar={currentUser?.has_avatar} version={currentUser?.avatar_version} size={32} />
                            {(showLabels || mobileOpen) && (
                                <div className="flex-1 min-w-0">
                                    <p className="text-sm font-medium text-rail-ink truncate leading-tight">{displayName}</p>
                                    <p className="text-xs capitalize text-rail-ink/70 leading-tight mt-0.5">{displayRole}</p>
                                </div>
                            )}
                        </Link>
                        {(showLabels || mobileOpen) && (
                            <>
                                <button type="button" onClick={() => setTheme(THEMES[(THEMES.indexOf(theme) + 1) % THEMES.length])}
                                    className="p-1.5 text-rail-ink/70 hover:text-rail-ink hover:bg-rail-2 rounded-md"
                                    aria-label={`Theme: ${theme}. Switch theme`} title={`Theme: ${theme}`}>
                                    <ThemeIcon size={15} />
                                </button>
                                <Link to="/docs" className="p-1.5 text-rail-ink/70 hover:text-rail-ink hover:bg-rail-2 rounded-md"
                                    aria-label="API docs" title="API docs">
                                    <Code size={15} />
                                </Link>
                                <button onClick={handleLogout}
                                    className="p-1.5 rounded-md text-rail-ink/70 hover:text-[#ff5d6c] hover:bg-rail-2"
                                    aria-label="Sign out" title="Sign out">
                                    <LogOut size={15} />
                                </button>
                            </>
                        )}
                    </div>
                </div>
            </aside>

            {/* Main */}
            <div className="flex-1 min-w-0 flex flex-col h-full overflow-hidden">
                {DEMO && (
                    <div role="note" className="shrink-0 px-4 py-1.5 text-xs text-center bg-accent/15 border-b border-accent/30 text-fg-2">
                        <span className="font-semibold text-accent">Demo</span> · read-only sample data, changes aren't saved ·{' '}
                        <a href={DEMO_REPO_URL} target="_blank" rel="noopener noreferrer" className="underline text-fg hover:text-accent">Get sochub on GitHub →</a>
                    </div>
                )}
                {/* relative z-30: backdrop-blur makes the header its own stacking context, so without a z-index the bell dropdown sat under positioned page content (case panel, sticky tabs). */}
                <header className="relative z-30 h-14 px-4 sm:px-6 flex items-center justify-between gap-3 border-b border-line bg-canvas shrink-0">
                    <div className="flex items-center gap-3 min-w-0">
                        <button onClick={() => setMobileOpen(true)}
                            className="lg:hidden p-2 -ml-2 text-fg-3 hover:text-fg" aria-label="Open menu">
                            <Menu size={18} />
                        </button>
                        <nav aria-label="Breadcrumb" className="min-w-0">
                            <ol className="flex items-center gap-1.5 text-sm truncate">
                                {crumbs.map((c, i) => (
                                    <li key={i} className={cn('flex items-center gap-1.5 capitalize', i === crumbs.length - 1 ? 'text-fg font-medium' : 'text-fg-3')}>
                                        {i > 0 && <span aria-hidden className="text-muted">/</span>}
                                        {i === 0 && crumbs.length > 1 && navItem ? <Link to={navItem.path} className="hover:text-fg hover:underline underline-offset-2">{c}</Link> : <span className="num">{c}</span>}
                                    </li>
                                ))}
                            </ol>
                        </nav>
                    </div>
                    <div className="flex items-center gap-2">
                        <button type="button" onClick={() => window.dispatchEvent(new Event('palette:open'))}
                            className="flex items-center gap-2 h-8 pl-2.5 pr-1.5 text-sm text-fg-3 bg-panel border border-line-strong rounded-md hover:text-fg transition-colors sm:w-48">
                            <Search size={14} aria-hidden />
                            <span className="hidden sm:inline mr-auto">Search…</span>
                            <kbd className="hidden sm:inline text-[10px] border border-line rounded-sm px-1 py-0.5 text-fg-3">⌘K</kbd>
                        </button>
                        <button type="button" onClick={() => window.dispatchEvent(new Event('copilot:toggle'))}
                            className="inline-flex items-center gap-1.5 h-8 px-2.5 rounded-md border border-line-strong bg-panel text-sm text-fg-2 hover:text-fg hover:bg-raised"
                            aria-label="Open copilot" title="Copilot (⌘J)">
                            <Sparkles size={14} className="text-accent" aria-hidden /><span className="hidden md:inline">Copilot</span>
                        </button>
                        <NotificationBell />
                        {currentUser?.role !== 'viewer' && (
                            <Link to="/cases?new=1" className="inline-flex items-center gap-1.5 h-8 px-3 rounded-md bg-accent text-on-accent text-sm font-semibold hover:bg-accent-hover">
                                <Plus size={14} aria-hidden /><span className="hidden sm:inline">New case</span>
                            </Link>
                        )}
                    </div>
                </header>

                {/* relative: absolutely positioned descendants (e.g. sr-only labels) must resolve inside the scroller, not <body>, or the whole document grows and scrolls past the shell. */}
                <main id="main" tabIndex={-1} className="relative flex-1 overflow-y-auto console-grid focus:outline-none">
                    <Suspense fallback={<PageSkeleton />}>
                        <Outlet />
                    </Suspense>
                </main>
            </div>

            <CopilotWidget />
            <CommandPalette nav={ALL_NAV.filter((i) => !('briefs' in i) || briefsOn)} />
        </div>
    );
}
