import { AlertTriangle } from 'lucide-react';
import { cn } from '../../lib/utils';
import Button from './Button';

/** Loading placeholder shaped like the content (rows by default). */
export function Skeleton({ rows = 5, className }: { rows?: number; className?: string }) {
    return (
        <div role="status" aria-label="Loading" className={cn('space-y-2', className)}>
            {Array.from({ length: rows }, (_, i) => (
                <div key={i} className="h-9 rounded-sm bg-raised animate-pulse" style={{ opacity: 1 - i * (0.6 / rows) }} />
            ))}
        </div>
    );
}

export function EmptyState({ title, body, action, className }: { title: string; body?: React.ReactNode; action?: React.ReactNode; className?: string }) {
    return (
        <div className={cn('py-12 px-4 text-center', className)}>
            <p className="text-sm font-medium text-fg">{title}</p>
            {body && <p className="mt-1 text-sm text-fg-3 max-w-[60ch] mx-auto">{body}</p>}
            {action && <div className="mt-4 flex justify-center gap-2">{action}</div>}
        </div>
    );
}

export function ErrorState({ message = 'Could not load this.', onRetry, className }: { message?: string; onRetry?: () => void; className?: string }) {
    return (
        <div role="alert" className={cn('flex flex-wrap items-center gap-3 rounded-md border border-severity-critical/30 bg-severity-critical/5 px-4 py-3 text-sm text-severity-critical', className)}>
            <AlertTriangle size={15} className="shrink-0" aria-hidden />
            <span className="mr-auto">{message}</span>
            {onRetry && <Button size="sm" variant="secondary" onClick={onRetry}>Try again</Button>}
        </div>
    );
}
