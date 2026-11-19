import { useEffect, useState } from 'react';
import { useQuery, useMutation, useQueryClient, keepPreviousData } from '@tanstack/react-query';
import { api } from '../../api/client';
import Modal, { modalInput, btnPrimary, btnSecondary } from '../../components/layout/Modal';
import { getPage } from '../../api/paging';
import { apiErrorMessage } from '../../api/errors';

interface Props {
    alert: { id: number; title: string; source: string; payload: unknown };
    ids?: number[]; // bulk: promote all these via POST /alerts/bulk (alert seeds a new case)
    onClose: () => void;
}
interface CaseLite { id: number; title: string; }
const PICKER_LIMIT = 50;

export default function PromoteAlertModal({ alert, ids, onClose }: Props) {
    const qc = useQueryClient();
    const [mode, setMode] = useState<'existing' | 'new'>('existing');
    const [caseId, setCaseId] = useState<string>('');

    // The server searches all cases (title, description or id); the picker shows the newest matches.
    const [search, setSearch] = useState('');
    const [q, setQ] = useState('');
    useEffect(() => {
        const t = setTimeout(() => setQ(search.trim()), 300);
        return () => clearTimeout(t);
    }, [search]);
    const { data: found } = useQuery({
        queryKey: ['cases', 'promote-picker', q],
        queryFn: () => getPage<CaseLite>('/cases/', { ...(q && { q }), limit: PICKER_LIMIT }),
        placeholderData: keepPreviousData,
        enabled: mode === 'existing',
    });
    const cases = found?.items;

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
            if (ids) await api.post('/alerts/bulk', { ids, action: 'promote', case_id: Number(targetId) });
            else await api.post(`/alerts/${alert.id}/promote/${targetId}`);
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
            title={ids ? `Promote ${ids.length} alerts` : 'Promote alert'}
            size="lg"
            footer={<>
                <button onClick={onClose} className={btnSecondary}>Cancel</button>
                <button disabled={!canSubmit || promote.isPending} onClick={() => promote.mutate()} className={btnPrimary}>
                    {promote.isPending ? 'Working…' : 'Promote'}
                </button>
            </>}
        >
            <p className="text-sm text-fg-3 mb-4 truncate">{alert.title}</p>

            <div className="flex gap-2 mb-4">
                <button type="button" aria-pressed={mode === 'existing'} onClick={() => setMode('existing')} className={`flex-1 rounded-md py-1.5 text-sm border ${mode === 'existing' ? 'bg-accent text-on-accent border-accent' : 'bg-panel text-fg-2 border-line-strong'}`}>Existing case</button>
                <button type="button" aria-pressed={mode === 'new'} onClick={() => setMode('new')} className={`flex-1 rounded-md py-1.5 text-sm border ${mode === 'new' ? 'bg-accent text-on-accent border-accent' : 'bg-panel text-fg-2 border-line-strong'}`}>New case</button>
            </div>

            {mode === 'existing' && (
                <div className="space-y-2">
                    <input type="search" value={search} onChange={e => setSearch(e.target.value)}
                        placeholder="Search cases by ID or title…" aria-label="Search cases" className={modalInput} />
                    <select value={caseId} onChange={e => setCaseId(e.target.value)} className={modalInput} aria-label="Case">
                        <option value="">Select a case…</option>
                        {cases?.map(c => <option key={c.id} value={c.id}>#{c.id} — {c.title}</option>)}
                    </select>
                    {found && found.total > found.items.length && (
                        <p className="text-xs text-fg-3">Showing the newest {found.items.length} of {found.total} matching cases. Search to narrow down.</p>
                    )}
                </div>
            )}
            {mode === 'new' && (
                <p className="text-xs text-fg-3">A new case titled "{alert.title}" will be created from this alert and linked.</p>
            )}
            {promote.isError && (
                <p role="alert" className="mt-3 text-sm text-severity-critical">{apiErrorMessage(promote.error, 'Could not promote the alert.')}</p>
            )}
        </Modal>
    );
}
