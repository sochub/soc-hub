import { Stamp, type Tone } from '../../components/ui';
import type { SLAStatus } from '../../types';

const SLA_LABEL: Record<SLAStatus, string> = {
    not_tracked: 'Not tracked',
    met: 'Met',
    on_track: 'On track',
    at_risk: 'At risk',
    breached: 'Breached',
};

const SLA_TONE: Record<SLAStatus, Tone> = {
    not_tracked: 'neutral',
    met: 'ok',
    on_track: 'ok',
    at_risk: 'medium',
    breached: 'critical',
};

export function SLABadge({ status }: { status?: SLAStatus | null }) {
    if (!status || status === 'not_tracked') {
        return <span className="text-xs text-muted">—</span>;
    }
    return <Stamp tone={SLA_TONE[status]}>{SLA_LABEL[status]}</Stamp>;
}

/** "2h 15m left" / "3h 10m overdue" / "47d 0h overdue" style text, ticking against a due_at ISO timestamp. */
export function formatSlaCountdown(dueAt: string | null | undefined, status?: SLAStatus | null): string | null {
    if (!dueAt || status === 'met' || status === 'not_tracked') return null;
    const diffMs = new Date(dueAt).getTime() - Date.now();
    const overdue = diffMs < 0;
    const totalMinutes = Math.round(Math.abs(diffMs) / 60000);
    const hours = Math.floor(totalMinutes / 60);
    const minutes = totalMinutes % 60;
    const text = hours >= 48 ? `${Math.floor(hours / 24)}d ${hours % 24}h` : hours > 0 ? `${hours}h ${minutes}m` : `${minutes}m`;
    return overdue ? `${text} overdue` : `${text} left`;
}
