import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ShieldCheck } from 'lucide-react';
import { api } from '../../api/client';
import { cn } from '../../lib/utils';

type Action = {
    id: number; action: string; target_kind: string; target: string; status: 'active' | 'released' | 'blocked';
    reason: string | null; release_hint: string | null; created_at: string; released_at: string | null;
};
const STATUS: Record<Action['status'], string> = {
    active: 'bg-red-500/15 text-red-400 border-red-500/30', released: 'bg-emerald-500/15 text-emerald-400 border-emerald-500/30',
    blocked: 'bg-raised text-muted border-line',
};

/** Automated containment on this case (from containment_guard steps): what is contained, refused, released. */
export default function CaseContainment({ caseId, canEdit }: { caseId: number; canEdit: boolean }) {
    const qc = useQueryClient();
    const key = ['containment', caseId];
    const { data: actions = [] } = useQuery({ queryKey: key, queryFn: async () => (await api.get(`/containment/actions?case_id=${caseId}`)).data as Action[] });
    const release = useMutation({
        mutationFn: async (id: number) => api.post(`/containment/actions/${id}/release`),
        onSuccess: () => { qc.invalidateQueries({ queryKey: key }); qc.invalidateQueries({ queryKey: ['case', String(caseId)] }); },
    });
    if (!actions.length) return null;
    return (
        <div className="space-y-2">
            <h3 className="text-sm font-semibold text-fg-2 flex items-center gap-1.5"><ShieldCheck size={14} /> Containment
                <span className="font-mono text-xs text-muted">{actions.filter((a) => a.status === 'active').length} active</span></h3>
            {actions.map((a) => (
                <article key={a.id} className="rounded-lg border border-line bg-panel p-3 text-sm">
                    <div className="flex flex-wrap items-center gap-2">
                        <span className={cn('text-[10px] font-medium px-1.5 py-0.5 rounded-sm border', STATUS[a.status])}>{a.status}</span>
                        <span className="text-fg">{a.action}</span>
                        <span className="font-mono text-xs text-fg-2">{a.target_kind} {a.target}</span>
                        <span className="text-xs text-muted">{new Date(a.released_at ?? a.created_at).toLocaleString()}</span>
                        {canEdit && a.status === 'active' && (
                            <button onClick={() => { if (confirm('Mark this containment as released? Undo it at the vendor first.')) release.mutate(a.id); }}
                                className="ml-auto text-xs bg-raised hover:bg-raised-2 text-fg-2 px-2.5 py-1 rounded-sm">Mark released</button>
                        )}
                    </div>
                    {a.reason && <p className="mt-1 text-xs text-fg-3">Refused: {a.reason}</p>}
                    {a.status === 'active' && a.release_hint && <p className="mt-1 text-xs text-fg-3">To undo: {a.release_hint}</p>}
                </article>
            ))}
        </div>
    );
}
