import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { CheckCircle2, AlertTriangle } from 'lucide-react';
import { api } from '../../api/client';
import Modal, { modalInput, modalLabel, btnPrimary, btnSecondary } from '../../components/layout/Modal';
import type { CaseReportView, CaseTask } from '../../types';
import { CASE_OUTCOMES } from './outcomes';

/** Closing is its own step: outcome, closing note, and a look at what is still open. */
export default function CloseCaseModal({ caseId, open, onClose, onClosed }: {
    caseId: number; open: boolean; onClose: () => void; onClosed: () => void;
}) {
    const qc = useQueryClient();
    const [outcome, setOutcome] = useState('');
    const [note, setNote] = useState('');

    const { data: tasks = [] } = useQuery({
        queryKey: ['case-tasks', caseId], enabled: open,
        queryFn: async () => (await api.get(`/cases/${caseId}/tasks`)).data as CaseTask[],
    });
    const { data: containment = [] } = useQuery({
        queryKey: ['containment', caseId], enabled: open,
        queryFn: async () => (await api.get(`/containment/actions?case_id=${caseId}`)).data as { status: string }[],
    });
    const { data: report } = useQuery({
        queryKey: ['case-report', caseId], enabled: open,
        queryFn: async () => (await api.get(`/cases/${caseId}/report`)).data as CaseReportView,
    });

    const close = useMutation({
        mutationFn: async () => api.put(`/cases/${caseId}`, { status: 'closed', outcome, closing_note: note.trim() }),
        onSuccess: () => {
            qc.invalidateQueries({ queryKey: ['case', String(caseId)] });
            qc.invalidateQueries({ queryKey: ['cases'] });
            qc.invalidateQueries({ queryKey: ['audit-logs', 'case', String(caseId)] });
            onClosed();
        },
    });
    const detail = (close.error as { response?: { data?: { detail?: unknown } } } | null)?.response?.data?.detail;

    const openTasks = tasks.filter((t) => t.status !== 'done').length;
    const activeContainment = containment.filter((a) => a.status === 'active').length;
    const reportWritten = !!report?.report.executive_summary?.trim();
    const checks: [boolean, string][] = [
        [openTasks === 0, openTasks === 0 ? 'All tasks done' : `${openTasks} task${openTasks > 1 ? 's' : ''} still open`],
        [activeContainment === 0, activeContainment === 0 ? 'No active containment' : `${activeContainment} containment action${activeContainment > 1 ? 's' : ''} still active`],
        [reportWritten, reportWritten ? `Report written (TLP:${report?.report.tlp.toUpperCase()})` : 'Report has no executive summary yet'],
    ];

    return (
        <Modal open={open} onClose={onClose} title={`Close case #${caseId}`} size="md"
            footer={<>
                <button onClick={onClose} className={btnSecondary}>Cancel</button>
                <button onClick={() => close.mutate()} disabled={!outcome || !note.trim() || close.isPending} className={btnPrimary}>
                    {close.isPending ? 'Closing…' : 'Close case'}
                </button>
            </>}>
            <div className="space-y-4">
                <ul className="space-y-1.5 text-sm" aria-label="Before closing">
                    {checks.map(([ok, text]) => (
                        <li key={text} className={ok ? 'flex items-center gap-2 text-fg-2' : 'flex items-center gap-2 text-severity-medium'}>
                            {ok ? <CheckCircle2 size={15} className="text-emerald-700 dark:text-emerald-300 shrink-0" aria-hidden /> : <AlertTriangle size={15} className="shrink-0" aria-hidden />}
                            {text}
                        </li>
                    ))}
                </ul>
                <p className="text-xs text-fg-3">Open items don't block closing; they stay on the closed case.</p>
                <div>
                    <label htmlFor="close-outcome" className={modalLabel}>Outcome</label>
                    <select id="close-outcome" value={outcome} onChange={(e) => setOutcome(e.target.value)} className={modalInput}>
                        <option value="">Pick an outcome</option>
                        {CASE_OUTCOMES.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
                    </select>
                </div>
                <div>
                    <label htmlFor="close-note" className={modalLabel}>Closing note</label>
                    <textarea id="close-note" value={note} onChange={(e) => setNote(e.target.value)} maxLength={8000}
                        className={`${modalInput} min-h-[96px]`} placeholder="What happened and how it was resolved" />
                </div>
                {close.isError && <p role="alert" className="text-sm text-severity-critical">
                    {typeof detail === 'string' ? detail : 'Could not close the case. Check your connection and try again.'}
                </p>}
            </div>
        </Modal>
    );
}
