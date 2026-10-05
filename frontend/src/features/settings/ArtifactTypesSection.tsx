import { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { Tags, Plus, Pencil, Trash2, Loader2 } from 'lucide-react';
import { api } from '../../api/client';
import { btnPrimary, btnSecondary } from '../../components/layout/Modal';
import { useArtifactTypes, type ArtifactTypeDef } from '../artifacts/useArtifactTypes';

type Draft = { id?: number; key: string; label: string; payload_key: string; show_in_mindmap: boolean; private: boolean };
const EMPTY: Draft = { key: '', label: '', payload_key: '', show_in_mindmap: true, private: false };
const input = 'mt-1 w-full h-8 px-2 text-sm border border-zinc-300 bg-white focus:outline-none focus:border-accent-500';

function errorText(e: unknown): string {
    const d = (e as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
    if (Array.isArray(d)) return d.map((x: { msg?: string }) => x?.msg ?? String(x)).join('; ');
    return d ? String(d) : 'Save failed';
}

export default function ArtifactTypesSection() {
    const qc = useQueryClient();
    const { custom } = useArtifactTypes();
    const [draft, setDraft] = useState<Draft | null>(null);
    const [error, setError] = useState<string | null>(null);
    const done = () => { qc.invalidateQueries({ queryKey: ['artifact-types'] }); setDraft(null); setError(null); };
    const fail = (e: unknown) => setError(errorText(e));

    const save = useMutation({
        mutationFn: async (d: Draft) => {
            const body = { label: d.label, payload_key: d.payload_key, show_in_mindmap: d.show_in_mindmap, private: d.private };
            return d.id ? api.put(`/artifact-types/${d.id}`, body) : api.post('/artifact-types/', { ...body, key: d.key });
        },
        onSuccess: done, onError: fail,
    });
    const remove = useMutation({ mutationFn: (id: number) => api.delete(`/artifact-types/${id}`), onSuccess: done, onError: fail });
    const edit = (d: ArtifactTypeDef) => { setError(null); setDraft({ ...d, payload_key: d.payload_key ?? '' }); };

    return (
        <section className="bg-white border border-zinc-200">
            <div className="flex items-center justify-between px-4 h-11 border-b border-zinc-200">
                <h2 className="flex items-center gap-2 text-sm font-semibold text-zinc-900">
                    <Tags size={15} className="text-accent-600" /> Artifact types
                </h2>
                {!draft && (
                    <button type="button" className={btnSecondary} onClick={() => { setError(null); setDraft(EMPTY); }}>
                        <Plus size={14} /> Add type
                    </button>
                )}
            </div>
            <div className="p-4 space-y-3">
                <p className="text-xs text-zinc-500">
                    Custom types appear next to the built-in ones. With an alert payload key, promoting an alert fills them automatically.
                    Workflows read them as <code className="font-mono">{'{{ case.attributes.<key> }}'}</code>. Private types are only visible to workflows; making an existing type private hides it from now on but doesn't remove timeline entries already written.
                </p>
                {custom.length > 0 && (
                    <div className="overflow-x-auto">
                        <table className="w-full text-sm">
                            <thead>
                                <tr className="label-mono text-left text-zinc-400">
                                    <th className="py-1 pr-3 font-normal">key</th><th className="pr-3 font-normal">label</th>
                                    <th className="pr-3 font-normal">payload key</th><th className="pr-3 font-normal">mindmap</th>
                                    <th className="pr-3 font-normal">private</th><th />
                                </tr>
                            </thead>
                            <tbody>
                                {custom.map((d) => (
                                    <tr key={d.id} className="border-t border-zinc-100">
                                        <td className="py-1.5 pr-3 font-mono">{d.key}</td>
                                        <td className="pr-3">{d.label}</td>
                                        <td className="pr-3 font-mono text-zinc-500">{d.payload_key ?? '—'}</td>
                                        <td className="pr-3">{d.private ? '—' : d.show_in_mindmap ? 'yes' : 'no'}</td>
                                        <td className="pr-3">{d.private ? 'yes' : 'no'}</td>
                                        <td className="text-right whitespace-nowrap">
                                            <button type="button" aria-label={`Edit ${d.key}`} onClick={() => edit(d)}
                                                className="p-1 text-zinc-500 hover:text-zinc-900"><Pencil size={14} /></button>
                                            <button type="button" aria-label={`Delete ${d.key}`} disabled={remove.isPending}
                                                onClick={() => remove.mutate(d.id)}
                                                className="p-1 text-zinc-500 hover:text-red-700"><Trash2 size={14} /></button>
                                        </td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                )}
                {draft && (
                    <form className="grid grid-cols-1 sm:grid-cols-2 gap-3 border border-zinc-200 p-3"
                        onSubmit={(e) => { e.preventDefault(); save.mutate(draft); }}>
                        <label className="text-xs text-zinc-600">Key
                            <input className={`${input} font-mono`} value={draft.key} disabled={!!draft.id} required
                                pattern="[a-z][a-z0-9_]{0,31}" title="lowercase letters, digits and _; starts with a letter"
                                placeholder="username" onChange={(e) => setDraft({ ...draft, key: e.target.value })} />
                        </label>
                        <label className="text-xs text-zinc-600">Label
                            <input className={input} value={draft.label} required maxLength={64} placeholder="Username"
                                onChange={(e) => setDraft({ ...draft, label: e.target.value })} />
                        </label>
                        <label className="text-xs text-zinc-600 sm:col-span-2">Alert payload key (optional)
                            <input className={`${input} font-mono`} value={draft.payload_key} maxLength={128}
                                placeholder="user_name or user.email"
                                onChange={(e) => setDraft({ ...draft, payload_key: e.target.value })} />
                        </label>
                        <label className="flex items-center gap-2 text-sm text-zinc-800">
                            <input type="checkbox" checked={draft.show_in_mindmap && !draft.private} disabled={draft.private}
                                onChange={(e) => setDraft({ ...draft, show_in_mindmap: e.target.checked })}
                                className="border-zinc-300 text-accent-600 focus:ring-accent-500" />
                            Show in mindmap
                        </label>
                        <label className="flex items-center gap-2 text-sm text-zinc-800">
                            <input type="checkbox" checked={draft.private}
                                onChange={(e) => setDraft({ ...draft, private: e.target.checked })}
                                className="border-zinc-300 text-accent-600 focus:ring-accent-500" />
                            Private (workflows only)
                        </label>
                        <div className="sm:col-span-2 flex gap-2">
                            <button type="submit" className={btnPrimary} disabled={save.isPending}>
                                {save.isPending && <Loader2 size={14} className="animate-spin" />} Save
                            </button>
                            <button type="button" className={btnSecondary} onClick={() => { setDraft(null); setError(null); }}>Cancel</button>
                        </div>
                    </form>
                )}
                {error && <p role="alert" className="text-xs text-red-700">{error}</p>}
            </div>
        </section>
    );
}
