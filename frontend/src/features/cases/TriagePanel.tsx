import type { ReactNode } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { Sparkles, Check, X, Loader2, RefreshCw, ArrowUpRight, AlertTriangle } from 'lucide-react';
import { api } from '../../api/client';
import { Button, Badge, SeverityStamp } from '../../components/ui';
import type { CaseTriage, PlaybookSummary, TriageItemStatus, User } from '../../types';

function ItemFrame({ status, children }: { status: TriageItemStatus; children: ReactNode }) {
    if (status === 'dismissed') return null;
    return <div className="flex items-start gap-3 py-2.5 border-t border-line first:border-t-0">{children}</div>;
}

function TriageActions({ status, pending, canEdit, onConfirm, onDismiss }: {
    status: TriageItemStatus; pending: boolean; canEdit?: boolean; onConfirm: () => void; onDismiss: () => void;
}) {
    if (status === 'confirmed') {
        return <span className="shrink-0 text-xs text-emerald-700 dark:text-emerald-300 flex items-center gap-1"><Check size={13} aria-hidden /> Confirmed</span>;
    }
    if (!canEdit) return null;
    return (
        <div className="shrink-0 flex items-center gap-1.5">
            <Button size="sm" variant="secondary" className="h-7 px-2" onClick={onConfirm} disabled={pending}>
                {pending ? <Loader2 size={12} className="animate-spin" aria-hidden /> : <Check size={12} aria-hidden />} Confirm
            </Button>
            <Button size="icon" variant="ghost" className="h-7 w-7" onClick={onDismiss} disabled={pending} aria-label="Dismiss">
                <X size={13} />
            </Button>
        </div>
    );
}

