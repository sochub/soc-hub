import { cn } from '../../lib/utils';

export function Card({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
    return (
        <div
            className={cn(
                'rounded-2xl border border-line bg-panel/40 backdrop-blur-xs shadow-lg',
                className,
            )}
            {...props}
        />
    );
}

export function CardHeader({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
    return <div className={cn('p-6 pb-3', className)} {...props} />;
}

export function CardTitle({ className, ...props }: React.HTMLAttributes<HTMLHeadingElement>) {
    return (
        <h3
            className={cn('text-base font-semibold text-fg tracking-tight', className)}
            {...props}
        />
    );
}

export function CardContent({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
    return <div className={cn('p-6 pt-3', className)} {...props} />;
}
