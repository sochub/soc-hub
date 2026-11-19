import { Suspense } from 'react';
import { Outlet } from 'react-router-dom';
import PageSkeleton from './PageSkeleton';
import { FolderTabLinks } from '../ui/Tabs';

export interface HubTab { label: string; to: string; end?: boolean; show?: boolean }

/** A section whose sub-pages are folder tabs (Alerts, Observables). The hub owns the page title; tabs sit on the sheet below. */
export default function HubLayout({ title, tabs }: { title: string; tabs: HubTab[] }) {
    return (
        <div className="px-4 sm:px-6 pt-5 mx-auto w-full max-w-[1600px]">
            <h1 className="text-2xl font-semibold text-fg capitalize mb-3">{title}</h1>
            <FolderTabLinks label={`${title} sections`} items={tabs.filter((t) => t.show !== false)} />
            <div className="bg-panel border border-t-0 border-line rounded-b-md mb-6">
                <Suspense fallback={<PageSkeleton />}>
                    <Outlet />
                </Suspense>
            </div>
        </div>
    );
}
