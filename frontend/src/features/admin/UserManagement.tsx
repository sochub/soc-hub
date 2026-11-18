import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { UserPlus, Shield, ShieldCheck, Eye, ChevronDown } from 'lucide-react';
import { api } from '../../api/client';
import type { User } from '../../types';
import InviteUserModal from './InviteUserModal';
import Modal from '../../components/layout/Modal';
import { Badge, Button, ErrorState, FolderTabs, Select, Skeleton, Stamp } from '../../components/ui';
import SettingsPageHeader from '../settings/SettingsPageHeader';
import Avatar from '../../components/Avatar';
import GroupsSection from '../escalation/GroupsSection';
import { mfaErrorMessage } from '../profile/mfaErrors';

const roleBadgeColors: Record<string, string> = {
    admin: 'bg-accent/10 text-accent border-accent/30',
    analyst: 'bg-raised text-fg-2 border-line-strong',
    viewer: 'bg-raised text-fg-3 border-line',
    super_admin: 'bg-severity-medium/10 text-severity-medium border-severity-medium/30',
};

const ROLE_LABEL: Record<string, string> = { admin: 'Admin', analyst: 'Analyst', viewer: 'Viewer', super_admin: 'Super admin' };

const roleIcons: Record<string, typeof Shield> = {
    admin: ShieldCheck,
    analyst: Shield,
    viewer: Eye,
    super_admin: ShieldCheck,
};

