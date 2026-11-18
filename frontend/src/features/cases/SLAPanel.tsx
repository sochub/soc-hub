import { useEffect, useState } from 'react';
import { Timer } from 'lucide-react';
import type { Case } from '../../types';
import { SLABadge, formatSlaCountdown } from './SLABadge';

function Clock({ label, status, dueAt }: {
    label: string; status?: Case['sla_response_status']; dueAt?: string | null;
}) {
    const [, setTick] = useState(0);
    useEffect(() => {
        const id = setInterval(() => setTick((t) => t + 1), 30000);
        return () => clearInterval(id);
    }, []);

    if (!status || status === 'not_tracked') return null;
    const countdown = formatSlaCountdown(dueAt, status);

    return (
        <span className="flex items-center gap-1.5 whitespace-nowrap">
            <span>{label}</span>
            <SLABadge status={status} />
            {countdown && <span className="num">{countdown}</span>}
        </span>
    );
}

export default function SLAPanel({ caseData }: { caseData: Case }) {
    if (
        (!caseData.sla_response_status || caseData.sla_response_status === 'not_tracked')
        && (!caseData.sla_resolution_status || caseData.sla_resolution_status === 'not_tracked')
    ) {
        return null;
    }

    return (
        // Inline in the case meta line; the badges carry the alarm, the line stays one row.
        <span className="flex flex-wrap items-center gap-x-3 gap-y-1">
            <Timer size={12} aria-hidden />
            <Clock label="SLA response" status={caseData.sla_response_status} dueAt={caseData.sla_response_due_at} />
            <Clock label="resolution" status={caseData.sla_resolution_status} dueAt={caseData.sla_resolution_due_at} />
        </span>
    );
}
