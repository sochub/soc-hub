import { useEffect, useState } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { KeyRound, ArrowLeft } from 'lucide-react';
import { api, loginNotice, session, ssoLoginUrl } from '../../api/client';
import { Button, Input, Label } from '../../components/ui';
import MfaSetupDialog from '../profile/MfaSetupDialog';
import { mfaErrorMessage } from '../profile/mfaErrors';
import { takeReturnTo } from './returnTo';
import AuthLayout from './AuthLayout';
import { ssoErrorMessage } from './ssoErrors';

export default function Login() {
    const [email, setEmail] = useState('');
    const [password, setPassword] = useState('');
    const [error, setError] = useState('');
    const [isLoading, setIsLoading] = useState(false);
    const [ssoMode, setSsoMode] = useState(false);
    const [ssoSlug, setSsoSlug] = useState('');
    // Home-realm discovery: the email's domain belongs to an organization that signs in with SSO. The password
    // field is grayed out; break-glass accounts (super admin, admins with 2FA) can still choose a password.
    const [realm, setRealm] = useState<{ domain: string; slug: string } | null>(null);
    const [passwordFor, setPasswordFor] = useState(''); // the domain the user chose a password for
    const domain = /^[^@\s]+@([^@\s]+\.[^@\s]+)$/.exec(email.trim())?.[1]?.toLowerCase() ?? '';
    const usePassword = !!domain && passwordFor === domain;
    useEffect(() => {
        if (!domain) return;
        let live = true;
        const t = setTimeout(() => {
            api.post('/auth/sso/discover', { email: email.trim() })
                .then((r) => { if (live) setRealm(r.data.slug ? { domain, slug: r.data.slug } : null); })
                .catch(() => { if (live) setRealm(null); }); // no hint: the normal form still works
        }, 350);
        return () => { live = false; clearTimeout(t); };
    }, [domain]); // eslint-disable-line react-hooks/exhaustive-deps -- per domain, not per keystroke
    const ssoLocked = !!realm && realm.domain === domain && !usePassword;
    // Challenge token lives in component state only (never persisted).
    const [challenge, setChallenge] = useState<{ token: string; kind: 'code' | 'setup' } | null>(null);
    const [code, setCode] = useState('');
    const navigate = useNavigate();
    const location = useLocation();
    const [storedNotice] = useState(loginNotice.peek);
    const notice = (location.state as { notice?: string } | null)?.notice ?? storedNotice;
    useEffect(() => { loginNotice.clear(); }, []);

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
            navigate(takeReturnTo());
        } else if (hash.startsWith('#sso_error=')) {
            setError(ssoErrorMessage(hash.slice('#sso_error='.length)));
            window.history.replaceState(null, '', window.location.pathname);
        }
    }, [navigate]);

    const handleSso = (e: React.FormEvent) => {
        e.preventDefault();
        const slug = ssoSlug.trim().toLowerCase();
        if (slug) window.location.href = ssoLoginUrl(slug);
    };

    const handleLogin = async (e: React.FormEvent) => {
        e.preventDefault();
        if (ssoLocked && realm) { window.location.href = ssoLoginUrl(realm.slug); return; }
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
            navigate(takeReturnTo());
        } catch (err: any) {
            const d = err.response?.data?.detail;
            if (err.response?.status === 403 && d?.code === 'sso_required' && d.slug) {
                return; // the api interceptor sends the browser to the organization's SSO
            } else if (err.response?.status === 403 && d?.code === 'password_required') {
                setError(loginNotice.peek() ?? 'Sign in with your password to open this organization.');
                loginNotice.clear();
            } else if (err.response?.status === 401) {
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
            navigate(takeReturnTo());
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
        <AuthLayout
            title={challenge?.kind === 'code' ? 'Two-factor authentication' : challenge?.kind === 'setup' ? 'Set up two-factor authentication' : ssoMode ? 'Sign in with SSO' : 'Sign in'}
            description={challenge ? undefined : 'Enter your credentials to continue.'}>
            {notice && <p role="status" className="text-sm text-fg-2">{notice}</p>}
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
                            navigate(takeReturnTo());
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
                            value={ssoLocked ? '' : password}
                            placeholder={ssoLocked ? 'Your organization signs in with SSO' : undefined}
                            onChange={(e) => setPassword(e.target.value)}
                            disabled={isLoading || ssoLocked}
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
                        {ssoLocked ? <><KeyRound size={16} /> Continue with SSO</> : isLoading ? 'Signing in…' : 'Sign in'}
                    </Button>
                    {realm && realm.domain === domain && (
                        <p className="text-xs text-fg-3 text-center">
                            {usePassword ? 'Password sign-in is for break-glass accounts. ' : <>Accounts at <strong>{realm.domain}</strong> sign in with SSO. </>}
                            <button type="button" onClick={() => { setPasswordFor(usePassword ? '' : domain); setError(''); }}
                                className="text-accent hover:underline">
                                {usePassword ? 'Use SSO instead' : 'Sign in with a password instead'}
                            </button>
                        </p>
                    )}

                    <div className="relative py-1">
                        <div className="absolute inset-0 flex items-center"><span className="w-full border-t border-line" /></div>
                        <div className="relative flex justify-center"><span className="bg-panel px-3 text-xs text-fg-3">or</span></div>
                    </div>

                    <Button type="button" variant="outline" size="lg" className="w-full"
                        onClick={() => { setSsoMode(true); setError(''); }}>
                        <KeyRound size={16} /> Sign in with SSO
                    </Button>
                </form>
            )}
        </AuthLayout>
    );
}
