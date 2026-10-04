import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { UserPlus, Shield, ShieldCheck, Eye, ChevronDown } from 'lucide-react';
import { api } from '../../api/client';
import type { User } from '../../types';
import InviteUserModal from './InviteUserModal';
import PageContainer from '../../components/layout/PageContainer';
import Modal, { btnDanger, btnSecondary } from '../../components/layout/Modal';
import Avatar from '../../components/Avatar';
import { mfaErrorMessage } from '../profile/mfaErrors';

const roleBadgeColors: Record<string, string> = {
    admin: 'bg-purple-50 text-purple-700 border-purple-200',
    analyst: 'bg-accent-500/20 text-accent-600 border-blue-200',
    viewer: 'bg-zinc-200 text-zinc-500 border-zinc-300',
    super_admin: 'bg-amber-50 text-amber-700 border-amber-200',
};

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
    const [notice, setNotice] = useState<{ ok: boolean; text: string } | null>(null);
    const queryClient = useQueryClient();

    const { data: me } = useQuery({
        queryKey: ['currentUser'],
        queryFn: async () => (await api.get('/users/me')).data as User,
        staleTime: 5 * 60 * 1000,
    });

    const { data: users = [], isLoading } = useQuery({
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

    return (
        <PageContainer>
            <div className="flex items-center justify-between">
                <div>
                    <h1 className="text-2xl font-bold text-zinc-900">User Management</h1>
                    <p className="text-sm text-zinc-500 mt-1">Manage users in your organization</p>
                </div>
                <button
                    onClick={() => setShowInviteModal(true)}
                    className="flex items-center gap-2 px-4 py-2 bg-accent-600 hover:bg-accent-700 text-white rounded-lg text-sm font-medium transition-colors"
                >
                    <UserPlus size={16} />
                    Invite User
                </button>
            </div>

            {notice && (
                <div role={notice.ok ? 'status' : 'alert'}
                    className={`flex items-center justify-between gap-3 px-4 py-2.5 text-sm border ${notice.ok ? 'bg-emerald-50 border-emerald-200 text-emerald-800' : 'bg-red-50 border-red-200 text-red-700'}`}>
                    <span>{notice.text}</span>
                    <button onClick={() => setNotice(null)} className="text-xs underline">Dismiss</button>
                </div>
            )}

            <div className="bg-white border border-zinc-200 rounded-xl overflow-hidden">
                <table className="w-full">
                    <thead>
                        <tr className="border-b border-zinc-200">
                            <th className="text-left px-6 py-3 text-xs font-semibold text-zinc-400 uppercase tracking-wider">User</th>
                            <th className="text-left px-6 py-3 text-xs font-semibold text-zinc-400 uppercase tracking-wider">Role</th>
                            <th className="text-left px-6 py-3 text-xs font-semibold text-zinc-400 uppercase tracking-wider">Status</th>
                            <th className="text-left px-6 py-3 text-xs font-semibold text-zinc-400 uppercase tracking-wider">MFA</th>
                            <th className="text-right px-6 py-3 text-xs font-semibold text-zinc-400 uppercase tracking-wider">Actions</th>
                        </tr>
                    </thead>
                    <tbody className="divide-y divide-zinc-200">
                        {isLoading ? (
                            <tr>
                                <td colSpan={5} className="px-6 py-8 text-center text-zinc-400">
                                    <div className="animate-spin rounded-full h-6 w-6 border-t-2 border-b-2 border-blue-500 mx-auto" />
                                </td>
                            </tr>
                        ) : users.map(user => {
                            const role = user.role ?? 'viewer';
                            const RoleIcon = roleIcons[role] || Shield;
                            return (
                                <tr key={user.id} className="hover:bg-zinc-100 transition-colors">
                                    <td className="px-6 py-4">
                                        <div className="flex items-center gap-3">
                                            <Avatar userId={user.id} name={user.full_name || user.email} hasAvatar={user.has_avatar} version={user.avatar_version} size={32} />
                                            <div>
                                                <p className="text-sm font-medium text-zinc-800">{user.full_name || 'Unnamed'}</p>
                                                <p className="text-xs text-zinc-400">{user.email}</p>
                                            </div>
                                        </div>
                                    </td>
                                    <td className="px-6 py-4">
                                        {editingRoleUser === user.id ? (
                                            <select
                                                className="bg-white border border-zinc-200 rounded px-2 py-1 text-xs text-zinc-800"
                                                defaultValue={role}
                                                onChange={e => roleMutation.mutate({ userId: user.id, role: e.target.value })}
                                                onBlur={() => setEditingRoleUser(null)}
                                                autoFocus
                                            >
                                                <option value="analyst">Analyst</option>
                                                <option value="admin">Admin</option>
                                                <option value="viewer">Viewer</option>
                                            </select>
                                        ) : (
                                            <button
                                                onClick={() => setEditingRoleUser(user.id)}
                                                className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium border ${roleBadgeColors[role] || roleBadgeColors.viewer}`}
                                            >
                                                <RoleIcon size={12} />
                                                {role}
                                                <ChevronDown size={10} className="opacity-50" />
                                            </button>
                                        )}
                                    </td>
                                    <td className="px-6 py-4">
                                        <span className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium ${
                                            user.is_active
                                                ? 'bg-green-50 text-green-700 border border-green-200'
                                                : 'bg-red-50 text-red-700 border border-red-200'
                                        }`}>
                                            <span className={`w-1.5 h-1.5 rounded-full ${user.is_active ? 'bg-green-400' : 'bg-red-400'}`} />
                                            {user.is_active ? 'Active' : 'Inactive'}
                                        </span>
                                    </td>
                                    <td className="px-6 py-4">
                                        <span className={`inline-flex items-center px-2 py-0.5 text-xs font-medium border ${
                                            user.mfa_enabled
                                                ? 'bg-green-50 text-green-700 border-green-200'
                                                : 'bg-zinc-100 text-zinc-500 border-zinc-200'
                                        }`}>
                                            MFA {user.mfa_enabled ? 'on' : 'off'}
                                        </span>
                                    </td>
                                    <td className="px-6 py-4 text-right space-x-4">
                                        {me && user.mfa_enabled && user.id !== me.id && (!user.is_super_admin || me.is_super_admin) && (
                                            <button
                                                onClick={() => setResetTarget(user)}
                                                className="text-xs text-red-600 hover:text-red-800 transition-colors"
                                            >
                                                Reset MFA
                                            </button>
                                        )}
                                        <button
                                            onClick={() => toggleActiveMutation.mutate({ userId: user.id, activate: !user.is_active })}
                                            className="text-xs text-zinc-500 hover:text-zinc-800 transition-colors"
                                        >
                                            {user.is_active ? 'Deactivate' : 'Activate'}
                                        </button>
                                    </td>
                                </tr>
                            );
                        })}
                    </tbody>
                </table>
            </div>

            <Modal
                open={!!resetTarget}
                onClose={() => setResetTarget(null)}
                title="Reset two-factor authentication"
                size="md"
                footer={<>
                    <button className={btnSecondary} onClick={() => setResetTarget(null)} disabled={resetMfa.isPending}>Cancel</button>
                    <button className={btnDanger} onClick={() => resetTarget && resetMfa.mutate(resetTarget)} disabled={resetMfa.isPending}>
                        {resetMfa.isPending ? 'Resetting…' : 'Reset MFA'}
                    </button>
                </>}
            >
                <p className="text-sm text-zinc-700">
                    Remove two-factor authentication for <strong>{resetTarget?.full_name || resetTarget?.email}</strong>?
                    Their active sessions will be signed out, and they will need to set it up again
                    {' '}if your organization requires it.
                </p>
            </Modal>

            <InviteUserModal isOpen={showInviteModal} onClose={() => setShowInviteModal(false)} />
        </PageContainer>
    );
}
