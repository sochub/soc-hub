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
        <div className="flex items-center gap-2">
            <span className="label-mono text-zinc-400">{label}</span>
            <SLABadge status={status} />
            {countdown && <span className="num text-xs text-zinc-500">{countdown}</span>}
        </div>
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
        <div className="border border-zinc-200 bg-white mb-6 px-4 h-11 flex items-center gap-6">
            <span className="label-mono flex items-center gap-2 text-zinc-500 shrink-0">
                <Timer size={13} className="text-accent-600" /> SLA
            </span>
            <Clock label="response" status={caseData.sla_response_status} dueAt={caseData.sla_response_due_at} />
            <Clock label="resolution" status={caseData.sla_resolution_status} dueAt={caseData.sla_resolution_due_at} />
        </div>
    );
}
