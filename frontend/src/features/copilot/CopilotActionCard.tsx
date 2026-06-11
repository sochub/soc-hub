import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { Check, X, Loader2, Zap, ArrowUpRight, Lightbulb } from 'lucide-react';
import { api } from '../../api/client';
import type { CopilotAction, ActionResult } from '../../types';

const WRITE_ACTIONS = new Set(['create_case', 'add_artifact', 'add_timeline_note', 'update_case']);

const ACTION_LABEL: Record<string, string> = {
    create_case: 'Create case',
    add_artifact: 'Add artifact',
    add_timeline_note: 'Add note',
    update_case: 'Update case',
    find_related: 'Find related cases',
};

/** Exact payload preview so the user sees what will be written before confirming. */
function ParamPreview({ action }: { action: CopilotAction }) {
    const p = action.params as Record<string, any>;
    switch (action.type) {
        case 'add_timeline_note':
            return p.content ? (
                <blockquote className="border-l-2 border-brand-500/50 pl-2 my-1.5 text-slate-300 text-xs italic whitespace-pre-wrap">
                    “{p.content}”
                </blockquote>
            ) : null;
        case 'create_case':
            return (
                <dl className="my-1.5 space-y-0.5 text-xs">
                    <div><dt className="inline text-slate-500">title: </dt><dd className="inline text-slate-200">{p.title}</dd></div>
                    {p.severity && <div><dt className="inline text-slate-500">severity: </dt><dd className="inline text-slate-200">{p.severity}</dd></div>}
                    {p.description && <div><dt className="inline text-slate-500">description: </dt><dd className="inline text-slate-300">{String(p.description).slice(0, 140)}</dd></div>}
                </dl>
            );
        case 'add_artifact':
            return (
                <p className="my-1.5 text-xs">
                    <code className="bg-slate-900 text-brand-300 px-1 py-0.5">{p.value}</code>
                    <span className="text-slate-500 ml-1.5">({p.artifact_type})</span>
                </p>
            );
        case 'update_case':
            return (
                <p className="my-1.5 text-xs text-slate-300">
                    {['status', 'severity'].filter(k => p[k]).map(k => `${k} → ${p[k]}`).join(' · ') || null}
                </p>
            );
        default:
            return null;
    }
}

interface Props {
    action: CopilotAction;
    caseId?: number | null;
}

