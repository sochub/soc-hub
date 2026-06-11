import { useEffect, useState } from 'react';
import { Outlet, Link, useLocation, useNavigate } from 'react-router-dom';
import {
    LayoutDashboard, ShieldAlert, Layers, Settings, Bell, Database, Share2, LogOut,
    Users, Building2, AlertOctagon, Menu, X, PanelLeftClose, PanelLeftOpen, BookText,
} from 'lucide-react';
import { cn } from '../../lib/utils';
import { useQuery } from '@tanstack/react-query';
import { api } from '../../api/client';
import type { User } from '../../types';
import CopilotWidget from '../../features/copilot/CopilotWidget';
import TenantSwitcher from '../../features/tenants/TenantSwitcher';

const baseSidebarItems = [
    { icon: LayoutDashboard, label: 'Dashboard', path: '/' },
    { icon: ShieldAlert, label: 'Cases', path: '/cases' },
    { icon: Database, label: 'Artifacts', path: '/artifacts' },
    { icon: AlertOctagon, label: 'IOCs', path: '/iocs' },
    { icon: BookText, label: 'Playbooks', path: '/playbooks' },
    { icon: Share2, label: 'Mind Map', path: '/mindmap' },
    { icon: Layers, label: 'Integrations', path: '/integrations' },
    { icon: Settings, label: 'Settings', path: '/settings' },
];
const adminItems = [{ icon: Users, label: 'Users', path: '/admin/users' }];
const superAdminItems = [{ icon: Building2, label: 'Tenants', path: '/superadmin/tenants' }];

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
        localStorage.removeItem('token');
        navigate('/login', { replace: true });
    };

    const userInitials = currentUser?.full_name
        ? currentUser.full_name.split(' ').map(n => n[0]).join('').toUpperCase().slice(0, 2)
        : currentUser?.email?.[0]?.toUpperCase() ?? '?';
    const displayName = currentUser?.full_name || currentUser?.email || 'User';
    const displayRole = currentUser?.role ? currentUser.role.replace('_', ' ') : '';

    const sidebarItems = [...baseSidebarItems];
    if (currentUser?.role === 'admin' || currentUser?.is_super_admin) sidebarItems.push(...adminItems);
    if (currentUser?.is_super_admin) sidebarItems.push(...superAdminItems);

    const section = location.pathname === '/'
        ? 'dashboard'
        : location.pathname.split('/')[1];

    const showLabels = !collapsed; // desktop labels; mobile drawer always expanded

    return (
        <div className="flex h-screen bg-zinc-50 text-zinc-900 font-sans antialiased overflow-hidden">
            {/* Mobile overlay */}
            {mobileOpen && (
                <div className="fixed inset-0 z-30 bg-zinc-900/30 backdrop-blur-[2px] lg:hidden"
                    onClick={() => setMobileOpen(false)} aria-hidden="true" />
            )}

            {/* Sidebar */}
            <aside
                className={cn(
                    'fixed lg:relative z-40 h-full flex flex-col bg-white border-r border-zinc-200',
                    'transition-[width,transform] duration-200 ease-out lg:translate-x-0',
                    collapsed ? 'lg:w-16' : 'lg:w-60',
                    'w-60',
                    mobileOpen ? 'translate-x-0' : '-translate-x-full',
                )}
                aria-label="Primary navigation"
            >
                {/* Brand + collapse */}
                <div className={cn('h-14 flex items-center border-b border-zinc-200 shrink-0',
                    collapsed ? 'lg:justify-center px-3' : 'px-4 justify-between')}>
                    <Link to="/" className="flex items-center gap-2.5 min-w-0">
                        <span className="w-7 h-7 flex items-center justify-center bg-accent-600 text-white shrink-0">
                            <ShieldAlert size={16} />
                        </span>
                        {(showLabels || mobileOpen) && (
                            <span className="font-mono font-semibold text-[15px] tracking-tight text-zinc-900 truncate">
                                soc<span className="text-accent-600">hub</span>
                            </span>
                        )}
                    </Link>
                    <button onClick={() => setMobileOpen(false)}
                        className="lg:hidden p-1.5 text-zinc-400 hover:text-zinc-900" aria-label="Close menu">
                        <X size={18} />
                    </button>
                    {!collapsed && (
                        <button onClick={() => setCollapsed(true)}
                            className="hidden lg:inline-flex p-1.5 text-zinc-400 hover:text-zinc-900 hover:bg-zinc-100"
                            aria-label="Collapse sidebar" title="Collapse sidebar">
                            <PanelLeftClose size={16} />
                        </button>
                    )}
                </div>

                {/* Tenant switcher (hidden when collapsed on desktop) */}
                {(showLabels || mobileOpen) && (
                    <div className="px-3 py-3 border-b border-zinc-200">
                        <TenantSwitcher />
                    </div>
                )}

                {/* Nav */}
                <nav className="flex-1 overflow-y-auto py-2">
                    {(showLabels || mobileOpen) && (
                        <p className="label-mono px-4 pt-2 pb-1.5">menu</p>
                    )}
                    {sidebarItems.map((item) => {
                        const Icon = item.icon;
                        const isActive = location.pathname === item.path
                            || (item.path !== '/' && location.pathname.startsWith(item.path));
                        return (
                            <Link key={item.path} to={item.path}
                                aria-current={isActive ? 'page' : undefined}
                                title={collapsed ? item.label : undefined}
                                className={cn(
                                    'relative flex items-center gap-3 h-9 mx-2 px-3 text-sm transition-colors',
                                    collapsed ? 'lg:justify-center lg:px-0 lg:mx-2' : '',
                                    isActive
                                        ? 'bg-accent-50 text-accent-700 font-medium'
                                        : 'text-zinc-600 hover:bg-zinc-100 hover:text-zinc-900',
                                )}>
                                {isActive && <span className="absolute left-0 top-1 bottom-1 w-0.5 bg-accent-600" />}
                                <Icon size={17} className="shrink-0" />
                                {(showLabels || mobileOpen) && <span className="truncate">{item.label}</span>}
                            </Link>
                        );
                    })}
                </nav>

                {/* Expand button when collapsed */}
                {collapsed && (
                    <button onClick={() => setCollapsed(false)}
                        className="hidden lg:flex items-center justify-center h-10 border-t border-zinc-200 text-zinc-400 hover:text-zinc-900 hover:bg-zinc-100"
                        aria-label="Expand sidebar" title="Expand sidebar">
                        <PanelLeftOpen size={16} />
                    </button>
                )}

                {/* User */}
                <div className={cn('border-t border-zinc-200 shrink-0',
                    collapsed ? 'lg:px-2 px-3 py-3' : 'px-3 py-3')}>
                    <div className={cn('flex items-center gap-2.5', collapsed ? 'lg:justify-center' : '')}>
                        <span className="w-8 h-8 bg-zinc-900 text-white flex items-center justify-center text-xs font-mono font-semibold shrink-0">
                            {userInitials}
                        </span>
                        {(showLabels || mobileOpen) && (
                            <>
                                <div className="flex-1 min-w-0">
                                    <p className="text-sm font-medium text-zinc-900 truncate leading-tight">{displayName}</p>
                                    <p className="label-mono leading-tight mt-0.5">{displayRole}</p>
                                </div>
                                <button onClick={handleLogout}
                                    className="p-1.5 text-zinc-400 hover:text-severity-critical hover:bg-zinc-100"
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
                <header className="h-14 px-4 sm:px-6 flex items-center justify-between border-b border-zinc-200 bg-white shrink-0">
                    <div className="flex items-center gap-3 min-w-0">
                        <button onClick={() => setMobileOpen(true)}
                            className="lg:hidden p-2 -ml-2 text-zinc-500 hover:text-zinc-900" aria-label="Open menu">
                            <Menu size={18} />
                        </button>
                        <nav aria-label="Breadcrumb" className="flex items-center gap-2 font-mono text-xs">
                            <span className="text-zinc-400">~/</span>
                            <span className="text-zinc-900 font-medium">{section}</span>
                        </nav>
                    </div>
                    <button className="relative p-2 text-zinc-500 hover:text-zinc-900" aria-label="Notifications">
                        <Bell size={17} />
                        <span className="absolute top-1.5 right-1.5 w-1.5 h-1.5 bg-accent-600" />
                    </button>
                </header>

                <main className="flex-1 overflow-y-auto console-grid">
                    <Outlet />
                </main>
            </div>

            <CopilotWidget />
        </div>
    );
}
