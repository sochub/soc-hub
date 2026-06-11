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
            <div className="min-h-screen flex items-center justify-center bg-slate-950 text-slate-100">
                <div className="animate-spin rounded-full h-8 w-8 border-t-2 border-b-2 border-blue-500" />
            </div>
        );
    }

    if (!validation?.valid) {
        return (
            <div className="min-h-screen flex items-center justify-center bg-slate-950 text-slate-100">
                <div className="max-w-md w-full p-8 bg-slate-900 rounded-xl border border-slate-800 text-center space-y-4">
                    <h2 className="text-2xl font-bold">Invalid Invitation</h2>
                    <p className="text-slate-400">This invitation link is invalid, expired, or has already been used.</p>
                    <button
                        onClick={() => navigate('/login')}
                        className="px-6 py-2 bg-blue-600 hover:bg-blue-500 text-white rounded-md transition-colors"
                    >
                        Go to Login
                    </button>
                </div>
            </div>
        );
    }

    if (success) {
        return (
            <div className="min-h-screen flex items-center justify-center bg-slate-950 text-slate-100">
                <div className="max-w-md w-full p-8 bg-slate-900 rounded-xl border border-slate-800 text-center space-y-4">
                    <h2 className="text-2xl font-bold text-green-400">Account Created!</h2>
                    <p className="text-slate-400">Redirecting to login...</p>
                </div>
            </div>
        );
    }

    return (
        <div className="min-h-screen flex items-center justify-center bg-slate-950 text-slate-100 p-4">
            <div className="max-w-md w-full p-8 bg-slate-900 rounded-xl border border-slate-800 space-y-6">
                <div className="space-y-2">
                    <h2 className="text-2xl font-bold">Join {validation.tenant_name}</h2>
                    <p className="text-slate-400 text-sm">
                        You've been invited as <span className="text-slate-200 font-medium">{validation.role}</span> to join {validation.tenant_name} on SOC Hub.
                    </p>
                    <p className="text-slate-500 text-xs">{validation.email}</p>
                </div>

                <form onSubmit={handleSubmit} className="space-y-4">
                    <div className="space-y-2">
                        <label className="text-sm font-medium text-slate-300">Full Name</label>
                        <input
                            type="text"
                            required
                            className="w-full bg-slate-950 border border-slate-800 rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                            value={fullName}
                            onChange={e => setFullName(e.target.value)}
                            disabled={submitting}
                        />
                    </div>
                    <div className="space-y-2">
                        <label className="text-sm font-medium text-slate-300">Password</label>
                        <input
                            type="password"
                            required
                            className="w-full bg-slate-950 border border-slate-800 rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                            value={password}
                            onChange={e => setPassword(e.target.value)}
                            disabled={submitting}
                        />
                    </div>
                    <div className="space-y-2">
                        <label className="text-sm font-medium text-slate-300">Confirm Password</label>
                        <input
                            type="password"
                            required
                            className="w-full bg-slate-950 border border-slate-800 rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                            value={confirmPassword}
                            onChange={e => setConfirmPassword(e.target.value)}
                            disabled={submitting}
                        />
                    </div>

                    {error && <p className="text-red-400 text-sm">{error}</p>}

                    <button
                        type="submit"
                        disabled={submitting}
                        className="w-full bg-blue-600 hover:bg-blue-500 text-white font-medium py-2 rounded-md transition-colors disabled:opacity-50"
                    >
                        {submitting ? 'Creating account...' : 'Create Account'}
                    </button>
                </form>
            </div>
        </div>
    );
}
