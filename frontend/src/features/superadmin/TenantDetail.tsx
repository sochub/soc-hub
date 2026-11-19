import { useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { ArrowLeft, Save, UserPlus, RefreshCw, X, Copy, Check, Trash2 } from 'lucide-react';
import { api } from '../../api/client';
import type { Tenant, User, Invitation } from '../../types';
import InviteUserModal from '../admin/InviteUserModal';
import DeleteTenantModal from './DeleteTenantModal';
import { Button, Card, CardHeader, CardTitle, EmptyState, Input, Label, Skeleton, Stamp, buttonClass, type Tone } from '../../components/ui';
import SettingsPageHeader from '../settings/SettingsPageHeader';

const INVITE_TONE: Record<string, Tone> = { pending: 'medium', accepted: 'ok', expired: 'neutral', revoked: 'critical' };

export default function TenantDetail() {
    const { id } = useParams<{ id: string }>();
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
        return <Skeleton rows={6} />;
    }

    if (!tenant) {
        return <EmptyState title="Organization not found" action={<Link to="/settings/tenants" className={buttonClass('secondary', 'sm')}>Back to organizations</Link>} />;
    }

    return (
        <div className="space-y-6">
            <Link to="/settings/tenants" className="inline-flex items-center gap-1.5 text-sm text-fg-3 hover:text-fg">
                <ArrowLeft size={14} aria-hidden />
                Organizations
            </Link>

            <SettingsPageHeader title={tenant.name}
                actions={<>
                    {!editing && <Button variant="secondary" size="sm" onClick={startEdit}>Edit</Button>}
                    <Button variant={tenant.is_active ? 'danger' : 'secondary'} size="sm" onClick={() => toggleActiveMutation.mutate()}>
                        {tenant.is_active ? 'Deactivate' : 'Activate'}
                    </Button>
                    {!tenant.is_active && (
                        <Button variant="danger" size="sm" onClick={() => setShowDeleteModal(true)}>
                            <Trash2 size={12} />
                            Delete permanently
                        </Button>
                    )}
                </>} />

            <Card className="p-4">
                {editing ? (
                    <div className="space-y-3 max-w-md">
                        <div>
                            <Label htmlFor="tenant-name">Name</Label>
                            <Input id="tenant-name" value={editName} onChange={e => setEditName(e.target.value)} />
                        </div>
                        <div>
                            <Label htmlFor="tenant-slug">Slug</Label>
                            <Input id="tenant-slug" className="font-mono" value={editSlug} onChange={e => setEditSlug(e.target.value)} />
                        </div>
                        <div className="flex gap-2">
                            <Button size="sm" onClick={handleSave} disabled={updateMutation.isPending}>
                                <Save size={12} />
                                Save
                            </Button>
                            <Button size="sm" variant="secondary" onClick={() => setEditing(false)}>Cancel</Button>
                        </div>
                    </div>
                ) : (
                    <dl className="grid grid-cols-2 sm:grid-cols-4 gap-4 text-sm">
                        <div>
                            <dt className="label-caps">Slug</dt>
                            <dd className="mt-1 font-mono text-fg-2">{tenant.slug}</dd>
                        </div>
                        <div>
                            <dt className="label-caps">Status</dt>
                            <dd className="mt-1"><Stamp tone={tenant.is_active ? 'ok' : 'critical'}>{tenant.is_active ? 'Active' : 'Inactive'}</Stamp></dd>
                        </div>
                        <div>
                            <dt className="label-caps">Created</dt>
                            <dd className="mt-1 num text-fg-2">{new Date(tenant.created_at).toLocaleDateString()}</dd>
                        </div>
                        <div>
                            <dt className="label-caps">Users</dt>
                            <dd className="mt-1 num text-fg-2">{users.length}</dd>
                        </div>
                    </dl>
                )}
            </Card>

            <Card>
                <CardHeader>
                    <CardTitle>Users <span className="num font-normal text-fg-3">{users.length}</span></CardTitle>
                    <Button size="sm" onClick={() => setShowInviteModal(true)}>
                        <UserPlus size={12} />
                        Invite user
                    </Button>
                </CardHeader>
                <div className="overflow-x-auto">
                <table className="w-full text-sm">
                    <thead>
                        <tr className="border-b border-line label-caps text-left">
                            <th className="px-3 py-2">User</th>
                            <th className="px-3 py-2">Role</th>
                            <th className="px-3 py-2">Status</th>
                        </tr>
                    </thead>
                    <tbody className="divide-y divide-line">
                        {usersLoading ? (
                            <tr><td colSpan={3} className="p-3"><Skeleton rows={3} /></td></tr>
                        ) : users.length === 0 ? (
                            <tr>
                                <td colSpan={3} className="px-3 py-8 text-center text-fg-3">No users in this organization</td>
                            </tr>
                        ) : users.map(user => (
                            <tr key={user.id} className="hover:bg-raised transition-colors">
                                <td className="px-3 py-2.5">
                                    <p className="text-fg">{user.full_name || 'Unnamed'}</p>
                                    <p className="text-xs text-fg-3">{user.email}</p>
                                </td>
                                <td className="px-3 py-2.5">
                                    <span className="text-xs text-fg-2 capitalize">{user.role}</span>
                                </td>
                                <td className="px-3 py-2.5">
                                    <Stamp tone={user.is_active ? 'ok' : 'critical'}>{user.is_active ? 'Active' : 'Inactive'}</Stamp>
                                </td>
                            </tr>
                        ))}
                    </tbody>
                </table>
                </div>
            </Card>

            <Card>
                <CardHeader>
                    <CardTitle>Invitations <span className="num font-normal text-fg-3">{invitations.length}</span></CardTitle>
                </CardHeader>
                {invitationsLoading ? (
                    <Skeleton rows={3} className="p-3" />
                ) : invitations.length === 0 ? (
                    <EmptyState title="No invitations yet" className="py-8" />
                ) : (
                    <div className="overflow-x-auto">
                    <table className="w-full text-sm">
                        <thead>
                            <tr className="border-b border-line label-caps text-left">
                                <th className="px-3 py-2">Email</th>
                                <th className="px-3 py-2">Role</th>
                                <th className="px-3 py-2">Status</th>
                                <th className="px-3 py-2">Expires</th>
                                <th className="px-3 py-2 text-right"><span className="sr-only">Actions</span></th>
                            </tr>
                        </thead>
                        <tbody className="divide-y divide-line">
                            {[...pendingInvitations, ...otherInvitations].map(inv => {
                                const isExpired = inv.status === 'pending' && new Date(inv.expires_at) < new Date();
                                const displayStatus = isExpired ? 'expired' : inv.status;
                                return (
                                    <tr key={inv.id} className="hover:bg-raised transition-colors">
                                        <td className="px-3 py-2.5 text-fg">{inv.email}</td>
                                        <td className="px-3 py-2.5">
                                            <span className="text-xs text-fg-2 capitalize">{inv.role}</span>
                                        </td>
                                        <td className="px-3 py-2.5">
                                            <Stamp tone={INVITE_TONE[displayStatus] ?? 'neutral'}>{displayStatus}</Stamp>
                                        </td>
                                        <td className="px-3 py-2.5">
                                            <span className="text-xs text-fg-3 num">
                                                {new Date(inv.expires_at).toLocaleString()}
                                            </span>
                                        </td>
                                        <td className="px-3 py-2.5 text-right">
                                            {(displayStatus === 'pending' || displayStatus === 'expired') && (
                                                <div className="flex items-center justify-end gap-1">
                                                    <Button variant="ghost" size="icon" className="h-8 w-8" onClick={() => handleCopyLink(inv)}
                                                        title="Copy invite link" aria-label={`Copy invite link for ${inv.email}`}>
                                                        {copiedId === inv.id ? <Check size={14} className="text-lime" /> : <Copy size={14} />}
                                                    </Button>
                                                    <Button variant="ghost" size="icon" className="h-8 w-8" onClick={() => resendMutation.mutate(inv.id)}
                                                        disabled={resendMutation.isPending} title="Resend invitation" aria-label={`Resend invitation to ${inv.email}`}>
                                                        <RefreshCw size={14} className={resendMutation.isPending ? 'animate-spin' : ''} />
                                                    </Button>
                                                    <Button variant="ghost" size="icon" className="h-8 w-8 hover:text-severity-critical" onClick={() => revokeMutation.mutate(inv.id)}
                                                        disabled={revokeMutation.isPending} title="Revoke invitation" aria-label={`Revoke invitation for ${inv.email}`}>
                                                        <X size={14} />
                                                    </Button>
                                                </div>
                                            )}
                                        </td>
                                    </tr>
                                );
                            })}
                        </tbody>
                    </table>
                    </div>
                )}
            </Card>

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
        </div>
    );
}
