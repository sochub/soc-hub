import { Suspense } from 'react';
import { useQuery } from '@tanstack/react-query';
import { NavLink, Outlet, useLocation, useNavigate } from 'react-router-dom';
import { api } from '../../api/client';
import PageSkeleton from '../../components/layout/PageSkeleton';
import { Select } from '../../components/ui';
import { cn } from '../../lib/utils';
import type { User } from '../../types';

interface NavItem { label: string; to: string; end?: boolean; show: boolean }
interface NavGroup { label: string; items: NavItem[] }

export default function SettingsHub() {
    const { data: me } = useQuery({
        queryKey: ['currentUser'],
        queryFn: async () => (await api.get('/users/me')).data as User,
        staleTime: 300_000,
    });
    const navigate = useNavigate();
    const { pathname } = useLocation();
    const isAdmin = me?.role === 'admin' || !!me?.is_super_admin;
    const groups: NavGroup[] = [
        { label: 'Account', items: [{ label: 'Profile', to: '/settings/profile', show: true }] },
        {
            label: 'Organization', items: [
                { label: 'General', to: '/settings', end: true, show: isAdmin },
                { label: 'Integrations', to: '/settings/integrations', show: true },
                { label: 'Users', to: '/settings/users', show: isAdmin },
                { label: 'Escalation', to: '/settings/escalation', show: isAdmin },
                { label: 'Briefs', to: '/settings/briefs', show: isAdmin },
                { label: 'Audit log', to: '/settings/audit', show: isAdmin },
            ],
        },
        {
            label: 'Reports', items: [
                { label: 'Time', to: '/settings/time', show: isAdmin },
                { label: 'Workload', to: '/settings/workload', show: isAdmin },
            ],
        },
        { label: 'Platform', items: [{ label: 'Organizations', to: '/settings/tenants', show: !!me?.is_super_admin }] },
    ].map((g) => ({ ...g, items: g.items.filter((i) => i.show) })).filter((g) => g.items.length > 0);

    const flat = groups.flatMap((g) => g.items);
    // Longest prefix wins so /settings/tenants/5 selects "Organizations", and /settings selects "General".
    const current = flat
        .filter((i) => (i.end ? pathname === i.to || pathname === `${i.to}/` : pathname.startsWith(i.to)))
        .sort((a, b) => b.to.length - a.to.length)[0]?.to ?? '';

    return (
        <div className="px-4 sm:px-6 pt-5 pb-10 mx-auto w-full max-w-[1400px]">
            <h1 className="text-2xl font-semibold text-fg mb-5">Settings</h1>
            <div className="md:hidden mb-5">
                <label htmlFor="settings-section" className="sr-only">Settings section</label>
                <Select id="settings-section" value={current} onChange={(e) => navigate(e.target.value)}>
                    {current === '' && <option value="">Choose a section</option>}
                    {groups.map((g) => (
                        <optgroup key={g.label} label={g.label}>
                            {g.items.map((i) => <option key={i.to} value={i.to}>{i.label}</option>)}
                        </optgroup>
                    ))}
                </Select>
            </div>
            <div className="flex gap-8">
                <nav aria-label="Settings sections" className="hidden md:block w-48 shrink-0 sticky top-4 self-start space-y-5">
                    {groups.map((g) => (
                        <div key={g.label}>
                            <p className="label-caps text-fg-3 px-2 mb-1.5">{g.label}</p>
                            <ul className="space-y-0.5">
                                {g.items.map((i) => (
                                    <li key={i.to}>
                                        <NavLink to={i.to} end={i.end} className={({ isActive }) => cn(
                                            'block rounded-md px-2 py-1.5 text-sm transition-colors',
                                            isActive ? 'bg-raised text-fg font-medium' : 'text-fg-2 hover:bg-raised hover:text-fg',
                                        )}>
                                            {i.label}
                                        </NavLink>
                                    </li>
                                ))}
                            </ul>
                        </div>
                    ))}
                </nav>
                <div className="min-w-0 flex-1 max-w-4xl">
                    <Suspense fallback={<PageSkeleton />}>
                        <Outlet />
                    </Suspense>
                </div>
            </div>
        </div>
    );
}