export default function UserManagement() {
    const [showInviteModal, setShowInviteModal] = useState(false);
    const [editingRoleUser, setEditingRoleUser] = useState<number | null>(null);
    const [resetTarget, setResetTarget] = useState<User | null>(null);
    const [tab, setTab] = useState<'members' | 'groups'>('members');
    const [notice, setNotice] = useState<{ ok: boolean; text: string } | null>(null);
    const queryClient = useQueryClient();

    const { data: me } = useQuery({
        queryKey: ['currentUser'],
        queryFn: async () => (await api.get('/users/me')).data as User,
        staleTime: 5 * 60 * 1000,
    });

    const { data: users = [], isLoading, isError, refetch } = useQuery({
        queryKey: ['users'],
        queryFn: async () => {
            const response = await api.get('/users/');
            return response.data as User[];
        },
    });

    const roleMutation = useMutation({
        mutationFn: async ({ userId, role }: { userId: number; role: string }) => {
            await api.put(`/users/${userId}/role`, { role });
        },
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['users'] });
            setEditingRoleUser(null);
        },
    });

    const toggleActiveMutation = useMutation({
        mutationFn: async ({ userId, activate }: { userId: number; activate: boolean }) => {
            await api.put(`/users/${userId}/${activate ? 'activate' : 'deactivate'}`);
        },
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['users'] });
        },
    });

    const resetMfa = useMutation({
        mutationFn: async (u: User) => { await api.post(`/users/${u.id}/mfa/reset`); },
        onSuccess: (_d, u) => {
            queryClient.invalidateQueries({ queryKey: ['users'] });
            setNotice({ ok: true, text: `Two-factor authentication reset for ${u.full_name || u.email}.` });
            setResetTarget(null);
        },
        onError: (err) => {
            setNotice({ ok: false, text: mfaErrorMessage(err, 'Could not reset two-factor authentication.') });
            setResetTarget(null);
        },
    });

    const toggle = (
        <FolderTabs label="Users view" idPrefix="users-view" value={tab} onChange={setTab}
            items={[{ value: 'members', label: 'Members', count: users.length || undefined }, { value: 'groups', label: 'Groups' }]} />
    );

    if (tab === 'groups') {
        return (
            <div className="space-y-6">
                <SettingsPageHeader title="Users" description="Manage users in your organization." />
                {toggle}
                <div id="users-view-panel" role="tabpanel" aria-labelledby="users-view-groups"><GroupsSection /></div>
            </div>
        );
    }

    return (
        <div className="space-y-6">
            <SettingsPageHeader title="Users" description="Manage users in your organization."
                actions={<Button onClick={() => setShowInviteModal(true)}><UserPlus size={16} />Invite user</Button>} />
            {toggle}

            <div id="users-view-panel" role="tabpanel" aria-labelledby="users-view-members" className="space-y-4">
            {notice && (
                <div role={notice.ok ? 'status' : 'alert'}
                    className={`flex items-center justify-between gap-3 rounded-md px-4 py-2.5 text-sm border ${notice.ok ? 'bg-raised border-line text-fg-2' : 'bg-severity-critical/5 border-severity-critical/30 text-severity-critical'}`}>
                    <span>{notice.text}</span>
                    <Button variant="ghost" size="sm" onClick={() => setNotice(null)}>Dismiss</Button>
                </div>
            )}

            {isError ? <ErrorState message="Could not load users." onRetry={() => refetch()} /> : (
            <div className="bg-panel border border-line rounded-md overflow-x-auto">
                <table className="w-full text-sm">
                    <thead>
                        <tr className="border-b border-line label-caps text-left">
                            <th className="px-3 py-2">User</th>
                            <th className="px-3 py-2">Role</th>
                            <th className="px-3 py-2">Status</th>
                            <th className="px-3 py-2">MFA</th>
                            <th className="px-3 py-2 text-right">Actions</th>
                        </tr>
                    </thead>
                    <tbody className="divide-y divide-line">
                        {isLoading ? (
                            <tr><td colSpan={5} className="p-3"><Skeleton rows={4} /></td></tr>
                        ) : users.map(user => {
                            const role = user.role ?? 'viewer';
                            const RoleIcon = roleIcons[role] || Shield;
                            // Server-enforced too (account_guard): account-wide actions only for accounts this
                            // admin owns; a super admin's membership is managed by super admins only.
                            const manageable = user.account_manageable !== false;
                            const roleEditable = !user.is_super_admin || !!me?.is_super_admin;
                            return (
                                <tr key={user.id} className="hover:bg-raised transition-colors">
                                    <td className="px-3 py-2.5">
                                        <div className="flex items-center gap-3">
                                            <Avatar userId={user.id} name={user.full_name || user.email} hasAvatar={user.has_avatar} version={user.avatar_version} size={28} />
                                            <div className="min-w-0">
                                                <p className="font-medium text-fg truncate">{user.full_name || 'Unnamed'}</p>
                                                <p className="text-xs text-fg-3 truncate">{user.email}</p>
                                            </div>
                                        </div>
                                    </td>
                                    <td className="px-3 py-2.5">
                                        {editingRoleUser === user.id && roleEditable ? (
                                            <Select inputSize="sm" className="w-28" aria-label={`Role for ${user.email}`}
                                                defaultValue={role}
                                                onChange={e => roleMutation.mutate({ userId: user.id, role: e.target.value })}
                                                onBlur={() => setEditingRoleUser(null)}
                                                autoFocus
                                            >
                                                <option value="analyst">Analyst</option>
                                                <option value="admin">Admin</option>
                                                <option value="viewer">Viewer</option>
                                            </Select>
                                        ) : (
                                            <button
                                                onClick={() => roleEditable && setEditingRoleUser(user.id)}
                                                disabled={!roleEditable}
                                                aria-label={roleEditable ? `Role: ${ROLE_LABEL[role] ?? role}. Change role` : undefined}
                                                className={`inline-flex items-center gap-1.5 rounded-sm border px-1.5 py-0.5 text-xs font-medium ${roleBadgeColors[role] || roleBadgeColors.viewer} ${roleEditable ? 'hover:bg-raised-2' : ''}`}
                                            >
                                                <RoleIcon size={12} aria-hidden />
                                                {ROLE_LABEL[role] ?? role}
                                                {roleEditable && <ChevronDown size={10} className="opacity-50" aria-hidden />}
                                            </button>
                                        )}
                                    </td>
                                    <td className="px-3 py-2.5">
                                        <Stamp tone={user.is_active ? 'ok' : 'critical'}>{user.is_active ? 'Active' : 'Inactive'}</Stamp>
                                    </td>
                                    <td className="px-3 py-2.5">
                                        <Badge variant={user.mfa_enabled ? 'low' : 'default'}>MFA {user.mfa_enabled ? 'on' : 'off'}</Badge>
                                    </td>
                                    <td className="px-3 py-2.5 text-right whitespace-nowrap">
                                        <span className="inline-flex items-center gap-1">
                                        {me && manageable && user.mfa_enabled && user.id !== me.id && (!user.is_super_admin || me.is_super_admin) && (
                                            <Button variant="ghost" size="sm" className="text-severity-critical" onClick={() => setResetTarget(user)}>
                                                Reset MFA
                                            </Button>
                                        )}
                                        {manageable ? (
                                            <Button variant="ghost" size="sm"
                                                onClick={() => toggleActiveMutation.mutate({ userId: user.id, activate: !user.is_active })}>
                                                {user.is_active ? 'Deactivate' : 'Activate'}
                                            </Button>
                                        ) : (
                                            <span className="text-xs text-fg-3" title="This account belongs to other organizations or is a platform admin; ask a platform admin.">
                                                Managed by platform admin
                                            </span>
                                        )}
                                        </span>
                                    </td>
                                </tr>
                            );
                        })}
                    </tbody>
                </table>
            </div>
            )}
            </div>

            <Modal
                open={!!resetTarget}
                onClose={() => setResetTarget(null)}
                title="Reset two-factor authentication"
                size="md"
                footer={<>
                    <Button variant="secondary" onClick={() => setResetTarget(null)} disabled={resetMfa.isPending}>Cancel</Button>
                    <Button variant="danger" onClick={() => resetTarget && resetMfa.mutate(resetTarget)} disabled={resetMfa.isPending}>
                        {resetMfa.isPending ? 'Resetting…' : 'Reset MFA'}
                    </Button>
                </>}
            >
                <p className="text-sm text-fg-2">
                    Remove two-factor authentication for <strong>{resetTarget?.full_name || resetTarget?.email}</strong>?
                    Their active sessions will be signed out, and they will need to set it up again
                    {' '}if your organization requires it.
                </p>
            </Modal>

            <InviteUserModal isOpen={showInviteModal} onClose={() => setShowInviteModal(false)} />
        </div>
    );
}
