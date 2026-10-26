import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Pencil, Trash2, X, Server, User as UserIcon, Globe, Network, Box } from 'lucide-react';
import { api } from '../../api/client';
import { cn } from '../../lib/utils';
import type { CaseAsset, User } from '../../types';

const TYPES: [string, string, typeof Server][] = [
    ['host', 'Host', Server], ['account', 'Account', UserIcon], ['ip', 'IP', Network],
    ['domain', 'Domain', Globe], ['service', 'Service', Box], ['other', 'Other', Box],
];
const STATUSES: [string, string, string][] = [
    ['unknown', 'Unknown', 'bg-raised text-muted border-line'],
    ['compromised', 'Compromised', 'bg-red-500/15 text-red-400 border-red-500/30'],
    ['not_compromised', 'Not compromised', 'bg-emerald-500/15 text-emerald-400 border-emerald-500/30'],
];

type AssetDraft = { name: string; asset_type: string; ip: string; domain: string; description: string; compromise_status: string; tags: string };
const blankAsset = (): AssetDraft => ({ name: '', asset_type: 'host', ip: '', domain: '', description: '', compromise_status: 'unknown', tags: '' });
const fromAsset = (a: CaseAsset): AssetDraft => ({ name: a.name, asset_type: a.asset_type, ip: a.ip ?? '', domain: a.domain ?? '', description: a.description ?? '', compromise_status: a.compromise_status, tags: a.tags.join(', ') });
const assetPayload = (d: AssetDraft) => ({
    ...d, ip: d.ip || null, domain: d.domain || null, description: d.description || null,
    tags: d.tags.split(',').map((t) => t.trim()).filter(Boolean),
});

const field = 'w-full bg-panel border border-line rounded-lg px-3 py-2 text-sm text-fg focus:outline-hidden focus:ring-1 focus:ring-white/50';

/** The asset form fields, shared by AddAssetForm and the inline edit. */
function AssetFields({ draft, setDraft }: { draft: AssetDraft; setDraft: (d: AssetDraft) => void }) {
    return (
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <input value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} placeholder="Name (hostname, account…)" className={field} aria-label="Name" />
            <select value={draft.asset_type} onChange={(e) => setDraft({ ...draft, asset_type: e.target.value })} className={field} aria-label="Type">
                {TYPES.map(([k, label]) => <option key={k} value={k}>{label}</option>)}
            </select>
            <input value={draft.ip} onChange={(e) => setDraft({ ...draft, ip: e.target.value })} placeholder="IP" className={field} aria-label="IP" />
            <input value={draft.domain} onChange={(e) => setDraft({ ...draft, domain: e.target.value })} placeholder="Domain" className={field} aria-label="Domain" />
            <select value={draft.compromise_status} onChange={(e) => setDraft({ ...draft, compromise_status: e.target.value })} className={field} aria-label="Compromise status">
                {STATUSES.map(([k, label]) => <option key={k} value={k}>{label}</option>)}
            </select>
            <input value={draft.tags} onChange={(e) => setDraft({ ...draft, tags: e.target.value })} placeholder="Tags, comma separated" className={field} aria-label="Tags" />
            <textarea value={draft.description} onChange={(e) => setDraft({ ...draft, description: e.target.value })} placeholder="Description" rows={2} className={cn(field, 'sm:col-span-2 resize-none')} aria-label="Description" />
        </div>
    );
}

/** New asset form for the case's add dialog. */
export function AddAssetForm({ caseId, onDone }: { caseId: number; onDone: () => void }) {
    const qc = useQueryClient();
    const [draft, setDraft] = useState<AssetDraft>(blankAsset);
    const add = useMutation({
        mutationFn: async () => api.post(`/cases/${caseId}/assets`, assetPayload(draft)),
        onSuccess: () => { qc.invalidateQueries({ queryKey: ['case-assets', caseId] }); onDone(); },
    });
    return (
        <div className="space-y-3">
            <AssetFields draft={draft} setDraft={setDraft} />
            {add.isError && <p role="alert" className="text-xs text-severity-critical">Could not add the asset.</p>}
            <div className="flex justify-end gap-2">
                <button onClick={onDone} className="px-3 py-1.5 text-xs text-fg-3 hover:text-fg">Cancel</button>
                <button onClick={() => add.mutate()} disabled={!draft.name.trim() || add.isPending}
                    className="px-3 py-1.5 text-xs bg-accent text-on-accent rounded-sm font-medium hover:bg-accent-hover disabled:opacity-50">
                    {add.isPending ? 'Adding…' : 'Add asset'}
                </button>
            </div>
        </div>
    );
}

