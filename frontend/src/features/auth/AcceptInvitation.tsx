import { useState, useEffect } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { api } from '../../api/client';
import type { InvitationValidation } from '../../types';

export default function AcceptInvitation() {
    const { token } = useParams<{ token: string }>();
    const navigate = useNavigate();
    const [validation, setValidation] = useState<InvitationValidation | null>(null);
    const [loading, setLoading] = useState(true);
    const [fullName, setFullName] = useState('');
    const [password, setPassword] = useState('');
    const [confirmPassword, setConfirmPassword] = useState('');
    const [error, setError] = useState('');
    const [submitting, setSubmitting] = useState(false);
    const [success, setSuccess] = useState(false);

    useEffect(() => {
        if (!token) return;
        api.get(`/invitations/validate/${token}`)
            .then(res => setValidation(res.data))
            .catch(() => setValidation({ email: '', tenant_name: '', role: '', valid: false }))
            .finally(() => setLoading(false));
    }, [token]);

    const handleSubmit = async (e: React.FormEvent) => {
        e.preventDefault();
        setError('');

        if (password !== confirmPassword) {
            setError('Passwords do not match.');
            return;
        }
        if (password.length < 8) {
            setError('Password must be at least 8 characters.');
            return;
        }

        setSubmitting(true);
        try {
            await api.post('/invitations/accept', {
                token,
                full_name: fullName,
                password,
            });
            setSuccess(true);
            setTimeout(() => navigate('/login'), 2000);
        } catch (err: any) {
            setError(err.response?.data?.detail || 'Failed to accept invitation.');
        } finally {
            setSubmitting(false);
        }
    };

    if (loading) {
        return (
            <div className="min-h-screen flex items-center justify-center bg-canvas text-fg">
                <div className="animate-spin rounded-full h-8 w-8 border-t-2 border-b-2 border-accent" />
            </div>
        );
    }

    if (!validation?.valid) {
        return (
            <div className="min-h-screen flex items-center justify-center bg-canvas text-fg">
                <div className="max-w-md w-full p-8 bg-panel rounded-xl border border-line text-center space-y-4">
                    <h2 className="text-2xl font-bold">Invalid Invitation</h2>
                    <p className="text-fg-2">This invitation link is invalid, expired, or has already been used.</p>
                    <button
                        onClick={() => navigate('/login')}
                        className="px-6 py-2 bg-accent hover:bg-accent text-on-accent rounded-md transition-colors"
                    >
                        Go to Login
                    </button>
                </div>
            </div>
        );
    }

    if (success) {
        return (
            <div className="min-h-screen flex items-center justify-center bg-canvas text-fg">
                <div className="max-w-md w-full p-8 bg-panel rounded-xl border border-line text-center space-y-4">
                    <h2 className="text-2xl font-bold text-green-400">Account Created!</h2>
                    <p className="text-fg-2">Redirecting to login...</p>
                </div>
            </div>
        );
    }

    return (
        <div className="min-h-screen flex items-center justify-center bg-canvas text-fg p-4">
            <div className="max-w-md w-full p-8 bg-panel rounded-xl border border-line space-y-6">
                <div className="space-y-2">
                    <h2 className="text-2xl font-bold">Join {validation.tenant_name}</h2>
                    <p className="text-fg-2 text-sm">
                        You've been invited as <span className="text-fg font-medium">{validation.role}</span> to join {validation.tenant_name} on SOC Hub.
                    </p>
                    <p className="text-fg-3 text-xs">{validation.email}</p>
                </div>

                <form onSubmit={handleSubmit} className="space-y-4">
                    <div className="space-y-2">
                        <label className="text-sm font-medium text-fg-2">Full Name</label>
                        <input
                            type="text"
                            required
                            className="w-full bg-canvas border border-line rounded-md px-3 py-2 text-sm focus:outline-hidden focus:ring-2 focus:ring-accent"
                            value={fullName}
                            onChange={e => setFullName(e.target.value)}
                            disabled={submitting}
                        />
                    </div>
                    <div className="space-y-2">
                        <label className="text-sm font-medium text-fg-2">Password</label>
                        <input
                            type="password"
                            required
                            className="w-full bg-canvas border border-line rounded-md px-3 py-2 text-sm focus:outline-hidden focus:ring-2 focus:ring-accent"
                            value={password}
                            onChange={e => setPassword(e.target.value)}
                            disabled={submitting}
                        />
                    </div>
                    <div className="space-y-2">
                        <label className="text-sm font-medium text-fg-2">Confirm Password</label>
                        <input
                            type="password"
                            required
                            className="w-full bg-canvas border border-line rounded-md px-3 py-2 text-sm focus:outline-hidden focus:ring-2 focus:ring-accent"
                            value={confirmPassword}
                            onChange={e => setConfirmPassword(e.target.value)}
                            disabled={submitting}
                        />
                    </div>

                    {error && <p className="text-red-400 text-sm">{error}</p>}

                    <button
                        type="submit"
                        disabled={submitting}
                        className="w-full bg-accent hover:bg-accent text-on-accent font-medium py-2 rounded-md transition-colors disabled:opacity-50"
                    >
                        {submitting ? 'Creating account...' : 'Create Account'}
                    </button>
                </form>
            </div>
        </div>
    );
}
