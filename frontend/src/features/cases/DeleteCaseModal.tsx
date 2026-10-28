import { useState } from 'react';
import { AlertTriangle } from 'lucide-react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import { api } from '../../api/client';
import Modal, { modalInput, modalLabel, btnDanger, btnSecondary } from '../../components/layout/Modal';

interface Preview {
    tasks: number; activity_entries: number; artifacts: number; evidence_files: number; alerts: number; iocs: number;
    guests: number; tickets: number; active_containment: number; war_room: boolean; warnings: string[];
}

/** Admin-only: shows what goes with the case, then asks for the case number before deleting it for good. */
export default function DeleteCaseModal({ caseId, title, open, onClose }: {
    caseId: number; title: string; open: boolean; onClose: () => void;
}) {
    const [confirmText, setConfirmText] = useState('');
    const qc = useQueryClient();
    const navigate = useNavigate();
    const { data: pv, isLoading, isError } = useQuery({
        queryKey: ['case-delete-preview', caseId],
        queryFn: async () => (await api.get(`/cases/${caseId}/delete-preview`)).data as Preview,
        enabled: open, staleTime: 0,
    });
    const del = useMutation({
        mutationFn: async () => api.delete(`/cases/${caseId}`, { params: { confirm: caseId } }),
        onSuccess: () => {
            qc.removeQueries({ queryKey: ['case', String(caseId)] });
            qc.invalidateQueries({ queryKey: ['cases'] });
            qc.invalidateQueries({ queryKey: ['alerts'] });
            navigate('/cases');
        },
    });
    const close = () => { setConfirmText(''); del.reset(); onClose(); };
    const number = String(caseId).padStart(4, '0');
    const ok = confirmText.trim().replace(/^#/, '') === number || confirmText.trim() === String(caseId);
    const err = (del.error as { response?: { data?: { detail?: unknown } } } | null)?.response?.data?.detail;

    const removed = pv ? ([
        [pv.tasks, 'task', 'tasks'], [pv.activity_entries, 'activity entry', 'activity entries'],
        [pv.evidence_files, 'evidence file', 'evidence files'], [pv.artifacts, 'artifact link', 'artifact links'],
        [pv.guests, 'guest invitation', 'guest invitations'], [pv.tickets, 'ticket link', 'ticket links'],
    ] as [number, string, string][]).filter(([n]) => n > 0) : [];

    return (
        <Modal open={open} onClose={close} size="md"
            title={<span className="text-red-300 flex items-center gap-2"><AlertTriangle size={16} />Delete case #{number}</span>}
            footer={<>
                <button onClick={close} className={btnSecondary}>Cancel</button>
                <button onClick={() => del.mutate()} disabled={!ok || !pv || del.isPending} className={btnDanger}>
                    {del.isPending ? 'Deleting…' : 'Delete permanently'}
                </button>
            </>}>
            <div className="space-y-4 text-sm">
                <p className="text-fg-2">
                    This permanently deletes <span className="font-medium text-fg">{title}</span> and everything that
                    belongs only to it. It cannot be undone; the audit log keeps a record of the deletion.
                </p>
                {isLoading && <p className="text-muted">Checking what goes with it…</p>}
                {isError && <p role="alert" className="text-severity-critical">Could not load what the deletion affects.</p>}
                {pv && (
                    <>
                        {pv.warnings.length > 0 && (
                            <ul role="alert" className="space-y-1.5 border border-red-500/40 bg-red-500/10 p-3 text-red-200">
                                {pv.warnings.map((w) => <li key={w} className="flex gap-2"><AlertTriangle size={14} className="shrink-0 mt-0.5" />{w}</li>)}
                            </ul>
                        )}
                        {removed.length > 0 && (
                            <p className="text-fg-2">Deleted with it: {removed.map(([n, one, many]) => `${n} ${n === 1 ? one : many}`).join(', ')}.</p>
                        )}
                        <p className="text-muted">Kept: artifacts and IOCs stay in the organization{pv.alerts ? '; its alerts return to the Alerts queue' : ''}.</p>
                    </>
                )}
                <div>
                    <label className={modalLabel}>Type the case number <span className="font-mono text-red-300">{number}</span> to confirm</label>
                    <input type="text" autoComplete="off" value={confirmText} onChange={(e) => setConfirmText(e.target.value)}
                        className={`${modalInput} font-mono focus:border-red-500 focus:ring-red-500`} />
                </div>
                {del.isError && <p role="alert" className="text-severity-critical">{typeof err === 'string' ? err : 'Could not delete the case.'}</p>}
            </div>
        </Modal>
    );
}
