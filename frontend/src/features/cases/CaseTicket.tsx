import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { RefreshCw, Ticket } from 'lucide-react';
import { api } from '../../api/client';
import { cn } from '../../lib/utils';

type TicketInfo = {
    configured: boolean;
    ticket: { label: string; external_id: string; url: string | null; remote_status: string | null; remote_closed: boolean; syncing: boolean } | null;
};
const apiError = (err: unknown, fallback: string) => {
    const d = (err as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
    return typeof d === 'string' ? d : fallback;
};

/** Header pill: the case's linked ticket (status, sync now), or "Create ticket" when ticketing is set up. */
export default function CaseTicket({ caseId, canEdit }: { caseId: number; canEdit: boolean }) {
    const qc = useQueryClient();
    const key = ['case-ticket', caseId];
    const { data } = useQuery({ queryKey: key, queryFn: async () => (await api.get(`/cases/${caseId}/ticket`)).data as TicketInfo });
    const done = () => { qc.invalidateQueries({ queryKey: key }); qc.invalidateQueries({ queryKey: ['case', String(caseId)] }); };
    const create = useMutation({ mutationFn: async () => api.post(`/cases/${caseId}/ticket`), onSuccess: done });
    const sync = useMutation({ mutationFn: async () => api.post(`/cases/${caseId}/ticket/sync`), onSuccess: done });
    const err = create.error ?? sync.error;
    if (!data || (!data.ticket && !(data.configured && canEdit))) return null;
    const t = data.ticket;
    const pill = 'inline-flex items-center gap-1 whitespace-nowrap rounded-sm border border-line-strong px-2.5 py-0.5 text-xs text-fg-2';
    return (
        <span className="inline-flex items-center gap-1.5">
            {t ? (
                <span className={pill} title={t.remote_closed ? 'Closed in the ticketing system' : undefined}>
                    <Ticket size={12} />
                    {t.url ? <a href={t.url} target="_blank" rel="noopener noreferrer" className="hover:underline">{t.label} {t.external_id}</a>
                        : <span>{t.label} {t.external_id}</span>}
                    {t.remote_status && <span className={cn('text-muted', t.remote_closed && 'line-through')}>· {t.remote_status}</span>}
                    {t.syncing && canEdit && (
                        <button onClick={() => sync.mutate()} disabled={sync.isPending} aria-label="Sync ticket now" title="Sync now"
                            className="ml-0.5 text-muted hover:text-fg disabled:opacity-50"><RefreshCw size={11} className={sync.isPending ? 'animate-spin' : ''} /></button>
                    )}
                </span>
            ) : (
                <button onClick={() => create.mutate()} disabled={create.isPending} className={cn(pill, 'hover:bg-raised disabled:opacity-50')}>
                    <Ticket size={12} /> {create.isPending ? 'Creating…' : 'Create ticket'}
                </button>
            )}
            {err && <span role="alert" className="text-xs text-severity-critical">{apiError(err, 'Ticket action failed')}</span>}
        </span>
    );
}
