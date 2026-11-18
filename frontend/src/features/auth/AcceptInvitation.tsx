import { useState, useEffect } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { useQueryClient } from '@tanstack/react-query';
import { api, session } from '../../api/client';
import type { InvitationValidation } from '../../types';
import { saveReturnTo } from './returnTo';
import AuthLayout from './AuthLayout';
import { Button, Input, Label, Skeleton } from '../../components/ui';

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
    // Existing accounts accept signed in as the invited account; null = not signed in.
    const [meEmail, setMeEmail] = useState<string | null>(null);
    const queryClient = useQueryClient();

    useEffect(() => {
        if (!token) return;
        (async () => {
            try {
                const v: InvitationValidation = (await api.get(`/invitations/validate/${token}`)).data;
                setValidation(v);
                if (v.valid && v.existing_account && session.active()) {
                    try { setMeEmail((await api.get('/users/me')).data.email ?? null); } catch { setMeEmail(null); }
                }
            } catch {
                setValidation({ email: '', tenant_name: '', role: '', valid: false });
            } finally {
                setLoading(false);
            }
        })();
    }, [token]);

    const signInToAccept = async (signOutFirst: boolean) => {
        if (signOutFirst) {
            try { await api.post('/auth/logout'); } catch { /* session may already be gone */ }
            session.clear();
        }
        saveReturnTo(`/invite/${token}`);
        navigate('/login');
    };

    const handleJoin = async () => {
        setError('');
        setSubmitting(true);
        try {
            await api.post('/invitations/accept-existing', { token });
            await queryClient.invalidateQueries();
            navigate('/');
        } catch (err) {
            const detail = (err as { response?: { data?: { detail?: unknown } } }).response?.data?.detail;
            setError(typeof detail === 'string' ? detail : 'Failed to accept invitation.');
        } finally {
            setSubmitting(false);
        }
    };

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
        return <AuthLayout title="Checking your invitation" wide><Skeleton rows={3} /></AuthLayout>;
    }

    if (!validation?.valid) {
        return (
            <AuthLayout title="Invalid invitation" wide
                description="This invitation link is invalid, expired, or has already been used.">
                <Button size="lg" className="w-full" onClick={() => navigate('/login')}>Go to sign in</Button>
            </AuthLayout>
        );
    }

    if (success) {
        return (
            <AuthLayout title="Account created" wide>
                <p role="status" className="text-sm text-fg-2">Redirecting to sign in…</p>
            </AuthLayout>
        );
    }

    const intro = (
        <>
            <p>You've been invited as <span className="text-fg font-medium">{validation.role}</span> to join {validation.tenant_name} on sochub.</p>
            <p className="mt-1 text-xs">{validation.email}</p>
        </>
    );

    if (validation.existing_account) {
        const sameAccount = meEmail !== null && meEmail.toLowerCase() === validation.email.toLowerCase();
        return (
            <AuthLayout title={`Join ${validation.tenant_name}`} description={intro} wide>
                {sameAccount ? (
                    <Button size="lg" className="w-full" onClick={handleJoin} disabled={submitting}>
                        {submitting ? 'Joining…' : `Join ${validation.tenant_name}`}
                    </Button>
                ) : (
                    <div className="space-y-4">
                        <p className="text-fg-2 text-sm">
                            {meEmail
                                ? `You're signed in as ${meEmail}. Sign in as ${validation.email} to accept this invitation.`
                                : 'Sign in to your account to accept this invitation.'}
                        </p>
                        <Button size="lg" className="w-full" onClick={() => signInToAccept(meEmail !== null)}>
                            {meEmail ? 'Sign in as another account' : 'Sign in'}
                        </Button>
                    </div>
                )}
                {error && <p role="alert" className="text-severity-critical text-sm">{error}</p>}
            </AuthLayout>
        );
    }

    return (
        <AuthLayout title={`Join ${validation.tenant_name}`} description={intro} wide>
            <form onSubmit={handleSubmit} className="space-y-4">
                <div>
                    <Label htmlFor="invite-name">Full name</Label>
                    <Input id="invite-name" type="text" required autoComplete="name"
                        value={fullName} onChange={e => setFullName(e.target.value)} disabled={submitting} />
                </div>
                <div>
                    <Label htmlFor="invite-password">Password</Label>
                    <Input id="invite-password" type="password" required autoComplete="new-password"
                        value={password} onChange={e => setPassword(e.target.value)} disabled={submitting} />
                </div>
                <div>
                    <Label htmlFor="invite-password-again">Confirm password</Label>
                    <Input id="invite-password-again" type="password" required autoComplete="new-password"
                        value={confirmPassword} onChange={e => setConfirmPassword(e.target.value)} disabled={submitting} />
                </div>

                {error && <p role="alert" className="text-severity-critical text-sm">{error}</p>}

                <Button type="submit" size="lg" className="w-full" disabled={submitting}>
                    {submitting ? 'Creating account…' : 'Create account'}
                </Button>
            </form>
        </AuthLayout>
    );
}
