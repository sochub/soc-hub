import { useState } from 'react';
import { Copy, Check } from 'lucide-react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '../../api/client';
import Modal, { modalInput, modalLabel, btnPrimary, btnSecondary } from '../../components/layout/Modal';

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

    return (
        <Modal
            open={isOpen}
            onClose={handleClose}
            title="Invite User"
            size="lg"
            footer={inviteLink ? (
                <button onClick={handleClose} className={btnSecondary}>
                    Done
                </button>
            ) : (<>
                <button type="button" onClick={handleClose} className={btnSecondary}>Cancel</button>
                <button
                    type="submit"
                    form="invite-user-form"
                    disabled={inviteMutation.isPending}
                    className={btnPrimary}
                >
                    {inviteMutation.isPending ? 'Sending...' : 'Send Invitation'}
                </button>
            </>)}
        >
            <form id="invite-user-form" onSubmit={handleSubmit} className="space-y-4">
                <div>
                    <label className={modalLabel}>Email</label>
                    <input
                        type="email"
                        required
                        className={modalInput}
                        placeholder="user@example.com"
                        value={email}
                        onChange={e => setEmail(e.target.value)}
                        disabled={!!inviteLink}
                    />
                </div>

                <div>
                    <label className={modalLabel}>Role</label>
                    <select
                        className={modalInput}
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

                {inviteLink && (
                    <div className="space-y-2">
                        <label className="block text-xs font-medium text-green-700">Invitation Link</label>
                        <div className="flex items-center gap-2">
                            <input
                                type="text"
                                readOnly
                                className="flex-1 bg-white border border-zinc-300 px-3 py-2 text-xs font-mono text-zinc-700"
                                value={inviteLink}
                            />
                            <button
                                type="button"
                                onClick={handleCopy}
                                aria-label="Copy invitation link"
                                className="p-2 border border-zinc-300 hover:bg-zinc-100 transition-colors"
                            >
                                {copied ? <Check size={16} className="text-green-700" /> : <Copy size={16} className="text-zinc-500" />}
                            </button>
                        </div>
                        <p className="text-xs text-zinc-400">Share this link with the invited user.</p>
                    </div>
                )}
            </form>
        </Modal>
    );
}
