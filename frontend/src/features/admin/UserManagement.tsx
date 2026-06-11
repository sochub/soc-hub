import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { UserPlus, Shield, ShieldCheck, Eye, ChevronDown } from 'lucide-react';
import { api } from '../../api/client';
import type { User } from '../../types';
import InviteUserModal from './InviteUserModal';

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
    const queryClient = useQueryClient();

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

    return (
        <div className="space-y-6">
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

            <div className="bg-white border border-zinc-200 rounded-xl overflow-hidden">
                <table className="w-full">
                    <thead>
                        <tr className="border-b border-zinc-200">
                            <th className="text-left px-6 py-3 text-xs font-semibold text-zinc-400 uppercase tracking-wider">User</th>
                            <th className="text-left px-6 py-3 text-xs font-semibold text-zinc-400 uppercase tracking-wider">Role</th>
                            <th className="text-left px-6 py-3 text-xs font-semibold text-zinc-400 uppercase tracking-wider">Status</th>
                            <th className="text-right px-6 py-3 text-xs font-semibold text-zinc-400 uppercase tracking-wider">Actions</th>
                        </tr>
                    </thead>
                    <tbody className="divide-y divide-zinc-200">
                        {isLoading ? (
                            <tr>
                                <td colSpan={4} className="px-6 py-8 text-center text-zinc-400">
                                    <div className="animate-spin rounded-full h-6 w-6 border-t-2 border-b-2 border-blue-500 mx-auto" />
                                </td>
                            </tr>
                        ) : users.map(user => {
                            const role = user.role ?? 'viewer';
                            const RoleIcon = roleIcons[role] || Shield;
                            return (
                                <tr key={user.id} className="hover:bg-zinc-100 transition-colors">
                                    <td className="px-6 py-4">
                                        <div>
                                            <p className="text-sm font-medium text-zinc-800">{user.full_name || 'Unnamed'}</p>
                                            <p className="text-xs text-zinc-400">{user.email}</p>
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
                                    <td className="px-6 py-4 text-right">
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

            <InviteUserModal isOpen={showInviteModal} onClose={() => setShowInviteModal(false)} />
        </div>
    );
}
