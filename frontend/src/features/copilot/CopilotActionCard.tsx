import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { Check, X, Loader2, Zap, ArrowUpRight, Lightbulb, Pencil } from 'lucide-react';
import { api } from '../../api/client';
import { apiErrorMessage } from '../../api/errors';
import type { CopilotAction, ActionResult } from '../../types';
import { Button, Input, Select, Textarea, TONE_TEXT } from '../../components/ui';
import { CASE_OUTCOMES } from '../cases/outcomes';

const WRITE_ACTIONS = new Set(['create_case', 'add_artifact', 'add_timeline_note', 'update_case']);
/** Writes that land on one case: the card names that case before Confirm. */
const CASE_ACTIONS = new Set(['add_artifact', 'add_timeline_note', 'update_case']);

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
                <blockquote className="border-l-2 border-accent/50 pl-2 my-1.5 text-fg-2 text-xs italic whitespace-pre-wrap">
                    “{p.content}”
                </blockquote>
            ) : null;
        case 'create_case':
            return (
                <dl className="my-1.5 space-y-0.5 text-xs">
                    <div><dt className="inline text-fg-3">title: </dt><dd className="inline text-fg">{p.title}</dd></div>
                    {p.severity && <div><dt className="inline text-fg-3">severity: </dt><dd className="inline text-fg">{p.severity}</dd></div>}
                    {p.description && <div><dt className="inline text-fg-3">description: </dt><dd className="inline text-fg-2 whitespace-pre-wrap">{String(p.description)}</dd></div>}
                    {Array.isArray(p.artifacts) && p.artifacts.length > 0 && (
                        <div><dt className="inline text-fg-3">artifacts: </dt><dd className="inline">
                            {p.artifacts.map((a: { value: string; artifact_type: string }) => (
                                <code key={a.value} className="bg-panel text-accent px-1 py-0.5 mr-1">{a.value}</code>
                            ))}
                        </dd></div>
                    )}
                </dl>
            );
        case 'add_artifact':
            return (
                <p className="my-1.5 text-xs">
                    <code className="bg-panel text-accent px-1 py-0.5">{p.value}</code>
                    <span className="text-fg-3 ml-1.5">({p.artifact_type || 'other'})</span>
                    {p.description && <span className="text-fg-2 ml-1.5">— {String(p.description)}</span>}
                </p>
            );
        case 'update_case': {
            // The server adds tags to the case's own (never replaces them); a bare string is one tag.
            const tags = p.tags == null ? [] : ([] as unknown[]).concat(p.tags).map(String);
            const changes = ['status', 'severity'].filter(k => p[k]).map(k => `${k} → ${p[k]}`);
            if (tags.length) changes.push(`add tags: ${tags.join(', ')}`);
            return <p className="my-1.5 text-xs text-fg-2">{changes.join(' · ') || null}</p>;
        }
        default:
            return null;
    }
}

/** What the analyst may change before Confirm, per action (mirrors _EDITABLE in backend/app/api/v1/copilot.py). */
const EDITABLE: Record<string, string[]> = {
    add_timeline_note: ['content'],
    add_artifact: ['value', 'artifact_type', 'description'],
    create_case: ['title', 'severity', 'description'],
    update_case: ['tags'],
};
const CHOICES: Record<string, string[]> = {
    artifact_type: ['ip', 'domain', 'url', 'file_hash', 'email', 'hostname', 'username', 'other'],
    severity: ['critical', 'high', 'medium', 'low', 'info'],
};
type Draft = Record<string, string>;

/** What the server writes when the proposal leaves a choice out. */
const DEFAULTS: Record<string, string> = { artifact_type: 'other', severity: 'medium' };

/** A field's proposed value as the text the edit form starts from (tags: comma-separated). */
function proposed(action: CopilotAction, key: string): string {
    const value = action.params[key];
    if (key === 'tags') return (value == null ? [] : ([] as unknown[]).concat(value)).join(', ');
    return CHOICES[key] ? String(value || DEFAULTS[key]).toLowerCase() : String(value ?? '');
}

