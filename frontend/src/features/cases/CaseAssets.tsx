import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Pencil, Trash2, X, Server, User as UserIcon, Globe, Network, Box } from 'lucide-react';
import { api } from '../../api/client';
import { cn } from '../../lib/utils';
import { Button, Badge, Stamp, Skeleton, ErrorState, fieldClass, useConfirm, type Tone } from '../../components/ui';
import type { CaseAsset, User } from '../../types';

const TYPES: [string, string, typeof Server][] = [
    ['host', 'Host', Server], ['account', 'Account', UserIcon], ['ip', 'IP', Network],
    ['domain', 'Domain', Globe], ['service', 'Service', Box], ['other', 'Other', Box],
];
const STATUSES: [string, string, Tone][] = [
    ['unknown', 'Unknown', 'neutral'],
    ['compromised', 'Compromised', 'critical'],
    ['not_compromised', 'Not compromised', 'ok'],
];

type AssetDraft = { name: string; asset_type: string; ip: string; domain: string; description: string; compromise_status: string; tags: string };
const blankAsset = (): AssetDraft => ({ name: '', asset_type: 'host', ip: '', domain: '', description: '', compromise_status: 'unknown', tags: '' });
const fromAsset = (a: CaseAsset): AssetDraft => ({ name: a.name, asset_type: a.asset_type, ip: a.ip ?? '', domain: a.domain ?? '', description: a.description ?? '', compromise_status: a.compromise_status, tags: a.tags.join(', ') });
const assetPayload = (d: AssetDraft) => ({
    ...d, ip: d.ip || null, domain: d.domain || null, description: d.description || null,
    tags: d.tags.split(',').map((t) => t.trim()).filter(Boolean),
});

const field = fieldClass();

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
            <textarea value={draft.description} onChange={(e) => setDraft({ ...draft, description: e.target.value })} placeholder="Description" rows={2} className={cn(field, 'h-auto py-2 sm:col-span-2 resize-none')} aria-label="Description" />
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
                <Button variant="secondary" onClick={onDone}>Cancel</Button>
                <Button onClick={() => add.mutate()} disabled={!draft.name.trim() || add.isPending}>
                    {add.isPending ? 'Adding…' : 'Add asset'}
                </Button>
            </div>
        </div>
    );
}

// ponytail: no event<->asset links and no report section; add case_event_assets when the timeline needs asset lanes.
export default function CaseAssets({ caseId }: { caseId: number }) {
    const qc = useQueryClient();
    const [editing, setEditing] = useState<number | null>(null);
    const [draft, setDraft] = useState<AssetDraft>(blankAsset);
    const { confirm, dialog } = useConfirm();

    const { data: me } = useQuery({ queryKey: ['currentUser'], queryFn: async () => (await api.get('/users/me')).data as User, staleTime: 300_000 });
    const canEdit = me?.role === 'admin' || me?.role === 'analyst' || me?.is_super_admin;
    const key = ['case-assets', caseId];
    const { data: assets = [], isLoading, isError, refetch } = useQuery({ queryKey: key, queryFn: async () => (await api.get(`/cases/${caseId}/assets`)).data as CaseAsset[] });

    const invalidate = () => { qc.invalidateQueries({ queryKey: key }); };
    const save = useMutation({
        mutationFn: async () => api.put(`/cases/${caseId}/assets/${editing}`, assetPayload(draft)),
        onSuccess: () => { setEditing(null); invalidate(); },
    });
    const remove = useMutation({ mutationFn: async (id: number) => api.delete(`/cases/${caseId}/assets/${id}`), onSuccess: invalidate });

    if (isLoading) return <Skeleton rows={2} />;
    if (isError) return <ErrorState message="Could not load the assets." onRetry={() => refetch()} />;

    const form = (
        <div className="p-4 rounded-md border border-line bg-panel space-y-3">
            <div className="flex items-center justify-between">
                <h3 className="text-sm font-semibold text-fg">Edit asset</h3>
                <Button variant="ghost" size="icon" className="h-7 w-7" onClick={() => setEditing(null)} aria-label="Close"><X size={16} /></Button>
            </div>
            <AssetFields draft={draft} setDraft={setDraft} />
            <div className="flex justify-end gap-2">
                <Button variant="secondary" onClick={() => setEditing(null)}>Cancel</Button>
                <Button onClick={() => save.mutate()} disabled={!draft.name.trim() || save.isPending}>
                    {save.isPending ? 'Saving…' : 'Save'}
                </Button>
            </div>
            {save.isError && <p role="alert" className="text-xs text-severity-critical">Could not save the asset.</p>}
        </div>
    );

    return (
        <div className="space-y-3">
            {dialog}
            <h2 className="text-sm font-semibold text-fg">Assets <span className="num text-xs font-normal text-fg-3">{assets.length}</span></h2>
            {!assets.length && (
                <p className="text-sm text-fg-3">No assets yet. Add the hosts and accounts the incident touched, and whether they are compromised.</p>
            )}
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                {assets.map((a) => {
                    if (editing === a.id) return <div key={a.id} className="md:col-span-2">{form}</div>;
                    const Icon = (TYPES.find(([k]) => k === a.asset_type) ?? TYPES[5])[2];
                    const st = STATUSES.find(([k]) => k === a.compromise_status) ?? STATUSES[0];
                    return (
                        <article key={a.id} className="group rounded-md border border-line bg-panel p-3">
                            <div className="flex items-center gap-2">
                                <Icon size={16} className="text-fg-3 shrink-0" aria-hidden />
                                <span className="text-sm font-medium text-fg truncate">{a.name}</span>
                                <Stamp tone={st[2]}>{st[1]}</Stamp>
                                {canEdit && (
                                    <span className="ml-auto flex items-center gap-2 opacity-0 group-hover:opacity-100 group-focus-within:opacity-100 [@media(hover:none)]:opacity-100 transition-opacity">
                                        <button onClick={() => { setDraft(fromAsset(a)); setEditing(a.id); }} title="Edit" aria-label={`Edit ${a.name}`} className="p-1 rounded-sm text-fg-3 hover:text-fg hover:bg-raised"><Pencil size={13} /></button>
                                        <button onClick={async () => { if (await confirm({ title: `Delete asset ${a.name}?`, danger: true })) remove.mutate(a.id); }} title="Delete" aria-label={`Delete ${a.name}`} className="p-1 rounded-sm text-fg-3 hover:text-severity-critical hover:bg-raised"><Trash2 size={13} /></button>
                                    </span>
                                )}
                            </div>
                            {(a.ip || a.domain) && <p className="mt-1 text-xs font-mono text-fg-3">{[a.ip, a.domain].filter(Boolean).join(' · ')}</p>}
                            {a.description && <p className="mt-1 text-sm text-fg-2 whitespace-pre-wrap">{a.description}</p>}
                            {a.tags.length > 0 && (
                                <div className="mt-2 flex flex-wrap gap-1.5">
                                    {a.tags.map((t) => <Badge key={t}>#{t}</Badge>)}
                                </div>
                            )}
                        </article>
                    );
                })}
            </div>
        </div>
    );
}
