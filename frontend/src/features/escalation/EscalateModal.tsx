import { useState } from 'react';
import Modal, { modalInput, modalLabel, btnPrimary, btnSecondary } from '../../components/layout/Modal';
import type { EscalationGroup, EscalationTier } from '../../types';
import { errMsg, useEscalationAction } from './api';

interface Props {
    open: boolean; onClose: () => void; caseId: number;
    tiers: EscalationTier[]; groups: EscalationGroup[]; currentTier: number;
}

export default function EscalateModal({ open, onClose, caseId, tiers, groups, currentTier }: Props) {
    const options = tiers.filter((t) => (t.position ?? 0) > currentTier && t.id != null);
    return (
        <Modal open={open} onClose={onClose} title="Escalate case" size="md">
            {/* Inner form remounts on each open so its draft state is fresh. */}
            {open && <Form caseId={caseId} options={options} groups={groups} onClose={onClose} />}
        </Modal>
    );
}

function Form({ caseId, options, groups, onClose }: { caseId: number; options: EscalationTier[]; groups: EscalationGroup[]; onClose: () => void }) {
    const [tierId, setTierId] = useState<number | ''>(options[0]?.id ?? '');
    const [groupId, setGroupId] = useState<number | ''>('');
    const [reason, setReason] = useState('');
    const [summary, setSummary] = useState('');
    const [help, setHelp] = useState('');
    const [error, setError] = useState('');
    const escalate = useEscalationAction(caseId, 'escalate');

    const tier = options.find((t) => t.id === tierId);
    const tierGroups = groups.filter((g) => tier?.group_ids.includes(g.id));
    const effGroup = tierGroups.length === 1 ? tierGroups[0].id : groupId;
    const ready = tierId !== '' && effGroup !== '' && reason.trim() && summary.trim() && help.trim();

    const submit = (ev: React.FormEvent) => {
        ev.preventDefault();
        if (!ready) return;
        setError('');
        escalate.mutate(
            { to_tier_id: tierId, to_group_id: effGroup, reason: reason.trim(), summary: summary.trim(), requested_help: help.trim() },
            { onSuccess: onClose, onError: (e) => setError(errMsg(e, 'Could not escalate')) },
        );
    };

    const area = (label: string, v: string, set: (s: string) => void) => (
        <div>
            <label className={modalLabel}>{label}</label>
            <textarea className={modalInput} rows={3} value={v} onChange={(e) => set(e.target.value)} required />
        </div>
    );

    return (
        <form onSubmit={submit} className="space-y-4">
            <div className="grid grid-cols-2 gap-3">
                <div>
                    <label className={modalLabel}>Tier</label>
                    <select className={modalInput} value={tierId} onChange={(e) => { setTierId(Number(e.target.value)); setGroupId(''); }}>
                        {options.map((t) => <option key={t.id} value={t.id!}>Tier {t.position} · {t.name}</option>)}
                    </select>
                </div>
                <div>
                    <label className={modalLabel}>Group</label>
                    <select className={modalInput} value={effGroup} disabled={tierGroups.length === 1}
                        onChange={(e) => setGroupId(Number(e.target.value))} required>
                        {tierGroups.length !== 1 && <option value="">Select a group</option>}
                        {tierGroups.map((g) => <option key={g.id} value={g.id}>{g.name}</option>)}
                    </select>
                </div>
            </div>
            {area('Reason for escalating', reason, setReason)}
            {area('Investigation summary', summary, setSummary)}
            {area('Help requested', help, setHelp)}
            {error && <p role="alert" className="text-sm text-severity-critical">{error}</p>}
            <div className="flex justify-end gap-2">
                <button type="button" className={btnSecondary} onClick={onClose}>Cancel</button>
                <button type="submit" className={btnPrimary} disabled={!ready || escalate.isPending}>
                    {escalate.isPending ? 'Escalating…' : 'Escalate'}
                </button>
            </div>
        </form>
    );
}
