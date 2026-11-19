import { cn } from '../../lib/utils';
import { TONE_TEXT, severityTone, statusTone, type Tone } from './tones';
import { statusLabel } from '../../features/cases/statuses';

type Variant = 'default' | 'brand' | 'critical' | 'high' | 'medium' | 'low' | 'info';

const variants: Record<Variant, string> = {
    default: 'bg-raised text-fg-2 border-line',
    brand: 'bg-accent/10 text-accent border-accent/30',
    critical: 'bg-severity-critical/10 text-severity-critical border-severity-critical/30',
    high: 'bg-severity-high/10 text-severity-high border-severity-high/30',
    medium: 'bg-severity-medium/10 text-severity-medium border-severity-medium/30',
    low: 'bg-severity-low/10 text-severity-low border-severity-low/30',
    info: 'bg-severity-info/10 text-severity-info border-severity-info/30',
};

export interface BadgeProps extends React.HTMLAttributes<HTMLSpanElement> {
    variant?: Variant;
}

/** Neutral tag/label chip (tags, counts, metadata). For severity and status use <Stamp>. */
export default function Badge({ className, variant = 'default', ...props }: BadgeProps) {
    return <span className={cn('inline-flex items-center gap-1 rounded-sm border px-1.5 py-0.5 text-xs font-medium', variants[variant], className)} {...props} />;
}

export function severityVariant(severity?: string): Variant {
    const t = severityTone(severity);
    return t === 'critical' || t === 'high' || t === 'medium' || t === 'low' ? t : 'info';
}

/** Severity, status, verdict and TLP as a tinted mono tag in its tone colour. */
export function Stamp({ tone = 'neutral', className, ...props }: React.HTMLAttributes<HTMLSpanElement> & { tone?: Tone }) {
    return <span className={cn('stamp', TONE_TEXT[tone], className)} {...props} />;
}

export function SeverityStamp({ severity, className }: { severity?: string | null; className?: string }) {
    return <Stamp tone={severityTone(severity)} className={className}>{severity || 'info'}</Stamp>;
}

export function StatusStamp({ status, className }: { status?: string | null; className?: string }) {
    return <Stamp tone={statusTone(status)} className={className}>{statusLabel(status)}</Stamp>;
}