export default function TriagePanel({ caseId }: { caseId: number }) {
    const qc = useQueryClient();
    const key = ['case-triage', caseId];

    const { data: me } = useQuery({
        queryKey: ['currentUser'],
        queryFn: async () => (await api.get('/users/me')).data as User,
        staleTime: 5 * 60 * 1000,
    });
    const canEdit = me?.role === 'admin' || me?.role === 'analyst' || me?.is_super_admin;

    const { data: triage, isLoading } = useQuery({
        queryKey: key,
        queryFn: async () => {
            try {
                return (await api.get(`/cases/${caseId}/triage/latest`)).data as CaseTriage;
            } catch (e: any) {
                if (e?.response?.status === 404) return null;
                throw e;
            }
        },
        refetchInterval: (query) => (query.state.data?.status === 'pending' ? 3000 : false),
    });

    const { data: playbooks = [] } = useQuery({
        queryKey: ['playbooks', 'mine'],
        queryFn: async () => (await api.get('/playbooks/')).data as PlaybookSummary[],
        enabled: !!triage?.proposed_playbook_template_id,
    });
    const playbookName = playbooks.find((p) => p.id === triage?.proposed_playbook_template_id)?.name;

    const invalidate = () => {
        qc.invalidateQueries({ queryKey: key });
        qc.invalidateQueries({ queryKey: ['case', String(caseId)] });
        qc.invalidateQueries({ queryKey: ['case-tasks', caseId] });
    };

    const runTriage = useMutation({
        mutationFn: async () => (await api.post(`/cases/${caseId}/triage`)).data as CaseTriage,
        onSuccess: (data) => qc.setQueryData(key, data),
    });

    const setItemStatus = useMutation({
        mutationFn: async ({ field, status }: { field: string; status: 'confirmed' | 'dismissed' }) =>
            api.patch(`/cases/${caseId}/triage/${triage!.id}`, { field, status }),
        onSuccess: invalidate,
    });

    const confirmSeverityTags = useMutation({
        mutationFn: async () => {
            await api.post('/copilot/actions/execute', {
                type: 'update_case', case_id: caseId,
                params: { severity: triage!.proposed_severity, tags: triage!.proposed_tags },
            });
            await api.patch(`/cases/${caseId}/triage/${triage!.id}`, { field: 'severity_tags', status: 'confirmed' });
        },
        onSuccess: invalidate,
    });

    const confirmPlaybook = useMutation({
        mutationFn: async () => {
            await api.post('/copilot/actions/execute', {
                type: 'apply_playbook', case_id: caseId,
                params: { template_id: triage!.proposed_playbook_template_id },
            });
            await api.patch(`/cases/${caseId}/triage/${triage!.id}`, { field: 'playbook', status: 'confirmed' });
        },
        onSuccess: invalidate,
    });

    const confirmNextSteps = useMutation({
        mutationFn: async () => {
            await api.post('/copilot/actions/execute', {
                type: 'add_timeline_note', case_id: caseId,
                params: { content: triage!.next_steps_text },
            });
            await api.patch(`/cases/${caseId}/triage/${triage!.id}`, { field: 'next_steps', status: 'confirmed' });
        },
        onSuccess: invalidate,
    });

    if (isLoading) return null;

    // Empty state — no triage run yet. Analysts/admins can kick one off; viewers see nothing.
    if (!triage) {
        if (!canEdit) return null;
        return (
            <div className="mb-4 flex flex-wrap items-center gap-x-3 gap-y-1 text-sm text-fg-3">
                <span>No AI triage yet.</span>
                <button onClick={() => runTriage.mutate()} disabled={runTriage.isPending}
                    className="inline-flex items-center gap-1.5 text-accent hover:underline disabled:opacity-50 rounded-sm">
                    {runTriage.isPending ? <Loader2 size={13} className="animate-spin" /> : <Sparkles size={13} />}
                    Suggest severity, tags and playbook
                </button>
                {runTriage.isError && <span role="alert" className="text-severity-critical">Could not start triage. Is the AI provider reachable?</span>}
            </div>
        );
    }

    if (triage.status === 'pending') {
        return (
            <div role="status" className="border-y border-line py-3 mb-6 flex items-center gap-2.5 text-sm text-fg-3">
                <Loader2 size={15} className="animate-spin text-accent" aria-hidden /> AI triage running…
            </div>
        );
    }

    if (triage.status === 'failed') {
        return (
            <div className="border-y border-line py-3 mb-6 flex items-center justify-between gap-4">
                <p className="text-sm text-severity-critical flex items-center gap-2">
                    <AlertTriangle size={15} className="shrink-0" aria-hidden /> {triage.error_message || 'AI triage failed.'}
                </p>
                {canEdit && (
                    <Button size="sm" variant="secondary" className="shrink-0" onClick={() => runTriage.mutate()} disabled={runTriage.isPending}>
                        <RefreshCw size={13} aria-hidden /> Retry
                    </Button>
                )}
            </div>
        );
    }

    const hasSeverityTags = triage.severity_tags_status !== 'dismissed'
        && (!!triage.proposed_severity || (triage.proposed_tags?.length ?? 0) > 0);
    const hasPlaybook = triage.playbook_status !== 'dismissed' && !!triage.proposed_playbook_template_id;
    const hasRelated = (triage.related_case_ids?.length ?? 0) > 0;
    const hasNextSteps = triage.next_steps_status !== 'dismissed' && !!triage.next_steps_text;
    const nothing = !hasSeverityTags && !hasPlaybook && !hasRelated && !hasNextSteps;

    return (
        <div className="border-y border-line mb-6">
            <div className="flex items-center justify-between px-4 h-10 border-b border-line">
                <span className="label-caps flex items-center gap-2">
                    <Sparkles size={13} className="text-accent" aria-hidden /> AI triage
                </span>
                {canEdit && (
                    <Button size="sm" variant="ghost" className="h-7" onClick={() => runTriage.mutate()} disabled={runTriage.isPending}>
                        {runTriage.isPending ? <Loader2 size={12} className="animate-spin" aria-hidden /> : <RefreshCw size={12} aria-hidden />}
                        Re-triage
                    </Button>
                )}
            </div>

            <div className="px-4">
                {nothing && <p className="py-3 text-sm text-fg-3">No suggestions from this run.</p>}

                {hasSeverityTags && (
                    <ItemFrame status={triage.severity_tags_status}>
                        <div className="flex-1 min-w-0 flex items-center gap-2 flex-wrap">
                            {triage.proposed_severity && (
                                <SeverityStamp severity={triage.proposed_severity} />
                            )}
                            {triage.proposed_tags?.map((t) => (
                                <Badge key={t}>{t}</Badge>
                            ))}
                        </div>
                        <TriageActions status={triage.severity_tags_status} pending={confirmSeverityTags.isPending}
                            canEdit={canEdit} onConfirm={() => confirmSeverityTags.mutate()}
                            onDismiss={() => setItemStatus.mutate({ field: 'severity_tags', status: 'dismissed' })} />
                    </ItemFrame>
                )}

                {hasPlaybook && (
                    <ItemFrame status={triage.playbook_status}>
                        <p className="flex-1 min-w-0 text-sm text-fg-2">
                            Suggested playbook: <span className="font-medium">{playbookName ?? `#${triage.proposed_playbook_template_id}`}</span>
                        </p>
                        <TriageActions status={triage.playbook_status} pending={confirmPlaybook.isPending}
                            canEdit={canEdit} onConfirm={() => confirmPlaybook.mutate()}
                            onDismiss={() => setItemStatus.mutate({ field: 'playbook', status: 'dismissed' })} />
                    </ItemFrame>
                )}

                {hasRelated && (
                    <div className="flex items-start gap-3 py-2.5 border-t border-line first:border-t-0">
                        <p className="flex-1 min-w-0 text-sm text-fg-2">
                            Related cases:{' '}
                            {triage.related_case_ids!.map((id, i) => (
                                <span key={id}>
                                    {i > 0 && ', '}
                                    <Link to={`/cases/${id}`} className="text-accent hover:underline inline-flex items-center gap-0.5 num">
                                        #{id} <ArrowUpRight size={11} aria-hidden />
                                    </Link>
                                </span>
                            ))}
                        </p>
                    </div>
                )}

                {hasNextSteps && (
                    <ItemFrame status={triage.next_steps_status}>
                        <p className="flex-1 min-w-0 text-sm text-fg-2 italic">&ldquo;{triage.next_steps_text}&rdquo;</p>
                        <TriageActions status={triage.next_steps_status} pending={confirmNextSteps.isPending}
                            canEdit={canEdit} onConfirm={() => confirmNextSteps.mutate()}
                            onDismiss={() => setItemStatus.mutate({ field: 'next_steps', status: 'dismissed' })} />
                    </ItemFrame>
                )}
            </div>
            <div className="h-3" />
        </div>
    );
}
