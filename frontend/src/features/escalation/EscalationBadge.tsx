import type { EscalationSummary } from '../../types';
import { sinceShort } from './api';

export default function EscalationBadge({ e }: { e: EscalationSummary }) {
    const pending = e.status === 'pending';
    const text = pending
        ? `Tier ${e.tier_position} · ${e.group_name ?? e.tier_name} · pending ${sinceShort(e.requested_at)}`
        : `Tier ${e.tier_position} · accepted${e.accepted_by_name ? ` by ${e.accepted_by_name}` : ''}`;
    return (
        <span className={pending
            ? 'inline-flex items-center h-8 px-2.5 rounded-sm text-xs font-medium border whitespace-nowrap bg-amber-500/10 text-amber-300 border-amber-500/30'
            : 'inline-flex items-center h-8 px-2.5 rounded-sm text-xs font-medium border whitespace-nowrap border-line text-fg-2'}>
            {text}
        </span>
    );
}
