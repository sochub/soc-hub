import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '../../api/client';
import Modal, { modalInput, btnPrimary, btnSecondary } from '../../components/layout/Modal';

interface Props {
    alert: { id: number; title: string; source: string; payload: any };
    onClose: () => void;
}
interface CaseLite { id: number; title: string; }

export default function PromoteAlertModal({ alert, onClose }: Props) {
    const qc = useQueryClient();
    const [mode, setMode] = useState<'existing' | 'new'>('existing');
    const [caseId, setCaseId] = useState<string>('');

    const { data: cases } = useQuery<CaseLite[]>({
        queryKey: ['cases'],
        queryFn: async () => (await api.get('/cases/')).data,
    });

    const promote = useMutation({
        mutationFn: async () => {
            let targetId = caseId;
            if (mode === 'new') {
                const res = await api.post('/cases/', {
                    title: alert.title,
                    description: `Created from ${alert.source} alert.\n\n${JSON.stringify(alert.payload, null, 2)}`,
                    severity: 'medium',
                    status: 'new',
                    tags: [],
                    source: alert.source,
                });
                targetId = String(res.data.id);
            }
            await api.post(`/alerts/${alert.id}/promote/${targetId}`);
        },
        onSuccess: () => {
            qc.invalidateQueries({ queryKey: ['alerts'] });
            qc.invalidateQueries({ queryKey: ['cases'] });
            onClose();
        },
    });

    const canSubmit = mode === 'new' || !!caseId;

    return (
        <Modal
            open
            onClose={onClose}
            title="Promote alert"
            size="lg"
            footer={<>
                <button onClick={onClose} className={btnSecondary}>Cancel</button>
                <button disabled={!canSubmit || promote.isPending} onClick={() => promote.mutate()} className={btnPrimary}>
                    {promote.isPending ? 'Working…' : 'Promote'}
                </button>
            </>}
        >
            <p className="text-sm text-zinc-500 mb-4 truncate">{alert.title}</p>

            <div className="flex gap-2 mb-4">
                <button onClick={() => setMode('existing')} className={`flex-1 py-1.5 text-sm border ${mode === 'existing' ? 'bg-accent-600 text-white border-accent-600' : 'bg-white text-zinc-600 border-zinc-300'}`}>Existing case</button>
                <button onClick={() => setMode('new')} className={`flex-1 py-1.5 text-sm border ${mode === 'new' ? 'bg-accent-600 text-white border-accent-600' : 'bg-white text-zinc-600 border-zinc-300'}`}>New case</button>
            </div>

            {mode === 'existing' && (
                <select value={caseId} onChange={e => setCaseId(e.target.value)} className={modalInput}>
                    <option value="">Select a case…</option>
                    {cases?.map(c => <option key={c.id} value={c.id}>#{c.id} — {c.title}</option>)}
                </select>
            )}
            {mode === 'new' && (
                <p className="text-xs text-zinc-500">A new case titled "{alert.title}" will be created from this alert and linked.</p>
            )}
        </Modal>
    );
}