/** Only the fields the analyst actually changed, in the shape the server validates. */
function changedFields(action: CopilotAction, draft: Draft): Record<string, unknown> {
    const edits: Record<string, unknown> = {};
    for (const [key, text] of Object.entries(draft)) {
        if (text.trim() === proposed(action, key).trim()) continue;
        edits[key] = key === 'tags' ? text.split(',').map((t) => t.trim()).filter(Boolean) : text;
    }
    return edits;
}

function EditFields({ draft, onChange }: { draft: Draft; onChange: (d: Draft) => void }) {
    return (
        <div className="my-1.5 space-y-1.5">
            {Object.entries(draft).map(([key, text]) => {
                const common = {
                    value: text, 'aria-label': key.replace('_', ' '),
                    onChange: (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>) => onChange({ ...draft, [key]: e.target.value }),
                };
                if (CHOICES[key]) {
                    return (
                        <Select key={key} inputSize="sm" {...common}>
                            {CHOICES[key].map((o) => <option key={o} value={o}>{key.replace('_', ' ')}: {o}</option>)}
                        </Select>
                    );
                }
                if (key === 'content' || key === 'description') {
                    return <Textarea key={key} rows={key === 'content' ? 3 : 2} placeholder={key} className="text-xs" {...common} />;
                }
                return <Input key={key} inputSize="sm" placeholder={key === 'tags' ? 'tags, comma-separated' : key} {...common} />;
            })}
        </div>
    );
}

interface Props {
    action: CopilotAction;
    caseId?: number | null;
    /** The message that proposed it: the server stores the result there (and won't run it twice). */
    messageId: number;
    suggestionIndex?: number;
}

/** Sent with Confirm: the closure fields a closing proposal needs, and the values the analyst edited. */
type Extra = { outcome?: string; closing_note?: string; edits?: Record<string, unknown> };

function useExecute({ action, caseId, messageId, suggestionIndex }: Props) {
    const qc = useQueryClient();
    return useMutation({
        mutationFn: async (extra: Extra | void) => (await api.post('/copilot/actions/execute', {
            type: action.type,
            params: action.params,
            case_id: action.case_id ?? caseId ?? null,
            message_id: messageId,
            suggestion_index: suggestionIndex ?? null,
            ...(extra ?? {}),
        })).data as ActionResult,
        onSuccess: (r) => {
            // Refresh anything the action may have changed.
            for (const key of ['cases', 'artifacts', 'iocs']) qc.invalidateQueries({ queryKey: [key] });
            const id = r.case_id ?? action.case_id ?? caseId;
            if (id) {
                qc.invalidateQueries({ queryKey: ['case', String(id)] });
                qc.invalidateQueries({ queryKey: ['case-tasks', id] });
            }
        },
        // On a refusal too: the session holds the proposal's stored outcome (already run, cancelled, …).
        onSettled: () => qc.invalidateQueries({ queryKey: ['copilot-session'] }),
    });
}

/** Cancel / Dismiss is stored on the message, so the proposal stays settled after the panel is reopened. */
function useDismiss({ messageId, suggestionIndex }: Props, state: 'cancelled' | 'dismissed') {
    const qc = useQueryClient();
    return useMutation({
        mutationFn: async () => (await api.post('/copilot/actions/dismiss', {
            message_id: messageId, suggestion_index: suggestionIndex ?? null, state,
        })).data,
        onSettled: () => qc.invalidateQueries({ queryKey: ['copilot-session'] }),
    });
}

