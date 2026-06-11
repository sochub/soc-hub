import { useState } from 'react';
import { X, Copy, Check } from 'lucide-react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '../../api/client';

interface InviteUserModalProps {
    isOpen: boolean;
    onClose: () => void;
    tenantId?: number;
}

export default function InviteUserModal({ isOpen, onClose, tenantId }: InviteUserModalProps) {
    const [email, setEmail] = useState('');
    const [role, setRole] = useState('analyst');
    const [inviteLink, setInviteLink] = useState('');
    const [copied, setCopied] = useState(false);
    const [error, setError] = useState('');
    const queryClient = useQueryClient();

    const inviteMutation = useMutation({
        mutationFn: async (data: { email: string; role: string }) => {
            const params = tenantId ? { tenant_id: tenantId } : {};
            const response = await api.post('/invitations/', data, { params });
            return response.data;
        },
        onSuccess: (data) => {
            setInviteLink(data.invite_link || '');
            queryClient.invalidateQueries({ queryKey: ['invitations'] });
            setError('');
        },
        onError: (err: any) => {
            setError(err.response?.data?.detail || 'Failed to create invitation.');
        },
    });

    const handleSubmit = (e: React.FormEvent) => {
        e.preventDefault();
        setInviteLink('');
        inviteMutation.mutate({ email, role });
    };

    const handleCopy = () => {
        navigator.clipboard.writeText(inviteLink);
        setCopied(true);
        setTimeout(() => setCopied(false), 2000);
    };

    const handleClose = () => {
        setEmail('');
        setRole('analyst');
        setInviteLink('');
        setError('');
        setCopied(false);
        onClose();
    };

    if (!isOpen) return null;

    return (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm">
            <div className="w-full max-w-md bg-white rounded-xl border border-zinc-200 shadow-2xl">
                <div className="flex items-center justify-between p-6 border-b border-zinc-200">
                    <h3 className="text-lg font-semibold text-zinc-900">Invite User</h3>
                    <button onClick={handleClose} className="text-zinc-500 hover:text-zinc-800">
                        <X size={20} />
                    </button>
                </div>

                <form onSubmit={handleSubmit} className="p-6 space-y-4">
                    <div className="space-y-2">
                        <label className="text-sm font-medium text-zinc-700">Email</label>
                        <input
                            type="email"
                            required
                            className="w-full bg-white border border-zinc-200 rounded-md px-3 py-2 text-sm text-zinc-900 focus:outline-none focus:ring-2 focus:ring-accent-500"
                            placeholder="user@example.com"
                            value={email}
                            onChange={e => setEmail(e.target.value)}
                            disabled={!!inviteLink}
                        />
                    </div>

                    <div className="space-y-2">
                        <label className="text-sm font-medium text-zinc-700">Role</label>
                        <select
                            className="w-full bg-white border border-zinc-200 rounded-md px-3 py-2 text-sm text-zinc-900 focus:outline-none focus:ring-2 focus:ring-accent-500"
                            value={role}
                            onChange={e => setRole(e.target.value)}
                            disabled={!!inviteLink}
                        >
                            <option value="analyst">Analyst</option>
                            <option value="admin">Admin</option>
                            <option value="viewer">Viewer</option>
                        </select>
                    </div>

                    {error && <p className="text-red-700 text-sm">{error}</p>}

                    {inviteLink ? (
                        <div className="space-y-2">
                            <label className="text-sm font-medium text-green-700">Invitation Link</label>
                            <div className="flex items-center gap-2">
                                <input
                                    type="text"
                                    readOnly
                                    className="flex-1 bg-white border border-zinc-200 rounded-md px-3 py-2 text-xs text-zinc-700"
                                    value={inviteLink}
                                />
                                <button
                                    type="button"
                                    onClick={handleCopy}
                                    className="p-2 bg-zinc-100 hover:bg-zinc-200 rounded-md transition-colors"
                                >
                                    {copied ? <Check size={16} className="text-green-700" /> : <Copy size={16} className="text-zinc-500" />}
                                </button>
                            </div>
                            <p className="text-xs text-zinc-400">Share this link with the invited user.</p>
                        </div>
                    ) : (
                        <button
                            type="submit"
                            disabled={inviteMutation.isPending}
                            className="w-full bg-accent-600 hover:bg-accent-700 text-white font-medium py-2 rounded-md transition-colors disabled:opacity-50"
                        >
                            {inviteMutation.isPending ? 'Sending...' : 'Send Invitation'}
                        </button>
                    )}
                </form>

                {inviteLink && (
                    <div className="px-6 pb-6">
                        <button
                            onClick={handleClose}
                            className="w-full bg-zinc-100 hover:bg-zinc-200 text-zinc-800 font-medium py-2 rounded-md transition-colors"
                        >
                            Done
                        </button>
                    </div>
                )}
            </div>
        </div>
    );
}
