import { useEffect, useState } from 'react';
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { Button, Input, Label } from '../../components/ui';
import { guestApi, guestError, guestToken } from './guestApi';

export function GuestShell({ children }: { children: React.ReactNode }) {
    return (
        <div className="min-h-screen bg-canvas text-fg flex flex-col">
            <header className="h-12 border-b border-line flex items-center px-4 text-sm font-semibold">
                sochub <span className="ml-2 text-fg-3 font-normal">· guest access</span>
            </header>
            <main className="flex-1 w-full max-w-3xl mx-auto px-4 py-8">{children}</main>
        </div>
    );
}

function Card({ title, children }: { title: string; children: React.ReactNode }) {
    return (
        <div className="max-w-sm mx-auto bg-panel border border-line rounded-lg p-6 space-y-4">
            <h1 className="text-lg font-semibold">{title}</h1>
            {children}
        </div>
    );
}

/** Single-use tokens ride in the URL fragment (never sent to the server, so never in access logs).
 * Legacy `/:token` path links keep working. Either way the token is dropped from the URL bar/history. */
function useUrlToken(basePath: string): string | undefined {
    const params = useParams();
    const [token] = useState(() => params.token ?? (window.location.hash.slice(1) || undefined));
    useEffect(() => { window.history.replaceState(null, '', basePath); }, [basePath]);
    return token;
}

export function GuestLinkPage() {
    const token = useUrlToken('/guest/link');
    const navigate = useNavigate();
    const [error, setError] = useState<string | null>(token ? null : 'This link is invalid or has expired.');
    useEffect(() => {
        if (!token) return;
        guestApi.post('/auth/link', { token })
            .then((r) => { guestToken.set(r.data.access_token); navigate('/guest', { replace: true }); })
            .catch((err) => {
                const d = err?.response?.data?.detail;
                if (d?.code === 'sso_required') navigate(`/guest/login?t=${encodeURIComponent(d.slug)}`, { replace: true });
                else if (d?.code === 'account_login_required') navigate('/guest/login?m=account', { replace: true });
                else setError(guestError(err, 'This link is invalid or has expired.'));
            });
    }, [token, navigate]);
    return <GuestShell><Card title="Opening your case…">
        {error ? <p role="alert" className="text-sm text-severity-critical">{error} Ask the SOC team to resend your invitation.</p>
            : <p className="font-mono text-xs text-muted">$ verifying link…</p>}
    </Card></GuestShell>;
}

export function GuestLoginPage() {
    const [params] = useSearchParams();
    const navigate = useNavigate();
    const slug = params.get('t');
    const [email, setEmail] = useState('');
    const [password, setPassword] = useState('');
    const [mfaToken, setMfaToken] = useState<string | null>(null);
    const [code, setCode] = useState('');
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState<string | null>(() => {
        const h = new URLSearchParams(window.location.hash.slice(1)).get('sso_error');
        if (h) window.history.replaceState(null, '', window.location.pathname + window.location.search);
        return h;
    });
    const m = params.get('m');
    const notice = m === 'account' ? 'Sign in with your guest password.' : m === 'reset' ? 'Password updated — sign in.' : null;

    const done = (tok: string) => { guestToken.set(tok); navigate('/guest', { replace: true }); };
    const submit = async (e: React.FormEvent) => {
        e.preventDefault(); setBusy(true); setError(null);
        try {
            if (mfaToken) {
                done((await guestApi.post('/auth/login/mfa', { mfa_token: mfaToken, code })).data.access_token);
            } else {
                const r = await guestApi.post('/auth/login', { email, password });
                if (r.data.mfa_required) setMfaToken(r.data.mfa_token); else done(r.data.access_token);
            }
        } catch (err) {
            if (mfaToken && (err as { response?: { status?: number } })?.response?.status === 401) { setMfaToken(null); setCode(''); }
            setError(guestError(err, 'Could not sign in.'));
        } finally { setBusy(false); }
    };
    return <GuestShell><Card title="Guest sign-in">
        {notice && <p role="status" className="text-sm text-fg-2">{notice}</p>}
        {slug && (
            <a href={`/api/v1/auth/saml/${encodeURIComponent(slug)}/login?guest=1`}
                className="flex h-10 items-center justify-center rounded-lg border border-line text-sm hover:bg-raised/60">
                Sign in with SSO
            </a>
        )}
        <form onSubmit={submit} className="space-y-3">
            {!mfaToken ? (<>
                <div><Label htmlFor="g-email">Email</Label>
                    <Input id="g-email" type="email" autoComplete="username" required value={email} onChange={(e) => setEmail(e.target.value)} /></div>
                <div><Label htmlFor="g-pw">Password</Label>
                    <Input id="g-pw" type="password" autoComplete="current-password" required value={password} onChange={(e) => setPassword(e.target.value)} /></div>
            </>) : (
                <div><Label htmlFor="g-code">Authentication code</Label>
                    <Input id="g-code" inputMode="numeric" autoComplete="one-time-code" required value={code} onChange={(e) => setCode(e.target.value)} /></div>
            )}
            {error && <p role="alert" className="text-sm text-severity-critical">{error}</p>}
            <Button type="submit" className="w-full" disabled={busy}>{mfaToken ? 'Verify' : 'Sign in'}</Button>
            {mfaToken && <button type="button" className="text-xs text-accent" onClick={() => { setMfaToken(null); setCode(''); setError(null); }}>Back</button>}
        </form>
        <p className="text-xs text-fg-3">No password yet? Open the link from your invitation email.
            {' '}<Link to="/guest/forgot" className="text-accent">Forgot password?</Link></p>
    </Card></GuestShell>;
}

