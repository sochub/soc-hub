import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { KeyRound, ArrowLeft } from 'lucide-react';
import { api, session } from '../../api/client';
import { Button, Input, Label } from '../../components/ui';
import MfaSetupDialog from '../profile/MfaSetupDialog';
import { mfaErrorMessage } from '../profile/mfaErrors';

export default function Login() {
    const [email, setEmail] = useState('');
    const [password, setPassword] = useState('');
    const [error, setError] = useState('');
    const [isLoading, setIsLoading] = useState(false);
    const [ssoMode, setSsoMode] = useState(false);
    const [ssoSlug, setSsoSlug] = useState('');
    // Challenge token lives in component state only (never persisted).
    const [challenge, setChallenge] = useState<{ token: string; kind: 'code' | 'setup' } | null>(null);
    const [code, setCode] = useState('');
    const navigate = useNavigate();

    const resetChallenge = (msg = '') => {
        setChallenge(null);
        setCode('');
        setPassword('');
        setError(msg);
    };

    // SAML ACS bounces back here after setting the session cookie (#sso=ok), or with an error in the fragment.
    useEffect(() => {
        const hash = window.location.hash;
        if (hash === '#sso=ok') {
            window.history.replaceState(null, '', window.location.pathname);
            session.mark();
            navigate('/');
        } else if (hash.startsWith('#sso_error=')) {
            setError(decodeURIComponent(hash.slice('#sso_error='.length)));
            window.history.replaceState(null, '', window.location.pathname);
        }
    }, [navigate]);

    const handleSso = (e: React.FormEvent) => {
        e.preventDefault();
        const slug = ssoSlug.trim().toLowerCase();
        if (slug) window.location.href = `/api/v1/auth/saml/${encodeURIComponent(slug)}/login`;
    };

    const handleLogin = async (e: React.FormEvent) => {
        e.preventDefault();
        setError('');
        setIsLoading(true);

        try {
            const response = await api.post('/auth/login/access-token',
                `grant_type=password&username=${encodeURIComponent(email)}&password=${encodeURIComponent(password)}`,
                { headers: { 'Content-Type': 'application/x-www-form-urlencoded' } }
            );

            if (response.data.mfa_required || response.data.mfa_setup_required) {
                setChallenge({ token: response.data.mfa_token, kind: response.data.mfa_required ? 'code' : 'setup' });
                setCode('');
                setPassword(''); // not needed past this step; don't keep it in memory
                return;
            }
            session.mark();
            navigate('/');
        } catch (err: any) {
            if (err.response?.status === 401) {
                setError('Invalid email or password.');
            } else if (err.response?.status === 429) {
                setError(mfaErrorMessage(err));
            } else if (!err.response) {
                setError('Unable to connect to server. Please try again later.');
            } else {
                setError('An unexpected error occurred. Please try again.');
            }
        } finally {
            setIsLoading(false);
        }
    };

    const handleCode = async (e: React.FormEvent) => {
        e.preventDefault();
        if (!challenge) return;
        setError('');
        setIsLoading(true);
        try {
            await api.post('/auth/login/mfa', { mfa_token: challenge.token, code });
            session.mark();
            navigate('/');
        } catch (err) {
            if ((err as { response?: { status?: number } }).response?.status === 401) {
                resetChallenge('Your sign-in expired — please sign in again');
            } else {
                setError(mfaErrorMessage(err));
                setCode('');
            }
        } finally {
            setIsLoading(false);
        }
    };

    return (
        <div className="min-h-screen grid lg:grid-cols-2 bg-canvas text-fg font-sans">
            <div className="flex items-center justify-center p-6 sm:p-8 bg-panel lg:border-r border-line">
                <div className="w-full max-w-sm space-y-8">
                    <div className="space-y-2">
                        <div className="flex items-center gap-2.5 mb-6">
                            <div className="w-9 h-9 flex items-center justify-center bg-brand-gradient rounded-lg font-mono text-xs font-bold">
                                SH
                            </div>
                            <span className="font-display text-xl font-semibold tracking-tight">sochub</span>
                        </div>
                        <h1 className="text-3xl font-bold tracking-tight">Sign in</h1>
                        <p className="text-fg-2">Enter your credentials to access the SOC hub.</p>
                    </div>

                    {challenge?.kind === 'code' ? (
                        <form onSubmit={handleCode} className="space-y-4" noValidate>
                            <div className="space-y-2">
                                <Label htmlFor="mfa-code">Authentication code</Label>
                                <Input
                                    id="mfa-code"
                                    name="mfa-code"
                                    type="text"
                                    inputMode="numeric"
                                    autoComplete="one-time-code"
                                    maxLength={6}
                                    required
                                    autoFocus
                                    placeholder="123456"
                                    value={code}
                                    onChange={(e) => setCode(e.target.value.replace(/\D/g, ''))}
                                    disabled={isLoading}
                                    invalid={!!error}
                                    aria-describedby={error ? 'login-error' : undefined}
                                />
                                <p className="text-xs text-fg-3">Enter the 6-digit code from your authenticator app.</p>
                            </div>
                            {error && (
                                <p id="login-error" role="alert" className="text-severity-critical text-sm">{error}</p>
                            )}
                            <Button type="submit" size="lg" disabled={isLoading || code.length !== 6} className="w-full">
                                {isLoading ? 'Verifying…' : 'Verify'}
                            </Button>
                            <button type="button" onClick={() => resetChallenge()}
                                className="w-full inline-flex items-center justify-center gap-1.5 text-sm text-fg-2 hover:text-fg transition-colors">
                                <ArrowLeft size={14} /> Back
                            </button>
                        </form>
                    ) : challenge?.kind === 'setup' ? (
                        <div className="space-y-4">
                            <p className="text-sm text-fg-2">
                                Your organization requires two-factor authentication. Set it up to continue.
                            </p>
                            <MfaSetupDialog
                                open
                                authToken={challenge.token}
                                onClose={() => resetChallenge()}
                                onExpired={() => resetChallenge('Your sign-in expired — please sign in again')}
                                onEnabled={() => {
                                    session.mark();
                                    setChallenge(null);
                                    navigate('/');
                                }}
                            />
                            <button type="button" onClick={() => resetChallenge()}
                                className="w-full inline-flex items-center justify-center gap-1.5 text-sm text-fg-2 hover:text-fg transition-colors">
                                <ArrowLeft size={14} /> Back
                            </button>
                        </div>
                    ) : ssoMode ? (
                        <form onSubmit={handleSso} className="space-y-4" noValidate>
                            <div className="space-y-2">
                                <Label htmlFor="sso-slug">Organization slug</Label>
                                <Input
                                    id="sso-slug"
                                    name="sso-slug"
                                    type="text"
                                    autoComplete="organization"
                                    required
                                    placeholder="e.g. sochub"
                                    value={ssoSlug}
                                    onChange={(e) => setSsoSlug(e.target.value)}
                                />
                                <p className="text-xs text-fg-3">Ask your admin if you don't know your organization's slug.</p>
                            </div>

                            {error && (
                                <p role="alert" className="text-severity-critical text-sm">{error}</p>
                            )}

                            <Button type="submit" size="lg" disabled={!ssoSlug.trim()} className="w-full">
                                <KeyRound size={16} /> Continue with SSO
                            </Button>
                            <button type="button" onClick={() => { setSsoMode(false); setError(''); }}
                                className="w-full inline-flex items-center justify-center gap-1.5 text-sm text-fg-2 hover:text-fg transition-colors">
                                <ArrowLeft size={14} /> Back to password sign-in
                            </button>
                        </form>
                    ) : (
                        <form onSubmit={handleLogin} className="space-y-4" noValidate>
                            <div className="space-y-2">
                                <Label htmlFor="email">Email</Label>
                                <Input
                                    id="email"
                                    name="email"
                                    type="email"
                                    autoComplete="username"
                                    required
                                    placeholder="name@example.com"
                                    value={email}
                                    onChange={(e) => setEmail(e.target.value)}
                                    disabled={isLoading}
                                    invalid={!!error}
                                />
                            </div>
                            <div className="space-y-2">
                                <Label htmlFor="password">Password</Label>
                                <Input
                                    id="password"
                                    name="password"
                                    type="password"
                                    autoComplete="current-password"
                                    required
                                    value={password}
                                    onChange={(e) => setPassword(e.target.value)}
                                    disabled={isLoading}
                                    invalid={!!error}
                                    aria-describedby={error ? 'login-error' : undefined}
                                />
                            </div>

                            {error && (
                                <p id="login-error" role="alert" className="text-severity-critical text-sm">
                                    {error}
                                </p>
                            )}

                            <Button type="submit" size="lg" disabled={isLoading} className="w-full">
                                {isLoading ? 'Signing in…' : 'Sign In'}
                            </Button>

                            <div className="relative py-1">
                                <div className="absolute inset-0 flex items-center"><span className="w-full border-t border-line" /></div>
                                <div className="relative flex justify-center"><span className="bg-panel px-3 text-xs text-fg-3 uppercase tracking-wider">or</span></div>
                            </div>

                            <Button type="button" variant="outline" size="lg" className="w-full"
                                onClick={() => { setSsoMode(true); setError(''); }}>
                                <KeyRound size={16} /> Sign in with SSO
                            </Button>
                        </form>
                    )}
                </div>
            </div>
            <div className="hidden lg:flex items-center justify-center bg-canvas relative overflow-hidden">
                <div className="absolute inset-0 bg-linear-to-tr from-accent/20 to-raised/40" />
                <div className="relative z-10 text-center space-y-4 p-8">
                    <h2 className="text-4xl font-bold text-brand-gradient">
                        Secure Operations
                    </h2>
                    <p className="text-fg-2 max-w-md mx-auto">
                        Advanced case management powered by AI for modern security teams.
                    </p>
                </div>
            </div>
        </div>
    );
}
