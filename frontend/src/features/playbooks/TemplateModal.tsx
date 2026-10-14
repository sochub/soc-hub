import { useState } from 'react';
import { useQuery, useMutation } from '@tanstack/react-query';
import { Plus, Trash2 } from 'lucide-react';
import { api } from '../../api/client';
import type { PlaybookTemplate, PlaybookTaskTemplate } from '../../types';
import Modal, { btnPrimary, btnSecondary } from '../../components/layout/Modal';
import { PHASES, PHASE_LABEL, PHASE_COLOR, groupByPhase } from './phases';

interface Props {
    templateId: number | 'new';
    canEdit: boolean;
    /** when creating (templateId==='new'), create a global marketplace template */
    marketplace?: boolean;
    /** super admins may edit system/marketplace templates */
    isSuperAdmin?: boolean;
    onClose: () => void;
    onSaved: () => void;
}

type Draft = { name: string; category: string; description: string; tasks: PlaybookTaskTemplate[] };

export default function TemplateModal({ templateId, canEdit, marketplace, isSuperAdmin, onClose, onSaved }: Props) {
    const isNew = templateId === 'new';
    const [draft, setDraft] = useState<Draft | null>(isNew ? { name: '', category: 'other', description: '', tasks: [] } : null);

    const { data: template, isLoading } = useQuery({
        queryKey: ['playbook', templateId],
        queryFn: async () => (await api.get(`/playbooks/${templateId}`)).data as PlaybookTemplate,
        enabled: !isNew,
    });

    // editable when creating, when it's a tenant-owned template (admin), or when a
    // super admin is editing a system/marketplace template
    const editable = isNew || (!!template && canEdit && (!template.is_system || !!isSuperAdmin));

    // initialise draft from fetched template
    if (!isNew && template && draft === null) {
        setDraft({
            name: template.name, category: template.category, description: template.description ?? '',
            tasks: (template.tasks ?? []).map((t) => ({ ...t })),
        });
    }

    const save = useMutation({
        mutationFn: async () => {
            if (!draft) return;
            const payload = {
                name: draft.name, category: draft.category, description: draft.description,
                tasks: draft.tasks.map((t, i) => ({ phase: t.phase, title: t.title, description: t.description ?? '', order: i })),
            };
            if (isNew && marketplace) await api.post('/playbooks/marketplace', payload);
            else if (isNew) await api.post('/playbooks/', payload);
            else await api.put(`/playbooks/${templateId}`, payload);
        },
        onSuccess: onSaved,
    });

    const update = (patch: Partial<Draft>) => setDraft((d) => (d ? { ...d, ...patch } : d));
    const updateTask = (i: number, patch: Partial<PlaybookTaskTemplate>) =>
        setDraft((d) => d ? { ...d, tasks: d.tasks.map((t, j) => j === i ? { ...t, ...patch } : t) } : d);
    const addTask = () => setDraft((d) => d ? { ...d, tasks: [...d.tasks, { phase: 'identification', title: '', description: '', order: d.tasks.length }] } : d);
    const removeTask = (i: number) => setDraft((d) => d ? { ...d, tasks: d.tasks.filter((_, j) => j !== i) } : d);

    const body = () => {
        if (!isNew && isLoading) return <p className="font-mono text-xs text-muted">$ loading…</p>;
        if (!draft) return null;

        if (!editable) {
            // read-only grouped view (marketplace / system templates)
            const grouped = groupByPhase((template?.tasks ?? []) as PlaybookTaskTemplate[]);
            return (
                <div className="space-y-5">
                    {template?.description && <p className="text-sm text-fg-2">{template.description}</p>}
                    {grouped.map(([phase, tasks]) => (
                        <div key={phase}>
                            <div className="flex items-center gap-2 mb-2">
                                <span className="w-2 h-2" style={{ background: PHASE_COLOR[phase] }} />
                                <span className="label-mono">{PHASE_LABEL[phase]}</span>
                            </div>
                            <ul className="space-y-1.5 pl-4">
                                {tasks.map((t, i) => (
                                    <li key={i} className="text-sm">
                                        <span className="text-fg">{t.title}</span>
                                        {t.description && <span className="block text-xs text-fg-3">{t.description}</span>}
                                    </li>
                                ))}
                            </ul>
                        </div>
                    ))}
                </div>
            );
        }

        // editable form
        return (
            <div className="space-y-4">
                <div className="grid grid-cols-2 gap-3">
                    <div>
                        <label className="label-mono block mb-1">name</label>
                        <input value={draft.name} onChange={(e) => update({ name: e.target.value })}
                            className="w-full border border-line-strong px-2.5 py-1.5 text-sm focus:outline-hidden focus:ring-1 focus:ring-accent" />
                    </div>
                    <div>
                        <label className="label-mono block mb-1">category</label>
                        <input value={draft.category} onChange={(e) => update({ category: e.target.value })}
                            className="w-full border border-line-strong px-2.5 py-1.5 text-sm focus:outline-hidden focus:ring-1 focus:ring-accent" />
                    </div>
                </div>
                <div>
                    <label className="label-mono block mb-1">description</label>
                    <textarea value={draft.description} onChange={(e) => update({ description: e.target.value })} rows={2}
                        className="w-full border border-line-strong px-2.5 py-1.5 text-sm focus:outline-hidden focus:ring-1 focus:ring-accent" />
                </div>

                <div className="flex items-center justify-between">
                    <span className="label-mono">tasks ({draft.tasks.length})</span>
                    <button onClick={addTask} className="inline-flex items-center gap-1 text-xs text-accent font-medium hover:text-accent">
                        <Plus size={13} /> Add task
                    </button>
                </div>
                <div className="space-y-2">
                    {draft.tasks.map((t, i) => (
                        <div key={i} className="flex items-start gap-2 border border-line p-2">
                            <select value={t.phase} onChange={(e) => updateTask(i, { phase: e.target.value })}
                                className="border border-line-strong text-xs px-1.5 py-1.5 font-mono shrink-0 w-32">
                                {PHASES.map((p) => <option key={p} value={p}>{PHASE_LABEL[p]}</option>)}
                            </select>
                            <div className="flex-1 space-y-1">
                                <input value={t.title} placeholder="Task title" onChange={(e) => updateTask(i, { title: e.target.value })}
                                    className="w-full border border-line-strong px-2 py-1 text-sm focus:outline-hidden focus:ring-1 focus:ring-accent" />
                                <input value={t.description ?? ''} placeholder="Description (optional)" onChange={(e) => updateTask(i, { description: e.target.value })}
                                    className="w-full border border-line px-2 py-1 text-xs text-fg-2 focus:outline-hidden focus:ring-1 focus:ring-accent" />
                            </div>
                            <button onClick={() => removeTask(i)} aria-label="Remove task" className="text-muted hover:text-severity-critical p-1"><Trash2 size={14} /></button>
                        </div>
                    ))}
                    {draft.tasks.length === 0 && <p className="text-xs text-muted font-mono">no tasks — add one</p>}
                </div>
            </div>
        );
    };

    return (
        <Modal
            open
            onClose={onClose}
            size="xl"
            title={isNew ? (marketplace ? 'New Marketplace Template' : 'New Playbook') : editable ? `Edit · ${template?.name ?? ''}` : template?.name ?? 'Playbook'}
            footer={editable ? (<>
                <button onClick={onClose} className={btnSecondary}>Cancel</button>
                <button onClick={() => save.mutate()} disabled={!draft?.name || save.isPending} className={btnPrimary}>
                    {save.isPending ? 'Saving…' : 'Save'}
                </button>
            </>) : undefined}
        >
            {body()}
        </Modal>
    );
}
