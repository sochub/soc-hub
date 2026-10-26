import { useState } from 'react';

import { Button } from '../../components/ui';
import type { MilestoneKey, ReportMilestones } from '../../types';
import { DATE_INPUT_BOUNDS, MILESTONES, apiDetail, fmtLocal, isoToLocal, localToIso, useSaveMilestones } from './milestones';

/** The four case milestones: declared values are editable; blank means "use the computed value". */
export default function MilestonesEditor({ caseId, milestones, canEdit, onDone }: {
    caseId: number; milestones: ReportMilestones; canEdit: boolean; onDone?: () => void;
}) {
    const initial = Object.fromEntries(MILESTONES.map(({ key }) =>
        [key, milestones[key].source === 'declared' ? isoToLocal(milestones[key].at) : ''])) as Record<MilestoneKey, string>;
    const [form, setForm] = useState(initial);
    const save = useSaveMilestones(caseId);
    const [invalid, setInvalid] = useState<string | null>(null);
    const changed = MILESTONES.filter(({ key }) => form[key] !== initial[key]);

    const submit = () => {
        const bad = changed.find(({ key }) => form[key] && localToIso(form[key]) === null);
        if (bad) { setInvalid(`${bad.label} is not a valid date`); return; }
        setInvalid(null);
        const patch = Object.fromEntries(changed.map(({ key }) => [`${key}_at`, form[key] ? localToIso(form[key]) : null]));
        save.mutate(patch, { onSuccess: () => onDone?.() });
    };

    return (
        <div className="space-y-3">
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
                {MILESTONES.map(({ key, label }) => {
                    const m = milestones[key];
                    return (
                        <div key={key}>
                            <label htmlFor={`ms-${key}`} className="block text-xs font-medium text-fg-2 mb-1.5">{label}</label>
                            <input id={`ms-${key}`} type="datetime-local" disabled={!canEdit} value={form[key]} {...DATE_INPUT_BOUNDS}
                                onChange={(e) => setForm({ ...form, [key]: e.target.value })}
                                className="w-full text-sm num border border-line rounded-sm px-2 py-1.5 bg-panel text-fg focus:outline-hidden focus:border-accent disabled:bg-surface" />
                            <div className="mt-1 text-[11px] text-muted flex items-center gap-2 min-h-[16px]">
                                {form[key] ? (
                                    <>
                                        <span>Declared</span>
                                        {canEdit && <button type="button" onClick={() => setForm({ ...form, [key]: '' })}
                                            className="text-accent hover:underline">clear</button>}
                                    </>
                                ) : (
                                    <span title="Computed from case data">
                                        Computed: <span className="num">{m.source === 'declared' ? 'after saving' : fmtLocal(m.at)}</span>
                                    </span>
                                )}
                            </div>
                        </div>
                    );
                })}
            </div>
            {canEdit && (
                <div className="flex items-center justify-end gap-3">
                    {(invalid || save.isError) && <span role="alert" className="text-xs text-severity-critical">{invalid ?? apiDetail(save.error)}</span>}
                    <span className="text-[11px] text-muted">Local time · shared with the report</span>
                    <Button size="sm" disabled={!changed.length || save.isPending} onClick={submit}>
                        {save.isPending ? 'Saving…' : 'Save milestones'}
                    </Button>
                </div>
            )}
        </div>
    );
}
