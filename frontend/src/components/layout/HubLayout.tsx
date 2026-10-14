import { Suspense } from 'react';
import { NavLink, Outlet } from 'react-router-dom';
import PageSkeleton from './PageSkeleton';
import { cn } from '../../lib/utils';

export interface HubTab { label: string; to: string; end?: boolean; show?: boolean }

/** Section with a tab strip on top; each tab is a routed child page (Settings, Observables). */
export default function HubLayout({ title, tabs }: { title: string; tabs: HubTab[] }) {
    return (
        <div>
            <div className="px-4 sm:px-6 pt-5 border-b border-line bg-surface/60">
                <p className="label-mono mb-1">// {title}</p>
                <nav className="flex gap-1 overflow-x-auto -mb-px">
                    {tabs.filter(t => t.show !== false).map(t => (
                        <NavLink key={t.to} to={t.to} end={t.end}
                            className={({ isActive }) => cn(
                                'px-3 py-2 text-sm border-b-2 whitespace-nowrap transition-colors',
                                isActive ? 'border-accent text-fg font-medium' : 'border-transparent text-fg-3 hover:text-fg',
                            )}>
                            {t.label}
                        </NavLink>
                    ))}
                </nav>
            </div>
            <Suspense fallback={<PageSkeleton />}>
                <Outlet />
            </Suspense>
        </div>
    );
}
