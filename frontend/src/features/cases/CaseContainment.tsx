import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ShieldCheck } from 'lucide-react';
import { api } from '../../api/client';
import { Stamp, Button, useConfirm, type Tone } from '../../components/ui';

type Action = {
    id: number; action: string; target_kind: string; target: string; status: 'active' | 'released' | 'blocked';
    reason: string | null; release_hint: string | null; created_at: string; released_at: string | null;
};
const STATUS: Record<Action['status'], Tone> = { active: 'critical', released: 'ok', blocked: 'neutral' };

/** Automated containment on this case (from containment_guard steps): what is contained, refused, released. */
export default function CaseContainment({ caseId, canEdit }: { caseId: number; canEdit: boolean }) {
    const qc = useQueryClient();
    const { confirm, dialog } = useConfirm();
    const key = ['containment', caseId];
    const { data: actions = [] } = useQuery({ queryKey: key, queryFn: async () => (await api.get(`/containment/actions?case_id=${caseId}`)).data as Action[] });
    const release = useMutation({
        mutationFn: async (id: number) => api.post(`/containment/actions/${id}/release`),
        onSuccess: () => { qc.invalidateQueries({ queryKey: key }); qc.invalidateQueries({ queryKey: ['case', String(caseId)] }); },
    });
    if (!actions.length) return null;
    return (
        <div className="space-y-2">
            {dialog}
            <h2 className="text-sm font-semibold text-fg flex items-center gap-1.5"><ShieldCheck size={14} aria-hidden /> Containment
                <span className="num text-xs font-normal text-fg-3">{actions.filter((a) => a.status === 'active').length} active</span></h2>
            {actions.map((a) => (
                <article key={a.id} className="rounded-md border border-line bg-panel p-3 text-sm">
                    <div className="flex flex-wrap items-center gap-2">
                        <Stamp tone={STATUS[a.status]}>{a.status}</Stamp>
                        <span className="text-fg">{a.action}</span>
                        <span className="font-mono text-xs text-fg-2">{a.target_kind} {a.target}</span>
                        <span className="text-xs num text-fg-3">{new Date(a.released_at ?? a.created_at).toLocaleString()}</span>
                        {canEdit && a.status === 'active' && (
                            <Button size="sm" variant="secondary" className="ml-auto" onClick={async () => {
                                if (await confirm({ title: 'Mark this containment as released?', body: 'Undo it at the vendor first; this only records the release.', confirmLabel: 'Mark released' })) release.mutate(a.id);
                            }}>Mark released</Button>
                        )}
                    </div>
                    {a.reason && <p className="mt-1 text-xs text-fg-3">Refused: {a.reason}</p>}
                    {a.status === 'active' && a.release_hint && <p className="mt-1 text-xs text-fg-3">To undo: {a.release_hint}</p>}
                </article>
            ))}
        </div>
    );
}
