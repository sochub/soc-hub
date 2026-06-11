import { useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { ArrowLeft, Save, UserPlus, RefreshCw, X, Copy, Check } from 'lucide-react';
import { api } from '../../api/client';
import type { Tenant, User, Invitation } from '../../types';
import InviteUserModal from '../admin/InviteUserModal';

const statusColors: Record<string, string> = {
    pending: 'bg-amber-50 text-amber-700 border-amber-200',
    accepted: 'bg-green-50 text-green-700 border-green-200',
    expired: 'bg-zinc-200 text-zinc-500 border-zinc-300',
    revoked: 'bg-red-50 text-red-700 border-red-200',
};

export default function TenantDetail() {
    const { id } = useParams<{ id: string }>();
    const navigate = useNavigate();
    const queryClient = useQueryClient();
    const [editing, setEditing] = useState(false);
    const [editName, setEditName] = useState('');
    const [editSlug, setEditSlug] = useState('');
    const [showInviteModal, setShowInviteModal] = useState(false);
    const [copiedId, setCopiedId] = useState<number | null>(null);

    const { data: tenant, isLoading: tenantLoading } = useQuery({
        queryKey: ['tenant', id],
        queryFn: async () => {
            const response = await api.get(`/tenants/${id}`);
            return response.data as Tenant;
        },
    });

    const { data: users = [], isLoading: usersLoading } = useQuery({
        queryKey: ['tenant-users', id],
        queryFn: async () => {
            const response = await api.get(`/tenants/${id}/users`);
            return response.data as User[];
        },
    });

    const { data: invitations = [], isLoading: invitationsLoading } = useQuery({
        queryKey: ['tenant-invitations', id],
        queryFn: async () => {
            const response = await api.get('/invitations/', { params: { tenant_id: id } });
            return response.data as Invitation[];
        },
    });

    const updateMutation = useMutation({
        mutationFn: async (data: { name?: string; slug?: string }) => {
            const response = await api.put(`/tenants/${id}`, data);
            return response.data;
        },
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['tenant', id] });
            queryClient.invalidateQueries({ queryKey: ['tenants'] });
            setEditing(false);
        },
    });

    const toggleActiveMutation = useMutation({
        mutationFn: async () => {
            if (tenant?.is_active) {
                await api.delete(`/tenants/${id}`);
            } else {
                await api.put(`/tenants/${id}`, { is_active: true });
            }
        },
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['tenant', id] });
            queryClient.invalidateQueries({ queryKey: ['tenants'] });
        },
    });

    const resendMutation = useMutation({
        mutationFn: async (invitationId: number) => {
            const response = await api.post(`/invitations/${invitationId}/resend`, null, { params: { tenant_id: id } });
            return response.data;
        },
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['tenant-invitations', id] });
        },
    });

    const revokeMutation = useMutation({
        mutationFn: async (invitationId: number) => {
            await api.delete(`/invitations/${invitationId}`, { params: { tenant_id: id } });
        },
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['tenant-invitations', id] });
        },
    });

    const handleCopyLink = (invitation: Invitation) => {
        if (invitation.invite_link) {
            navigator.clipboard.writeText(invitation.invite_link);
            setCopiedId(invitation.id);
            setTimeout(() => setCopiedId(null), 2000);
        }
    };

    const startEdit = () => {
        if (tenant) {
            setEditName(tenant.name);
            setEditSlug(tenant.slug);
            setEditing(true);
        }
    };

    const handleSave = () => {
        updateMutation.mutate({ name: editName, slug: editSlug });
    };

    const pendingInvitations = invitations.filter(i => i.status === 'pending');
    const otherInvitations = invitations.filter(i => i.status !== 'pending');

    if (tenantLoading) {
        return (
            <div className="flex justify-center py-16">
                <div className="animate-spin rounded-full h-8 w-8 border-t-2 border-b-2 border-blue-500" />
            </div>
        );
    }

    if (!tenant) {
        return <p className="text-zinc-500">Tenant not found.</p>;
    }

    return (
        <div className="space-y-6">
            <button
                onClick={() => navigate('/superadmin/tenants')}
                className="flex items-center gap-2 text-sm text-zinc-500 hover:text-zinc-800 transition-colors"
            >
                <ArrowLeft size={16} />
                Back to Tenants
            </button>

            {/* Tenant Info */}
            <div className="bg-white border border-zinc-200 rounded-xl p-6 space-y-4">
                <div className="flex items-center justify-between">
                    <h1 className="text-xl font-bold text-zinc-900">{tenant.name}</h1>
                    <div className="flex items-center gap-2">
                        {!editing && (
                            <button
                                onClick={startEdit}
                                className="px-3 py-1.5 text-xs bg-zinc-100 hover:bg-zinc-200 text-zinc-800 rounded-md transition-colors"
                            >
                                Edit
                            </button>
                        )}
                        <button
                            onClick={() => toggleActiveMutation.mutate()}
                            className={`px-3 py-1.5 text-xs rounded-md transition-colors ${
                                tenant.is_active
                                    ? 'bg-red-50 text-red-700 hover:bg-red-50'
                                    : 'bg-green-50 text-green-700 hover:bg-green-50'
                            }`}
                        >
                            {tenant.is_active ? 'Deactivate' : 'Activate'}
                        </button>
                    </div>
                </div>

                {editing ? (
                    <div className="space-y-3">
                        <div className="space-y-1">
                            <label className="text-xs text-zinc-400">Name</label>
                            <input
                                className="w-full bg-white border border-zinc-200 rounded-md px-3 py-2 text-sm text-zinc-900 focus:outline-none focus:ring-2 focus:ring-accent-500"
                                value={editName}
                                onChange={e => setEditName(e.target.value)}
                            />
                        </div>
                        <div className="space-y-1">
                            <label className="text-xs text-zinc-400">Slug</label>
                            <input
                                className="w-full bg-white border border-zinc-200 rounded-md px-3 py-2 text-sm text-zinc-900 focus:outline-none focus:ring-2 focus:ring-accent-500"
                                value={editSlug}
                                onChange={e => setEditSlug(e.target.value)}
                            />
                        </div>
                        <div className="flex gap-2">
                            <button
                                onClick={handleSave}
                                disabled={updateMutation.isPending}
                                className="flex items-center gap-1.5 px-3 py-1.5 text-xs bg-accent-600 hover:bg-accent-700 text-white rounded-md transition-colors disabled:opacity-50"
                            >
                                <Save size={12} />
                                Save
                            </button>
                            <button
                                onClick={() => setEditing(false)}
                                className="px-3 py-1.5 text-xs bg-zinc-100 hover:bg-zinc-200 text-zinc-700 rounded-md transition-colors"
                            >
                                Cancel
                            </button>
                        </div>
                    </div>
                ) : (
                    <div className="grid grid-cols-2 gap-4 text-sm">
                        <div>
                            <p className="text-xs text-zinc-400">Slug</p>
                            <p className="text-zinc-700">{tenant.slug}</p>
                        </div>
                        <div>
                            <p className="text-xs text-zinc-400">Status</p>
                            <p className={tenant.is_active ? 'text-green-700' : 'text-red-700'}>
                                {tenant.is_active ? 'Active' : 'Inactive'}
                            </p>
                        </div>
                        <div>
                            <p className="text-xs text-zinc-400">Created</p>
                            <p className="text-zinc-700">{new Date(tenant.created_at).toLocaleDateString()}</p>
                        </div>
                        <div>
                            <p className="text-xs text-zinc-400">Users</p>
                            <p className="text-zinc-700">{users.length}</p>
                        </div>
                    </div>
                )}
            </div>

            {/* Users Table */}
            <div className="bg-white border border-zinc-200 rounded-xl overflow-hidden">
                <div className="px-6 py-4 border-b border-zinc-200 flex items-center justify-between">
                    <h2 className="text-sm font-semibold text-zinc-700">Users ({users.length})</h2>
                    <button
                        onClick={() => setShowInviteModal(true)}
                        className="flex items-center gap-1.5 px-3 py-1.5 text-xs bg-accent-600 hover:bg-accent-700 text-white rounded-md transition-colors"
                    >
                        <UserPlus size={12} />
                        Invite User
                    </button>
                </div>
                <table className="w-full">
                    <thead>
                        <tr className="border-b border-zinc-200">
                            <th className="text-left px-6 py-3 text-xs font-semibold text-zinc-400 uppercase tracking-wider">User</th>
                            <th className="text-left px-6 py-3 text-xs font-semibold text-zinc-400 uppercase tracking-wider">Role</th>
                            <th className="text-left px-6 py-3 text-xs font-semibold text-zinc-400 uppercase tracking-wider">Status</th>
                        </tr>
                    </thead>
                    <tbody className="divide-y divide-zinc-200">
                        {usersLoading ? (
                            <tr>
                                <td colSpan={3} className="px-6 py-8 text-center">
                                    <div className="animate-spin rounded-full h-5 w-5 border-t-2 border-b-2 border-blue-500 mx-auto" />
                                </td>
                            </tr>
                        ) : users.length === 0 ? (
                            <tr>
                                <td colSpan={3} className="px-6 py-8 text-center text-zinc-400 text-sm">No users in this tenant</td>
                            </tr>
                        ) : users.map(user => (
                            <tr key={user.id} className="hover:bg-zinc-100 transition-colors">
                                <td className="px-6 py-3">
                                    <p className="text-sm text-zinc-800">{user.full_name || 'Unnamed'}</p>
                                    <p className="text-xs text-zinc-400">{user.email}</p>
                                </td>
                                <td className="px-6 py-3">
                                    <span className="text-xs text-zinc-500">{user.role}</span>
                                </td>
                                <td className="px-6 py-3">
                                    <span className={`w-2 h-2 rounded-full inline-block ${user.is_active ? 'bg-green-400' : 'bg-red-400'}`} />
                                </td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            </div>

            {/* Invitations Table */}
            <div className="bg-white border border-zinc-200 rounded-xl overflow-hidden">
                <div className="px-6 py-4 border-b border-zinc-200">
                    <h2 className="text-sm font-semibold text-zinc-700">Invitations ({invitations.length})</h2>
                </div>
                {invitationsLoading ? (
                    <div className="px-6 py-8 flex justify-center">
                        <div className="animate-spin rounded-full h-5 w-5 border-t-2 border-b-2 border-blue-500" />
                    </div>
                ) : invitations.length === 0 ? (
                    <div className="px-6 py-8 text-center text-zinc-400 text-sm">No invitations yet</div>
                ) : (
                    <table className="w-full">
                        <thead>
                            <tr className="border-b border-zinc-200">
                                <th className="text-left px-6 py-3 text-xs font-semibold text-zinc-400 uppercase tracking-wider">Email</th>
                                <th className="text-left px-6 py-3 text-xs font-semibold text-zinc-400 uppercase tracking-wider">Role</th>
                                <th className="text-left px-6 py-3 text-xs font-semibold text-zinc-400 uppercase tracking-wider">Status</th>
                                <th className="text-left px-6 py-3 text-xs font-semibold text-zinc-400 uppercase tracking-wider">Expires</th>
                                <th className="text-right px-6 py-3 text-xs font-semibold text-zinc-400 uppercase tracking-wider">Actions</th>
                            </tr>
                        </thead>
                        <tbody className="divide-y divide-zinc-200">
                            {[...pendingInvitations, ...otherInvitations].map(inv => {
                                const isExpired = inv.status === 'pending' && new Date(inv.expires_at) < new Date();
                                const displayStatus = isExpired ? 'expired' : inv.status;
                                return (
                                    <tr key={inv.id} className="hover:bg-zinc-100 transition-colors">
                                        <td className="px-6 py-3">
                                            <p className="text-sm text-zinc-800">{inv.email}</p>
                                        </td>
                                        <td className="px-6 py-3">
                                            <span className="text-xs text-zinc-500">{inv.role}</span>
                                        </td>
                                        <td className="px-6 py-3">
                                            <span className={`inline-flex px-2 py-0.5 rounded-full text-xs font-medium border ${statusColors[displayStatus] || statusColors.pending}`}>
                                                {displayStatus}
                                            </span>
                                        </td>
                                        <td className="px-6 py-3">
                                            <span className="text-xs text-zinc-400">
                                                {new Date(inv.expires_at).toLocaleString()}
                                            </span>
                                        </td>
                                        <td className="px-6 py-3 text-right">
                                            {(displayStatus === 'pending' || displayStatus === 'expired') && (
                                                <div className="flex items-center justify-end gap-1">
                                                    <button
                                                        onClick={() => handleCopyLink(inv)}
                                                        className="p-1.5 text-zinc-500 hover:text-zinc-800 transition-colors rounded"
                                                        title="Copy invite link"
                                                    >
                                                        {copiedId === inv.id ? <Check size={14} className="text-green-700" /> : <Copy size={14} />}
                                                    </button>
                                                    <button
                                                        onClick={() => resendMutation.mutate(inv.id)}
                                                        disabled={resendMutation.isPending}
                                                        className="p-1.5 text-zinc-500 hover:text-accent-600 transition-colors rounded disabled:opacity-50"
                                                        title="Resend invitation"
                                                    >
                                                        <RefreshCw size={14} className={resendMutation.isPending ? 'animate-spin' : ''} />
                                                    </button>
                                                    <button
                                                        onClick={() => revokeMutation.mutate(inv.id)}
                                                        disabled={revokeMutation.isPending}
                                                        className="p-1.5 text-zinc-500 hover:text-red-700 transition-colors rounded disabled:opacity-50"
                                                        title="Revoke invitation"
                                                    >
                                                        <X size={14} />
                                                    </button>
                                                </div>
                                            )}
                                        </td>
                                    </tr>
                                );
                            })}
                        </tbody>
                    </table>
                )}
            </div>

            <InviteUserModal
                isOpen={showInviteModal}
                onClose={() => {
                    setShowInviteModal(false);
                    queryClient.invalidateQueries({ queryKey: ['tenant-users', id] });
                    queryClient.invalidateQueries({ queryKey: ['tenant-invitations', id] });
                }}
                tenantId={Number(id)}
            />
        </div>
    );
}
