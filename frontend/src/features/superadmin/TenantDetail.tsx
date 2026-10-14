import { useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { ArrowLeft, Save, UserPlus, RefreshCw, X, Copy, Check, Trash2 } from 'lucide-react';
import { api } from '../../api/client';
import type { Tenant, User, Invitation } from '../../types';
import InviteUserModal from '../admin/InviteUserModal';
import DeleteTenantModal from './DeleteTenantModal';
import PageContainer from '../../components/layout/PageContainer';

const statusColors: Record<string, string> = {
    pending: 'bg-amber-500/10 text-amber-300 border-amber-500/30',
    accepted: 'bg-green-500/10 text-green-300 border-green-500/30',
    expired: 'bg-raised-2 text-fg-3 border-line-strong',
    revoked: 'bg-red-500/10 text-red-300 border-red-500/30',
};

export default function TenantDetail() {
    const { id } = useParams<{ id: string }>();
    const navigate = useNavigate();
    const queryClient = useQueryClient();
    const [editing, setEditing] = useState(false);
    const [editName, setEditName] = useState('');
    const [editSlug, setEditSlug] = useState('');
    const [showInviteModal, setShowInviteModal] = useState(false);
    const [showDeleteModal, setShowDeleteModal] = useState(false);
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
            <PageContainer>
                <div className="flex justify-center py-16">
                    <div className="animate-spin rounded-full h-8 w-8 border-t-2 border-b-2 border-accent" />
                </div>
            </PageContainer>
        );
    }

    if (!tenant) {
        return <PageContainer><p className="text-fg-3">Tenant not found.</p></PageContainer>;
    }

    return (
        <PageContainer>
            <button
                onClick={() => navigate('/superadmin/tenants')}
                className="flex items-center gap-2 text-sm text-fg-3 hover:text-fg transition-colors"
            >
                <ArrowLeft size={16} />
                Back to Tenants
            </button>

            {/* Tenant Info */}
            <div className="bg-panel border border-line rounded-xl p-6 space-y-4">
                <div className="flex items-center justify-between">
                    <h1 className="text-xl font-bold text-fg">{tenant.name}</h1>
                    <div className="flex items-center gap-2">
                        {!editing && (
                            <button
                                onClick={startEdit}
                                className="px-3 py-1.5 text-xs bg-raised hover:bg-raised-2 text-fg rounded-md transition-colors"
                            >
                                Edit
                            </button>
                        )}
                        <button
                            onClick={() => toggleActiveMutation.mutate()}
                            className={`px-3 py-1.5 text-xs rounded-md transition-colors ${
                                tenant.is_active
                                    ? 'bg-red-500/10 text-red-300 hover:bg-red-500/10'
                                    : 'bg-green-500/10 text-green-300 hover:bg-green-500/10'
                            }`}
                        >
                            {tenant.is_active ? 'Deactivate' : 'Activate'}
                        </button>
                        {!tenant.is_active && (
                            <button
                                onClick={() => setShowDeleteModal(true)}
                                className="flex items-center gap-1.5 px-3 py-1.5 text-xs bg-red-500/10 text-red-300 hover:bg-red-500/10 rounded-md transition-colors"
                            >
                                <Trash2 size={12} />
                                Delete Permanently
                            </button>
                        )}
                    </div>
                </div>

                {editing ? (
                    <div className="space-y-3">
                        <div className="space-y-1">
                            <label className="text-xs text-muted">Name</label>
                            <input
                                className="w-full bg-panel border border-line rounded-md px-3 py-2 text-sm text-fg focus:outline-hidden focus:ring-2 focus:ring-accent"
                                value={editName}
                                onChange={e => setEditName(e.target.value)}
                            />
                        </div>
                        <div className="space-y-1">
                            <label className="text-xs text-muted">Slug</label>
                            <input
                                className="w-full bg-panel border border-line rounded-md px-3 py-2 text-sm text-fg focus:outline-hidden focus:ring-2 focus:ring-accent"
                                value={editSlug}
                                onChange={e => setEditSlug(e.target.value)}
                            />
                        </div>
                        <div className="flex gap-2">
                            <button
                                onClick={handleSave}
                                disabled={updateMutation.isPending}
                                className="flex items-center gap-1.5 px-3 py-1.5 text-xs bg-accent hover:bg-accent-hover text-on-accent rounded-md transition-colors disabled:opacity-50"
                            >
                                <Save size={12} />
                                Save
                            </button>
                            <button
                                onClick={() => setEditing(false)}
                                className="px-3 py-1.5 text-xs bg-raised hover:bg-raised-2 text-fg-2 rounded-md transition-colors"
                            >
                                Cancel
                            </button>
                        </div>
                    </div>
                ) : (
                    <div className="grid grid-cols-2 gap-4 text-sm">
                        <div>
                            <p className="text-xs text-muted">Slug</p>
                            <p className="text-fg-2">{tenant.slug}</p>
                        </div>
                        <div>
                            <p className="text-xs text-muted">Status</p>
                            <p className={tenant.is_active ? 'text-green-300' : 'text-red-300'}>
                                {tenant.is_active ? 'Active' : 'Inactive'}
                            </p>
                        </div>
                        <div>
                            <p className="text-xs text-muted">Created</p>
                            <p className="text-fg-2">{new Date(tenant.created_at).toLocaleDateString()}</p>
                        </div>
                        <div>
                            <p className="text-xs text-muted">Users</p>
                            <p className="text-fg-2">{users.length}</p>
                        </div>
                    </div>
                )}
            </div>

            {/* Users Table */}
            <div className="bg-panel border border-line rounded-xl overflow-hidden">
                <div className="px-6 py-4 border-b border-line flex items-center justify-between">
                    <h2 className="text-sm font-semibold text-fg-2">Users ({users.length})</h2>
                    <button
                        onClick={() => setShowInviteModal(true)}
                        className="flex items-center gap-1.5 px-3 py-1.5 text-xs bg-accent hover:bg-accent-hover text-on-accent rounded-md transition-colors"
                    >
                        <UserPlus size={12} />
                        Invite User
                    </button>
                </div>
                <table className="w-full">
                    <thead>
                        <tr className="border-b border-line">
                            <th className="text-left px-6 py-3 text-xs font-semibold text-muted uppercase tracking-wider">User</th>
                            <th className="text-left px-6 py-3 text-xs font-semibold text-muted uppercase tracking-wider">Role</th>
                            <th className="text-left px-6 py-3 text-xs font-semibold text-muted uppercase tracking-wider">Status</th>
                        </tr>
                    </thead>
                    <tbody className="divide-y divide-line">
                        {usersLoading ? (
                            <tr>
                                <td colSpan={3} className="px-6 py-8 text-center">
                                    <div className="animate-spin rounded-full h-5 w-5 border-t-2 border-b-2 border-accent mx-auto" />
                                </td>
                            </tr>
                        ) : users.length === 0 ? (
                            <tr>
                                <td colSpan={3} className="px-6 py-8 text-center text-muted text-sm">No users in this tenant</td>
                            </tr>
                        ) : users.map(user => (
                            <tr key={user.id} className="hover:bg-raised transition-colors">
                                <td className="px-6 py-3">
                                    <p className="text-sm text-fg">{user.full_name || 'Unnamed'}</p>
                                    <p className="text-xs text-muted">{user.email}</p>
                                </td>
                                <td className="px-6 py-3">
                                    <span className="text-xs text-fg-3">{user.role}</span>
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
            <div className="bg-panel border border-line rounded-xl overflow-hidden">
                <div className="px-6 py-4 border-b border-line">
                    <h2 className="text-sm font-semibold text-fg-2">Invitations ({invitations.length})</h2>
                </div>
                {invitationsLoading ? (
                    <div className="px-6 py-8 flex justify-center">
                        <div className="animate-spin rounded-full h-5 w-5 border-t-2 border-b-2 border-accent" />
                    </div>
                ) : invitations.length === 0 ? (
                    <div className="px-6 py-8 text-center text-muted text-sm">No invitations yet</div>
                ) : (
                    <table className="w-full">
                        <thead>
                            <tr className="border-b border-line">
                                <th className="text-left px-6 py-3 text-xs font-semibold text-muted uppercase tracking-wider">Email</th>
                                <th className="text-left px-6 py-3 text-xs font-semibold text-muted uppercase tracking-wider">Role</th>
                                <th className="text-left px-6 py-3 text-xs font-semibold text-muted uppercase tracking-wider">Status</th>
                                <th className="text-left px-6 py-3 text-xs font-semibold text-muted uppercase tracking-wider">Expires</th>
                                <th className="text-right px-6 py-3 text-xs font-semibold text-muted uppercase tracking-wider">Actions</th>
                            </tr>
                        </thead>
                        <tbody className="divide-y divide-line">
                            {[...pendingInvitations, ...otherInvitations].map(inv => {
                                const isExpired = inv.status === 'pending' && new Date(inv.expires_at) < new Date();
                                const displayStatus = isExpired ? 'expired' : inv.status;
                                return (
                                    <tr key={inv.id} className="hover:bg-raised transition-colors">
                                        <td className="px-6 py-3">
                                            <p className="text-sm text-fg">{inv.email}</p>
                                        </td>
                                        <td className="px-6 py-3">
                                            <span className="text-xs text-fg-3">{inv.role}</span>
                                        </td>
                                        <td className="px-6 py-3">
                                            <span className={`inline-flex px-2 py-0.5 rounded-full text-xs font-medium border ${statusColors[displayStatus] || statusColors.pending}`}>
                                                {displayStatus}
                                            </span>
                                        </td>
                                        <td className="px-6 py-3">
                                            <span className="text-xs text-muted">
                                                {new Date(inv.expires_at).toLocaleString()}
                                            </span>
                                        </td>
                                        <td className="px-6 py-3 text-right">
                                            {(displayStatus === 'pending' || displayStatus === 'expired') && (
                                                <div className="flex items-center justify-end gap-1">
                                                    <button
                                                        onClick={() => handleCopyLink(inv)}
                                                        className="p-1.5 text-fg-3 hover:text-fg transition-colors rounded-sm"
                                                        title="Copy invite link"
                                                    >
                                                        {copiedId === inv.id ? <Check size={14} className="text-green-300" /> : <Copy size={14} />}
                                                    </button>
                                                    <button
                                                        onClick={() => resendMutation.mutate(inv.id)}
                                                        disabled={resendMutation.isPending}
                                                        className="p-1.5 text-fg-3 hover:text-accent transition-colors rounded-sm disabled:opacity-50"
                                                        title="Resend invitation"
                                                    >
                                                        <RefreshCw size={14} className={resendMutation.isPending ? 'animate-spin' : ''} />
                                                    </button>
                                                    <button
                                                        onClick={() => revokeMutation.mutate(inv.id)}
                                                        disabled={revokeMutation.isPending}
                                                        className="p-1.5 text-fg-3 hover:text-red-300 transition-colors rounded-sm disabled:opacity-50"
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

            <DeleteTenantModal
                tenant={tenant}
                isOpen={showDeleteModal}
                onClose={() => setShowDeleteModal(false)}
            />
        </PageContainer>
    );
}
