import { cn } from '../../lib/utils';

type Variant = 'default' | 'brand' | 'critical' | 'high' | 'medium' | 'low' | 'info';

const variants: Record<Variant, string> = {
    default: 'bg-slate-800 text-slate-300 border-slate-700',
    brand: 'bg-brand-500/15 text-brand-300 border-brand-500/30',
    critical: 'bg-severity-critical/15 text-severity-critical border-severity-critical/30',
    high: 'bg-severity-high/15 text-severity-high border-severity-high/30',
    medium: 'bg-severity-medium/15 text-severity-medium border-severity-medium/30',
    low: 'bg-severity-low/15 text-severity-low border-severity-low/30',
    info: 'bg-severity-info/15 text-severity-info border-severity-info/30',
};

export interface BadgeProps extends React.HTMLAttributes<HTMLSpanElement> {
    variant?: Variant;
}

export default function Badge({ className, variant = 'default', ...props }: BadgeProps) {
    return (
        <span
            className={cn(
                'inline-flex items-center gap-1 rounded-full border px-2.5 py-0.5 text-xs font-medium',
                variants[variant],
                className,
            )}
            {...props}
        />
    );
}

/** Maps a case/alert severity string to the matching Badge variant. */
export function severityVariant(severity?: string): Variant {
    switch (severity?.toLowerCase()) {
        case 'critical':
            return 'critical';
        case 'high':
            return 'high';
        case 'medium':
            return 'medium';
        case 'low':
            return 'low';
        default:
            return 'info';
    }
}