// ponytail: no event<->asset links and no report section; add case_event_assets when the timeline needs asset lanes.
export default function CaseAssets({ caseId }: { caseId: number }) {
    const qc = useQueryClient();
    const [editing, setEditing] = useState<number | null>(null);
    const [draft, setDraft] = useState<AssetDraft>(blankAsset);

    const { data: me } = useQuery({ queryKey: ['currentUser'], queryFn: async () => (await api.get('/users/me')).data as User, staleTime: 300_000 });
    const canEdit = me?.role === 'admin' || me?.role === 'analyst' || me?.is_super_admin;
    const key = ['case-assets', caseId];
    const { data: assets = [], isLoading } = useQuery({ queryKey: key, queryFn: async () => (await api.get(`/cases/${caseId}/assets`)).data as CaseAsset[] });

    const invalidate = () => { qc.invalidateQueries({ queryKey: key }); };
    const save = useMutation({
        mutationFn: async () => api.put(`/cases/${caseId}/assets/${editing}`, assetPayload(draft)),
        onSuccess: () => { setEditing(null); invalidate(); },
    });
    const remove = useMutation({ mutationFn: async (id: number) => api.delete(`/cases/${caseId}/assets/${id}`), onSuccess: invalidate });

    if (isLoading) return <p className="font-mono text-xs text-muted">$ loading assets…</p>;

    const form = (
        <div className="glass-panel p-4 rounded-xl border border-line bg-panel space-y-3">
            <div className="flex items-center justify-between">
                <h4 className="text-sm font-semibold text-fg">Edit asset</h4>
                <button onClick={() => setEditing(null)} className="text-muted hover:text-fg" aria-label="Close"><X size={16} /></button>
            </div>
            <AssetFields draft={draft} setDraft={setDraft} />
            <div className="flex justify-end gap-2">
                <button onClick={() => setEditing(null)} className="px-3 py-1.5 text-xs text-fg-3 hover:text-fg">Cancel</button>
                <button onClick={() => save.mutate()} disabled={!draft.name.trim() || save.isPending}
                    className="px-3 py-1.5 text-xs bg-accent text-on-accent rounded-sm font-medium hover:bg-accent-hover disabled:opacity-50">
                    {save.isPending ? 'Saving…' : 'Save'}
                </button>
            </div>
            {save.isError && <p role="alert" className="text-xs text-severity-critical">Could not save the asset.</p>}
        </div>
    );

    return (
        <div className="space-y-3">
            <h3 className="text-sm font-semibold text-fg-2">Assets <span className="font-mono text-xs text-muted">{assets.length}</span></h3>
            {!assets.length && (
                <p className="text-sm text-muted">No assets yet. Add the hosts and accounts the incident touched, and whether they are compromised.</p>
            )}
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                {assets.map((a) => {
                    if (editing === a.id) return <div key={a.id} className="md:col-span-2">{form}</div>;
                    const Icon = (TYPES.find(([k]) => k === a.asset_type) ?? TYPES[5])[2];
                    const st = STATUSES.find(([k]) => k === a.compromise_status) ?? STATUSES[0];
                    return (
                        <article key={a.id} className="group rounded-lg border border-line bg-panel p-3">
                            <div className="flex items-center gap-2">
                                <Icon size={16} className="text-fg-3 shrink-0" />
                                <span className="text-sm font-medium text-fg truncate">{a.name}</span>
                                <span className={cn('text-[10px] font-medium px-1.5 py-0.5 rounded-sm border', st[2])}>{st[1]}</span>
                                {canEdit && (
                                    <span className="ml-auto flex items-center gap-2 opacity-0 group-hover:opacity-100 focus-within:opacity-100 transition-opacity">
                                        <button onClick={() => { setDraft(fromAsset(a)); setEditing(a.id); }} title="Edit" className="text-muted hover:text-fg"><Pencil size={13} /></button>
                                        <button onClick={() => { if (confirm('Delete this asset?')) remove.mutate(a.id); }} title="Delete" className="text-muted hover:text-severity-critical"><Trash2 size={13} /></button>
                                    </span>
                                )}
                            </div>
                            {(a.ip || a.domain) && <p className="mt-1 text-xs font-mono text-fg-3">{[a.ip, a.domain].filter(Boolean).join(' · ')}</p>}
                            {a.description && <p className="mt-1 text-sm text-fg-2 whitespace-pre-wrap">{a.description}</p>}
                            {a.tags.length > 0 && (
                                <div className="mt-2 flex flex-wrap gap-1.5">
                                    {a.tags.map((t) => <span key={t} className="px-2 py-0.5 rounded-full border border-line bg-raised text-fg-3 text-[11px]">#{t}</span>)}
                                </div>
                            )}
                        </article>
                    );
                })}
            </div>
        </div>
    );
}