export function GuestSsoPage() {
    const navigate = useNavigate();
    useEffect(() => {
        const tok = new URLSearchParams(window.location.hash.slice(1)).get('token');
        window.history.replaceState(null, '', '/guest/sso');  // drop the token from the URL bar/history
        if (tok) { guestToken.set(tok); navigate('/guest', { replace: true }); }
        else navigate('/guest/login', { replace: true });
    }, [navigate]);
    return <GuestShell><p className="font-mono text-xs text-muted">$ signing in…</p></GuestShell>;
}

export function GuestForgotPage() {
    const [email, setEmail] = useState('');
    const [sent, setSent] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const submit = async (e: React.FormEvent) => {
        e.preventDefault(); setError(null);
        try { await guestApi.post('/auth/forgot', { email }); setSent(true); }
        catch (err) { setError(guestError(err, 'Could not send the email.')); }
    };
    return <GuestShell><Card title="Reset your password">
        {sent ? <p className="text-sm">If a guest account exists for that address, we've emailed a reset link.</p> : (
            <form onSubmit={submit} className="space-y-3">
                <div><Label htmlFor="f-email">Email</Label>
                    <Input id="f-email" type="email" required value={email} onChange={(e) => setEmail(e.target.value)} /></div>
                {error && <p role="alert" className="text-sm text-severity-critical">{error}</p>}
                <Button type="submit" className="w-full">Send reset link</Button>
            </form>
        )}
    </Card></GuestShell>;
}

export function GuestResetPage() {
    const token = useUrlToken('/guest/reset');
    const navigate = useNavigate();
    const [password, setPassword] = useState('');
    const [error, setError] = useState<string | null>(token ? null : 'This reset link is invalid or has expired.');
    const submit = async (e: React.FormEvent) => {
        e.preventDefault(); setError(null);
        try { await guestApi.post('/auth/reset', { token, password }); navigate('/guest/login?m=reset', { replace: true }); }
        catch (err) { setError(guestError(err, 'This reset link is invalid or has expired.')); }
    };
    return <GuestShell><Card title="Choose a new password">
        <form onSubmit={submit} className="space-y-3">
            <div><Label htmlFor="r-pw">New password</Label>
                <Input id="r-pw" type="password" autoComplete="new-password" required value={password} onChange={(e) => setPassword(e.target.value)} /></div>
            {error && <p role="alert" className="text-sm text-severity-critical">{error}</p>}
            <Button type="submit" className="w-full" disabled={!token}>Save password</Button>
        </form>
    </Card></GuestShell>;
}

export function GuestRegisterPage() {
    const token = useUrlToken('/guest/register');
    const navigate = useNavigate();
    const [password, setPassword] = useState('');
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState<string | null>(token ? null : 'This link is invalid or has expired.');
    const submit = async (e: React.FormEvent) => {
        e.preventDefault(); setError(null); setBusy(true);
        try {
            const r = await guestApi.post('/auth/register/confirm', { token, password });
            guestToken.set(r.data.access_token);
            navigate('/guest', { replace: true });
        } catch (err) { setError(guestError(err, 'This link is invalid or has expired.')); }
        finally { setBusy(false); }
    };
    return <GuestShell><Card title="Choose a password">
        <form onSubmit={submit} className="space-y-3">
            <div><Label htmlFor="reg-pw">Password</Label>
                <Input id="reg-pw" type="password" autoComplete="new-password" required value={password} onChange={(e) => setPassword(e.target.value)} /></div>
            {error && <p role="alert" className="text-sm text-severity-critical">{error}</p>}
            <Button type="submit" className="w-full" disabled={busy || !token}>Create account</Button>
        </form>
    </Card></GuestShell>;
}
