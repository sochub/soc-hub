import { cn } from '../../lib/utils';

/** Title for a page inside the Settings hub. The hub owns the h1, so pages get an h2-level header. */
export default function SettingsPageHeader({ title, description, actions, className }: {
    title: React.ReactNode; description?: React.ReactNode; actions?: React.ReactNode; className?: string;
}) {
    return (
        <header className={cn('flex flex-wrap items-end justify-between gap-x-6 gap-y-3', className)}>
            <div className="min-w-0">
                <h2 className="text-lg font-semibold text-fg text-balance">{title}</h2>
                {description && <p className="mt-1 max-w-[70ch] text-sm text-fg-3">{description}</p>}
            </div>
            {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
        </header>
    );
}