export default function CopilotActionCard(props: Props) {
    const { action, caseId } = props;
    const isWrite = WRITE_ACTIONS.has(action.type);
    const exec = useExecute(props);
    const cancel = useDismiss(props, 'cancelled');
    const [outcome, setOutcome] = useState('');
    const [closingNote, setClosingNote] = useState('');
    // The edit form's values while "Edit" is on; null shows the proposal as proposed.
    const [draft, setDraft] = useState<Draft | null>(null);

    // Read-only correlation runs automatically, once: its result is stored on the message.
    useEffect(() => {
        if (!isWrite && !exec.isPending && !exec.data && !action.result && !action.state) {
            exec.mutate();
        }
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);

    const result = exec.data ?? action.result;
    const label = ACTION_LABEL[action.type] ?? action.type;
    // Cancelled as soon as it is clicked; a failed save puts the buttons back (with the error below).
    const state = action.state ?? (cancel.isPending || cancel.isSuccess ? 'cancelled' : null);
    const target = action.case_id ?? (caseId && caseId > 0 ? caseId : null);
    const onCase = CASE_ACTIONS.has(action.type);
    const closing = action.type === 'update_case' && String(action.params.status ?? '').toLowerCase() === 'closed';
    const open = isWrite && !result && !state;  // still confirmable
    const blocked = (onCase && (!target || !!action.case_missing)) || (closing && (!outcome || !closingNote.trim()));
    const error = exec.isError ? exec.error : cancel.isError ? cancel.error : null;

    return (
        <div className="mt-2 rounded-md border border-accent/30 bg-accent/5 p-3 text-sm">
            <div className="flex items-center gap-2 mb-1.5 text-accent font-medium">
                <Zap size={14} aria-hidden />
                <span>{label}</span>
            </div>

            {action.summary && <p className="text-fg-2 mb-1">{action.summary}</p>}
            {/* Where it will be written, before Confirm */}
            {onCase && !result && (
                action.case_missing ? (
                    <p role="alert" className="text-severity-critical text-xs">Case #{action.case_id} no longer exists.</p>
                ) : target ? (
                    <p className="text-xs text-fg-3">
                        → case <span className="num">#{target}</span>{action.case_title ? ` · ${action.case_title}` : ''}
                    </p>
                ) : (
                    <p role="alert" className="text-severity-critical text-xs">
                        No target case. Open a case, or name one (“case #12”) and ask again.
                    </p>
                )
            )}
            {open && draft
                ? <EditFields draft={draft} onChange={setDraft} />
                : (!result || action.edited) && <ParamPreview action={action} />}
            {result && action.edited && <p className="text-xs text-fg-3">Edited before confirming.</p>}
            {open && closing && (
                <div className="my-1.5 space-y-1.5">
                    <Select inputSize="sm" value={outcome} onChange={(e) => setOutcome(e.target.value)} aria-label="Outcome" required>
                        <option value="">Outcome (required)…</option>
                        {CASE_OUTCOMES.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
                    </Select>
                    <Textarea
                        rows={2} maxLength={8000} value={closingNote} onChange={(e) => setClosingNote(e.target.value)}
                        placeholder="Closing note (required)" aria-label="Closing note" required className="text-xs"
                    />
                </div>
            )}

            {/* Result */}
            {result && (
                <div className="text-fg">
                    <p className="flex items-center gap-1.5">
                        <Check size={14} className={`${TONE_TEXT.ok} shrink-0`} aria-hidden />
                        {result.message}
                    </p>
                    {result.case_id && action.type === 'create_case' && (
                        <Link to={`/cases/${result.case_id}`} className="inline-flex items-center gap-1 mt-1.5 text-accent hover:underline">
                            Open case #{result.case_id} <ArrowUpRight size={13} />
                        </Link>
                    )}
                    {result.related && result.related.length > 0 && (
                        <ul className="mt-1.5 space-y-1">
                            {result.related.map((r) => (
                                <li key={r.case_id}>
                                    <Link to={`/cases/${r.case_id}`} className="text-accent hover:underline inline-flex items-center gap-1">
                                        #{r.case_id} {r.title} <ArrowUpRight size={12} />
                                    </Link>
                                    <span className="text-xs text-fg-3"> — shares {r.shared_values.join(', ')}</span>
                                </li>
                            ))}
                        </ul>
                    )}
                </div>
            )}

            {/* Error */}
            {error && !result && (
                <p role="alert" className="text-severity-critical text-xs">
                    {apiErrorMessage(error, 'Action failed.')}
                </p>
            )}

            {/* Pending (also a write confirmed from another tab) */}
            {(exec.isPending || (state === 'running' && !result)) && (
                <p role="status" className="flex items-center gap-2 text-fg-2">
                    <Loader2 size={14} className="animate-spin" aria-hidden /> Working…
                </p>
            )}
            {!result && (state === 'cancelled' || state === 'dismissed') && <p className="text-fg-3 text-xs">Cancelled.</p>}

            {/* Confirm / cancel for write actions */}
            {open && (
                <div className="flex items-center gap-2 mt-1">
                    <Button
                        size="sm" disabled={exec.isPending || blocked} aria-label={`Confirm: ${label}`}
                        onClick={() => {
                            const edits = draft ? changedFields(action, draft) : {};
                            exec.mutate({
                                ...(closing ? { outcome, closing_note: closingNote } : {}),
                                ...(Object.keys(edits).length ? { edits } : {}),
                            });
                        }}
                    >
                        <Check size={13} aria-hidden /> Confirm
                    </Button>
                    {EDITABLE[action.type] && (
                        <Button
                            size="sm" variant="ghost" disabled={exec.isPending}
                            aria-label={draft ? `Discard edits: ${label}` : `Edit before confirming: ${label}`}
                            onClick={() => setDraft(draft ? null : Object.fromEntries(
                                EDITABLE[action.type].map((key) => [key, proposed(action, key)])))}
                        >
                            {draft ? 'Cancel edit' : <><Pencil size={13} aria-hidden /> Edit</>}
                        </Button>
                    )}
                    <Button size="sm" variant="secondary" disabled={exec.isPending} aria-label={`Cancel: ${label}`} onClick={() => cancel.mutate()}>
                        <X size={13} aria-hidden /> Cancel
                    </Button>
                </div>
            )}
        </div>
    );
}

/** Proactive suggestion chip — "I noticed X, want me to add it?" with Add/Dismiss. */
export function CopilotSuggestionChip(props: Props) {
    const { action } = props;
    const exec = useExecute(props);
    const dismiss = useDismiss(props, 'dismissed');
    const done = exec.data ?? action.result;

    if (!done && (action.state === 'dismissed' || action.state === 'cancelled' || dismiss.isPending || dismiss.isSuccess)) return null;

    if (done) {
        return (
            <p className={`mt-1.5 flex items-center gap-1.5 text-xs ${TONE_TEXT.ok}`}>
                <Check size={13} className="shrink-0" aria-hidden /> {done.message}
            </p>
        );
    }

    return (
        <div className="mt-1.5 flex items-start gap-2 rounded-md border border-severity-medium/40 bg-severity-medium/10 px-2.5 py-2 text-xs">
            <Lightbulb size={13} className={`${TONE_TEXT.medium} shrink-0 mt-0.5`} aria-hidden />
            <span className="flex-1 text-fg-2">{action.summary}</span>
            {(exec.isError || dismiss.isError) && <span role="alert" className="text-severity-critical">Failed</span>}
            {exec.isPending || action.state === 'running' ? (
                <Loader2 size={13} className="animate-spin text-fg-2 shrink-0 mt-0.5" aria-label="Adding" />
            ) : (
                <span className="flex items-center gap-1 shrink-0">
                    <Button size="sm" variant="secondary" className="h-6 px-2" onClick={() => exec.mutate()} aria-label="Add suggestion">
                        Add
                    </Button>
                    <Button variant="ghost" className="h-6 w-6 p-0" onClick={() => dismiss.mutate()} aria-label="Dismiss suggestion">
                        <X size={13} />
                    </Button>
                </span>
            )}
        </div>
    );
}
