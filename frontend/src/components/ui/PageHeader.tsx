import { cn } from '../../lib/utils';

/** One page header for every view: title (+ optional count and one-line purpose), actions on the right. */
export default function PageHeader({ title, count, description, actions, className }: {
    title: React.ReactNode; count?: number; description?: React.ReactNode; actions?: React.ReactNode; className?: string;
}) {
    return (
        <header className={cn('mb-5 flex flex-wrap items-end justify-between gap-x-6 gap-y-3', className)}>
            <div className="min-w-0">
                <h1 className="text-2xl font-semibold text-fg text-balance">
                    {title}
                    {count != null && <span className="num ml-2 text-base font-normal text-fg-3">{count}</span>}
                </h1>
                {description && <p className="mt-1 max-w-[70ch] text-sm text-fg-3">{description}</p>}
            </div>
            {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
        </header>
    );
}