export default function CopilotActionCard({ action, caseId }: Props) {
    const qc = useQueryClient();
    const [state, setState] = useState<'idle' | 'cancelled'>('idle');
    const isWrite = WRITE_ACTIONS.has(action.type);

    const exec = useMutation({
        mutationFn: async () => {
            const res = await api.post('/copilot/actions/execute', {
                type: action.type,
                params: action.params,
                case_id: caseId ?? null,
            });
            return res.data as ActionResult;
        },
        onSuccess: () => {
            // Refresh anything the action may have changed.
            qc.invalidateQueries({ queryKey: ['cases'] });
            qc.invalidateQueries({ queryKey: ['artifacts'] });
            if (caseId) qc.invalidateQueries({ queryKey: ['case', String(caseId)] });
        },
    });

    // Read-only correlation runs automatically.
    useEffect(() => {
        if (!isWrite && state === 'idle' && !exec.isPending && !exec.data) {
            exec.mutate();
        }
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);

    const result = exec.data;

    return (
        <div className="mt-2 rounded-xl border border-brand-500/30 bg-brand-500/[0.06] p-3 text-sm">
            <div className="flex items-center gap-2 mb-1.5 text-brand-300 font-medium">
                <Zap size={14} />
                <span>{ACTION_LABEL[action.type] ?? action.type}</span>
            </div>

            {action.summary && <p className="text-slate-300 mb-1">{action.summary}</p>}
            {!result && !exec.isPending && <ParamPreview action={action} />}

            {/* Result */}
            {result && (
                <div className="text-slate-200">
                    <p className="flex items-center gap-1.5">
                        <Check size={14} className="text-emerald-400 shrink-0" />
                        {result.message}
                    </p>
                    {result.case_id && action.type === 'create_case' && (
                        <Link to={`/cases/${result.case_id}`} className="inline-flex items-center gap-1 mt-1.5 text-brand-300 hover:text-brand-200">
                            Open case #{result.case_id} <ArrowUpRight size={13} />
                        </Link>
                    )}
                    {result.related && result.related.length > 0 && (
                        <ul className="mt-1.5 space-y-1">
                            {result.related.map((r) => (
                                <li key={r.case_id}>
                                    <Link to={`/cases/${r.case_id}`} className="text-brand-300 hover:text-brand-200 inline-flex items-center gap-1">
                                        #{r.case_id} {r.title} <ArrowUpRight size={12} />
                                    </Link>
                                    <span className="text-xs text-slate-500"> — shares {r.shared_values.join(', ')}</span>
                                </li>
                            ))}
                        </ul>
                    )}
                </div>
            )}

            {/* Error */}
            {exec.isError && (
                <p className="text-severity-critical text-xs">
                    {(exec.error as any)?.response?.data?.detail ?? 'Action failed.'}
                </p>
            )}

            {/* Pending */}
            {exec.isPending && (
                <p className="flex items-center gap-2 text-slate-400">
                    <Loader2 size={14} className="animate-spin" /> Working…
                </p>
            )}

            {/* Confirm / cancel for write actions */}
            {isWrite && !result && !exec.isPending && (
                state === 'cancelled' ? (
                    <p className="text-slate-500 text-xs">Cancelled.</p>
                ) : (
                    <div className="flex items-center gap-2 mt-1">
                        <button
                            onClick={() => exec.mutate()}
                            className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-brand-500 text-slate-950 text-xs font-semibold hover:bg-brand-400 transition-colors"
                        >
                            <Check size={13} /> Confirm
                        </button>
                        <button
                            onClick={() => setState('cancelled')}
                            className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-slate-700 text-slate-300 text-xs hover:bg-slate-800/60 transition-colors"
                        >
                            <X size={13} /> Cancel
                        </button>
                    </div>
                )
            )}
        </div>
    );
}

/** Proactive suggestion chip — "I noticed X, want me to add it?" with Add/Dismiss. */
export function CopilotSuggestionChip({ action, caseId }: Props) {
    const qc = useQueryClient();
    const [dismissed, setDismissed] = useState(false);

    const exec = useMutation({
        mutationFn: async () => {
            const res = await api.post('/copilot/actions/execute', {
                type: action.type,
                params: action.params,
                case_id: caseId ?? null,
            });
            return res.data as ActionResult;
        },
        onSuccess: () => {
            qc.invalidateQueries({ queryKey: ['cases'] });
            qc.invalidateQueries({ queryKey: ['artifacts'] });
            if (caseId) {
                qc.invalidateQueries({ queryKey: ['case', String(caseId)] });
                qc.invalidateQueries({ queryKey: ['case-tasks', caseId] });
            }
        },
    });

    if (dismissed) return null;

    if (exec.data) {
        return (
            <p className="mt-1.5 flex items-center gap-1.5 text-xs text-emerald-400">
                <Check size={13} className="shrink-0" /> {exec.data.message}
            </p>
        );
    }

    return (
        <div className="mt-1.5 flex items-start gap-2 rounded-lg border border-amber-500/30 bg-amber-500/[0.07] px-2.5 py-2 text-xs">
            <Lightbulb size={13} className="text-amber-400 shrink-0 mt-0.5" />
            <span className="flex-1 text-slate-300">{action.summary}</span>
            {exec.isError && <span className="text-severity-critical">failed</span>}
            {exec.isPending ? (
                <Loader2 size={13} className="animate-spin text-slate-400 shrink-0 mt-0.5" />
            ) : (
                <span className="flex items-center gap-1 shrink-0">
                    <button onClick={() => exec.mutate()}
                        className="px-2 py-0.5 rounded bg-amber-500/90 text-slate-950 font-semibold hover:bg-amber-400 transition-colors">
                        Add
                    </button>
                    <button onClick={() => setDismissed(true)} aria-label="Dismiss suggestion"
                        className="p-0.5 text-slate-500 hover:text-slate-300">
                        <X size={13} />
                    </button>
                </span>
            )}
        </div>
    );
}
