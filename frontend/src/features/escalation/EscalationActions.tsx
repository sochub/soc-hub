import { useState } from 'react';
import { ArrowUpCircle } from 'lucide-react';
import { Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { api } from '../../api/client';
import Modal, { modalInput, modalLabel, btnPrimary, btnSecondary } from '../../components/layout/Modal';
import type { Case, User } from '../../types';
import { errMsg, useEscalationAction, useGroups, usePolicy } from './api';
import EscalateModal from './EscalateModal';

const btn = 'inline-flex items-center gap-1.5 h-8 rounded-sm border border-line-strong px-2.5 text-xs text-fg-2 hover:bg-raised whitespace-nowrap disabled:opacity-50';

/** Prompt for a note (redirect target and/or note, return, withdraw). */
function NoteModal({ caseId, kind, groupOptions, onClose }: {
    caseId: number; kind: 'redirect' | 'return' | 'withdraw';
    groupOptions: { id: number; name: string }[]; onClose: () => void;
}) {
    const [note, setNote] = useState('');
    const [groupId, setGroupId] = useState<number | ''>(groupOptions[0]?.id ?? '');
    const [error, setError] = useState('');
    const m = useEscalationAction(caseId, kind);
    const needsNote = kind === 'return' || kind === 'redirect';
    const submit = (ev: React.FormEvent) => {
        ev.preventDefault();
        setError('');
        const body = kind === 'redirect' ? { to_group_id: groupId, note } : { note };
        m.mutate(body, { onSuccess: onClose, onError: (e) => setError(errMsg(e, `Could not ${kind}`)) });
    };
    return (
        <Modal open onClose={onClose} title={`${kind[0].toUpperCase()}${kind.slice(1)} escalation`} size="md">
            <form onSubmit={submit} className="space-y-4">
                {kind === 'redirect' && (
                    <div>
                        <label className={modalLabel}>Redirect to group</label>
                        <select className={modalInput} value={groupId} onChange={(e) => setGroupId(Number(e.target.value))} required>
                            {groupOptions.map((g) => <option key={g.id} value={g.id}>{g.name}</option>)}
                        </select>
                    </div>
                )}
                <div>
                    <label className={modalLabel}>Note{needsNote ? '' : ' (optional)'}</label>
                    <textarea className={modalInput} rows={3} value={note} onChange={(e) => setNote(e.target.value)} required={needsNote} />
                </div>
                {error && <p role="alert" className="text-sm text-severity-critical">{error}</p>}
                <div className="flex justify-end gap-2">
                    <button type="button" className={btnSecondary} onClick={onClose}>Cancel</button>
                    <button type="submit" className={btnPrimary} disabled={m.isPending || (kind === 'redirect' && groupId === '')}>
                        {m.isPending ? 'Working…' : 'Confirm'}
                    </button>
                </div>
            </form>
        </Modal>
    );
}

export default function EscalationActions({ caseData, me }: { caseData: Case; me?: User }) {
    const caseId = caseData.id;
    const esc = caseData.escalation ?? null;
    const isAdmin = !!me && (me.role === 'admin' || !!me.is_super_admin);
    const isAnalyst = isAdmin || me?.role === 'analyst';
    const { data: policy } = usePolicy();
    const { data: groups = [] } = useGroups();
    const [modal, setModal] = useState<'escalate' | 'redirect' | 'return' | 'withdraw' | null>(null);
    const accept = useEscalationAction(caseId, 'accept');
    const [acceptErr, setAcceptErr] = useState('');
    // Reuse the cached /users/me for member checks if `me` is not passed.
    const { data: cached } = useQuery({ queryKey: ['currentUser'], queryFn: async () => (await api.get('/users/me')).data as User, staleTime: 300_000, enabled: !me });
    const user = me ?? cached;

    if (!isAnalyst) return null;
    const tiers = policy?.tiers ?? [];
    const current = esc?.tier_position ?? 1;
    // Resolved/closed cases can't be escalated, redirected or returned (the server answers 409).
    const closed = caseData.status === 'resolved' || caseData.status === 'closed';
    const canEscalate = !closed && tiers.some((t) => (t.position ?? 0) > current);
    const isMember = !!user && groups.some((g) => g.id === esc?.group_id && g.members.some((m) => m.user_id === user.id));
    const pending = esc?.status === 'pending';
    const sameTier = tiers.find((t) => t.position === esc?.tier_position);
    const redirectGroups = groups.filter((g) => sameTier?.group_ids.includes(g.id) && g.id !== esc?.group_id);

    return (
        <>
            {canEscalate && (
                <button type="button" className={btn} onClick={() => setModal('escalate')}>
                    <ArrowUpCircle size={12} /> {esc ? 'Re-escalate' : 'Escalate'}
                </button>
            )}
            {!canEscalate && !closed && !pending && (
                <>
                    <button type="button" className={btn} disabled aria-describedby="escalate-hint">
                        <ArrowUpCircle size={12} /> {esc ? 'Re-escalate' : 'Escalate'}
                    </button>
                    <span id="escalate-hint" className="text-xs text-fg-3">
                        {isAdmin
                            ? <Link to="/settings/escalation" className="text-accent hover:underline">Set up escalation tiers</Link>
                            : 'Ask an admin to set up escalation tiers (Settings → Escalation)'}
                    </span>
                </>
            )}
            {pending && (isMember || isAdmin) && (
                <button type="button" className={btn} disabled={accept.isPending} title={acceptErr || undefined}
                    onClick={() => { setAcceptErr(''); accept.mutate({}, { onError: (e) => setAcceptErr(errMsg(e, 'Could not accept')) }); }}>
                    Accept
                </button>
            )}
            {pending && !closed && redirectGroups.length > 0 && <button type="button" className={btn} onClick={() => setModal('redirect')}>Redirect</button>}
            {pending && <button type="button" className={btn} onClick={() => setModal('withdraw')}>Withdraw</button>}
            {!closed && esc?.status === 'accepted' && esc.tier_position > 1 && <button type="button" className={btn} onClick={() => setModal('return')}>Return</button>}
            {acceptErr && <span role="alert" className="text-xs text-severity-critical">{acceptErr}</span>}
            <EscalateModal open={modal === 'escalate'} onClose={() => setModal(null)} caseId={caseId} tiers={tiers} groups={groups} currentTier={current} />
            {(modal === 'redirect' || modal === 'return' || modal === 'withdraw') && (
                <NoteModal caseId={caseId} kind={modal} groupOptions={redirectGroups} onClose={() => setModal(null)} />
            )}
        </>
    );
}
