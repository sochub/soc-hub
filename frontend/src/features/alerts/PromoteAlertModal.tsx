import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '../../api/client';
import { X } from 'lucide-react';

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
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" onClick={onClose}>
            <div className="glass-panel rounded-xl border border-zinc-200 w-full max-w-md p-6" onClick={e => e.stopPropagation()}>
                <div className="flex items-center justify-between mb-4">
                    <h2 className="text-lg font-bold text-zinc-900">Promote alert</h2>
                    <button onClick={onClose} className="text-zinc-400 hover:text-zinc-700"><X size={18} /></button>
                </div>
                <p className="text-sm text-zinc-500 mb-4 truncate">{alert.title}</p>

                <div className="flex gap-2 mb-4">
                    <button onClick={() => setMode('existing')} className={`flex-1 py-1.5 text-sm rounded-lg border ${mode === 'existing' ? 'bg-accent-600 text-white border-accent-600' : 'bg-white text-zinc-600 border-zinc-200'}`}>Existing case</button>
                    <button onClick={() => setMode('new')} className={`flex-1 py-1.5 text-sm rounded-lg border ${mode === 'new' ? 'bg-accent-600 text-white border-accent-600' : 'bg-white text-zinc-600 border-zinc-200'}`}>New case</button>
                </div>

                {mode === 'existing' && (
                    <select value={caseId} onChange={e => setCaseId(e.target.value)} className="w-full bg-white border border-zinc-200 rounded-lg px-3 py-2 text-sm mb-4 focus:outline-none focus:ring-1 focus:ring-accent-500">
                        <option value="">Select a case…</option>
                        {cases?.map(c => <option key={c.id} value={c.id}>#{c.id} — {c.title}</option>)}
                    </select>
                )}
                {mode === 'new' && (
                    <p className="text-xs text-zinc-500 mb-4">A new case titled "{alert.title}" will be created from this alert and linked.</p>
                )}

                <button disabled={!canSubmit || promote.isPending} onClick={() => promote.mutate()}
                    className="w-full py-2 rounded-lg bg-accent-600 text-white text-sm font-semibold hover:bg-accent-700 disabled:opacity-50">
                    {promote.isPending ? 'Working…' : 'Promote'}
                </button>
            </div>
        </div>
    );
}
