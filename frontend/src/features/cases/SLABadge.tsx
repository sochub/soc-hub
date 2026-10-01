import { cn } from '../../lib/utils';
import type { SLAStatus } from '../../types';

const SLA_LABEL: Record<SLAStatus, string> = {
    not_tracked: 'Not tracked',
    met: 'Met',
    on_track: 'On track',
    at_risk: 'At risk',
    breached: 'Breached',
};

const SLA_STYLE: Record<SLAStatus, string> = {
    not_tracked: 'text-zinc-400 bg-zinc-50 border-zinc-200',
    met: 'text-emerald-700 bg-emerald-50 border-emerald-200',
    on_track: 'text-emerald-700 bg-emerald-50 border-emerald-200',
    at_risk: 'text-amber-700 bg-amber-50 border-amber-200',
    breached: 'text-red-700 bg-red-50 border-red-200',
};

export function SLABadge({ status }: { status?: SLAStatus | null }) {
    if (!status || status === 'not_tracked') {
        return <span className="text-[10px] text-zinc-300">—</span>;
    }
    return (
        <span className={cn(
            'inline-flex items-center px-2 py-0.5 rounded text-[10px] font-bold uppercase tracking-wider border',
            SLA_STYLE[status],
        )}>
            {SLA_LABEL[status]}
        </span>
    );
}

/** "2h 15m left" / "3h 10m overdue" style text, ticking against a due_at ISO timestamp. */
export function formatSlaCountdown(dueAt: string | null | undefined, status?: SLAStatus | null): string | null {
    if (!dueAt || status === 'met' || status === 'not_tracked') return null;
    const diffMs = new Date(dueAt).getTime() - Date.now();
    const overdue = diffMs < 0;
    const totalMinutes = Math.round(Math.abs(diffMs) / 60000);
    const hours = Math.floor(totalMinutes / 60);
    const minutes = totalMinutes % 60;
    const text = hours > 0 ? `${hours}h ${minutes}m` : `${minutes}m`;
    return overdue ? `${text} overdue` : `${text} left`;
}
