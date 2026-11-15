import { formatDateTime } from '../../utils/datetime';
import type { EscalationHistoryRow } from '../../types';
import { useEscalationHistory } from './api';

const VERBS: Record<string, string> = {
    pending: 'Escalated', accepted: 'Accepted', redirected: 'Redirected', returned: 'Returned',
    climbed: 'Climbed (timeout)', superseded: 'Re-escalated', withdrawn: 'Withdrawn', cancelled: 'Cancelled (case closed)',
};
const VIA: Record<string, string> = {
    soc_hub: 'SOC Hub', admin_override: 'admin override', assignment: 'assignment', pagerduty: 'PagerDuty', opsgenie: 'Opsgenie',
};

/** Who moved the row into its status: climbs are the timer's, never the requester's. */
function actorOf(r: EscalationHistoryRow): string {
    if (r.status === 'climbed') return 'System';
    if (r.status === 'pending') return r.requested_by_name ?? 'System';
    if (r.status === 'accepted') return r.accepted_by_name ?? r.closed_by_name ?? 'System';
    return r.closed_by_name ?? 'System';
}

function verbOf(r: EscalationHistoryRow): string {
    const verb = VERBS[r.status] ?? r.status.replace(/_/g, ' ');
    return r.status === 'accepted' && r.accepted_via ? `${verb} via ${VIA[r.accepted_via] ?? r.accepted_via}` : verb;
}

export default function EscalationHistory({ caseId }: { caseId: number }) {
    const { data: rows = [] } = useEscalationHistory(caseId);
    if (rows.length === 0) return null;
    return (
        <section className="bg-panel border border-line rounded-lg mb-6">
            <header className="px-4 py-2.5 border-b border-line">
                <span className="label-mono">// escalation history</span>
            </header>
            <ul className="divide-y divide-line">
                {rows.map((r) => {
                    const actor = actorOf(r);
                    const from = r.from_group_name ?? r.from_tier_name;
                    return (
                        <li key={r.id} className="px-4 py-2.5 text-sm">
                            <div className="flex flex-wrap items-baseline gap-x-2">
                                <span className="text-fg">{actor}</span>
                                <span className="text-fg-3">{verbOf(r)}</span>
                                <span className="text-fg-2">{from ? `${from} → ` : ''}Tier {r.tier_position} · {r.group_name ?? r.tier_name}</span>
                                <span className="text-xs text-muted ml-auto">{formatDateTime(r.closed_at ?? r.accepted_at ?? r.requested_at)}</span>
                            </div>
                            {(r.close_note || r.reason) && <p className="text-xs text-fg-3 mt-1 whitespace-pre-wrap">{r.close_note ?? r.reason}</p>}
                        </li>
                    );
                })}
            </ul>
        </section>
    );
}
