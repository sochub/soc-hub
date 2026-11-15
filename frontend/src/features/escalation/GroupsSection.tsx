import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Plus, Pencil, Trash2 } from 'lucide-react';
import { api } from '../../api/client';
import type { EscalationGroup, User } from '../../types';
import { Button, Input, Label } from '../../components/ui';
import Modal from '../../components/layout/Modal';
import CheckList from './CheckList';
import { errMsg, useDeleteGroup, useGroups, usePolicy, useSaveGroup } from './api';

export default function GroupsSection() {
    const { data: groups = [], isLoading } = useGroups();
    const { data: policy } = usePolicy();
    const { data: users = [] } = useQuery({
        queryKey: ['users'],
        queryFn: async () => (await api.get('/users/')).data as User[],
    });
    const save = useSaveGroup();
    const del = useDeleteGroup();
    const [editing, setEditing] = useState<EscalationGroup | 'new' | null>(null);
    const [name, setName] = useState('');
    const [description, setDescription] = useState('');
    const [members, setMembers] = useState<number[]>([]);
    const [idpGroups, setIdpGroups] = useState('');
    const [error, setError] = useState<string | null>(null);
    const [listError, setListError] = useState<string | null>(null);

    const tierName = (id: number) => policy?.tiers.find((t) => t.id === id)?.name ?? `Tier ${id}`;

    const open = (g: EscalationGroup | 'new') => {
        setEditing(g); setError(null);
        setName(g === 'new' ? '' : g.name);
        setDescription(g === 'new' ? '' : g.description ?? '');
        setMembers(g === 'new' ? [] : g.members.map((m) => m.user_id));
        setIdpGroups(g === 'new' ? '' : (g.idp_groups ?? []).join('\n'));
    };
    const submit = () => {
        if (!editing) return;
        save.mutate(
            { id: editing === 'new' ? undefined : editing.id, body: { name, description: description.trim() || null, member_user_ids: members, idp_groups: idpGroups.split(/[\n,]+/).map((s) => s.trim()).filter(Boolean) } },
            { onSuccess: () => setEditing(null), onError: (e) => setError(errMsg(e, 'Could not save the group.')) },
        );
    };
    const remove = (g: EscalationGroup) => {
        if (!window.confirm(`Delete group "${g.name}"?`)) return;
        setListError(null);
        del.mutate(g.id, { onError: (e) => setListError(errMsg(e, 'Could not delete the group.')) });
    };

    return (
        <div className="space-y-4">
            <div className="flex items-center justify-between">
                <div>
                    <h1 className="text-2xl font-bold text-fg">Groups</h1>
                    <p className="text-sm text-fg-3 mt-1">Responder groups that cases can be escalated to.</p>
                </div>
                <Button onClick={() => open('new')}><Plus size={16} />New group</Button>
            </div>
            {listError && <p role="alert" className="text-sm text-severity-critical">{listError}</p>}
            <div className="bg-panel border border-line rounded-xl overflow-hidden">
                <table className="w-full">
                    <thead>
                        <tr className="border-b border-line">
                            <th className="text-left px-6 py-3 text-xs font-semibold text-muted uppercase tracking-wider">Name</th>
                            <th className="text-left px-6 py-3 text-xs font-semibold text-muted uppercase tracking-wider">Members</th>
                            <th className="text-left px-6 py-3 text-xs font-semibold text-muted uppercase tracking-wider">Used in tiers</th>
                            <th className="text-right px-6 py-3 text-xs font-semibold text-muted uppercase tracking-wider">Actions</th>
                        </tr>
                    </thead>
                    <tbody className="divide-y divide-line">
                        {isLoading ? (
                            <tr><td colSpan={4} className="px-6 py-8 text-center text-muted">Loading…</td></tr>
                        ) : groups.length === 0 ? (
                            <tr><td colSpan={4} className="px-6 py-8 text-center text-fg-3">No groups yet.</td></tr>
                        ) : groups.map((g) => (
                            <tr key={g.id}>
                                <td className="px-6 py-3 text-sm text-fg font-medium">{g.name}
                                    {g.description && <div className="text-xs text-fg-3 font-normal">{g.description}</div>}
                                </td>
                                <td className="px-6 py-3 text-sm text-fg-2">{g.members.length}</td>
                                <td className="px-6 py-3 text-sm text-fg-2">{g.tier_ids.length ? g.tier_ids.map(tierName).join(', ') : '—'}</td>
                                <td className="px-6 py-3 text-right whitespace-nowrap">
                                    <Button variant="ghost" size="sm" aria-label={`Edit ${g.name}`} onClick={() => open(g)}><Pencil size={14} /></Button>
                                    <Button variant="ghost" size="sm" aria-label={`Delete ${g.name}`} onClick={() => remove(g)}><Trash2 size={14} /></Button>
                                </td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            </div>

            <Modal open={!!editing} onClose={() => setEditing(null)} size="md"
                title={editing === 'new' ? 'New group' : 'Edit group'}
                footer={<>
                    <Button variant="secondary" onClick={() => setEditing(null)}>Cancel</Button>
                    <Button onClick={submit} disabled={!name.trim() || save.isPending}>{save.isPending ? 'Saving…' : 'Save'}</Button>
                </>}>
                <div className="space-y-4">
                    <div className="space-y-1.5">
                        <Label htmlFor="grp-name">Name</Label>
                        <Input id="grp-name" value={name} maxLength={100} onChange={(e) => setName(e.target.value)} />
                    </div>
                    <div className="space-y-1.5">
                        <Label htmlFor="grp-desc">Description</Label>
                        <Input id="grp-desc" value={description} maxLength={500} onChange={(e) => setDescription(e.target.value)} />
                    </div>
                    <div className="space-y-1.5">
                        <Label>Members</Label>
                        {/* Viewers can't act on escalations; one already in the group stays listed so it can be unchecked. */}
                        <CheckList options={users.filter((u) => u.role !== 'viewer' || members.includes(u.id)).map((u) => ({ id: u.id, label: u.full_name ? `${u.full_name} (${u.email})` : u.email }))}
                            value={members} onChange={setMembers} empty="No users available." />
                    </div>
                    <div className="space-y-1.5">
                        <Label htmlFor="grp-idp">SSO groups</Label>
                        <textarea id="grp-idp" rows={3} value={idpGroups} onChange={(e) => setIdpGroups(e.target.value)}
                            placeholder="soc-tier2, 3f2a9c1e-…"
                            className="w-full bg-panel border border-line-strong px-2.5 py-2 text-sm num focus:outline-hidden focus:ring-1 focus:ring-accent" />
                        <p className="text-xs text-fg-3">
                            IdP group names or IDs, separated by commas or new lines. When set, membership is synced from the
                            identity provider at each SSO sign-in and replaces manual changes; analysts and admins only.
                        </p>
                    </div>
                    {error && <p role="alert" className="text-sm text-severity-critical">{error}</p>}
                </div>
            </Modal>
        </div>
    );
}
