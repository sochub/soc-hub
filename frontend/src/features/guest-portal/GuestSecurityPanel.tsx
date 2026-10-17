import { useState } from 'react';
import QRCode from 'qrcode';
import { useQuery } from '@tanstack/react-query';
import { Button, Input, Label } from '../../components/ui';
import { guestApi, guestError, guestToken } from './guestApi';

interface GuestMe { email: string; has_account: boolean; mfa_enabled: boolean; amr: string }
interface Setup { otpauth_uri: string; secret: string; qr: string | null }

/** Two-factor setup for guests who signed in with their password (the only sessions that own an account). */
export default function GuestSecurityPanel() {
    const { data: me } = useQuery({
        queryKey: ['guest-me'], queryFn: async () => (await guestApi.get('/me')).data as GuestMe,
        enabled: !!guestToken.get(),
    });
    const [open, setOpen] = useState(false);
    const [setup, setSetup] = useState<Setup | null>(null);
    const [password, setPassword] = useState('');
    const [code, setCode] = useState('');
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [notice, setNotice] = useState<string | null>(null);

    if (!me || me.amr !== 'password') return null;

    const reset = () => { setSetup(null); setPassword(''); setCode(''); setError(null); };
    const run = async (fn: () => Promise<void>, fallback: string) => {
        setBusy(true); setError(null); setNotice(null);
        try { await fn(); } catch (err) { setError(guestError(err, fallback)); } finally { setBusy(false); }
    };
    const start = () => run(async () => {
        const { data } = await guestApi.post('/auth/mfa/setup');
        let qr: string | null = null;
        try { qr = await QRCode.toDataURL(data.otpauth_uri as string, { margin: 1, width: 176 }); } catch { /* text fallback */ }
        setSetup({ otpauth_uri: data.otpauth_uri, secret: data.secret, qr });
    }, 'Could not start two-factor setup.');
    const enable = (e: React.FormEvent) => { e.preventDefault(); void run(async () => {
        const { data } = await guestApi.post('/auth/mfa/enable', { password, code });
        reset(); guestToken.set(data.access_token); setNotice('Two-factor authentication is on.');
    }, 'Could not enable two-factor authentication.'); };
    const disable = (e: React.FormEvent) => { e.preventDefault(); void run(async () => {
        const { data } = await guestApi.post('/auth/mfa/disable', { password, code });
        reset(); guestToken.set(data.access_token); setNotice('Two-factor authentication is off.');
    }, 'Could not turn off two-factor authentication.'); };

    const creds = (
        <>
            <div><Label htmlFor="g-mfa-pw">Current password</Label>
                <Input id="g-mfa-pw" type="password" autoComplete="current-password" required value={password}
                    onChange={(e) => setPassword(e.target.value)} /></div>
            <div><Label htmlFor="g-mfa-code">Code from your authenticator app</Label>
                <Input id="g-mfa-code" inputMode="numeric" autoComplete="one-time-code" required maxLength={8} value={code}
                    onChange={(e) => setCode(e.target.value.replace(/\s/g, ''))} /></div>
        </>
    );

    return (
        <section className="mb-6 border border-line bg-panel rounded-lg p-3 text-sm space-y-3">
            <div className="flex items-center justify-between gap-2">
                <span><span className="font-medium">Security</span>
                    <span className="text-fg-3"> · two-factor authentication {me.mfa_enabled ? 'on' : 'off'}</span></span>
                <button type="button" className="text-accent text-xs" onClick={() => { setOpen(!open); reset(); setNotice(null); }}>
                    {open ? 'Close' : 'Manage'}
                </button>
            </div>
            {notice && <p role="status" className="text-fg-2">{notice}</p>}
            {open && (me.mfa_enabled ? (
                <form onSubmit={disable} className="space-y-2">
                    <p className="text-fg-3">To turn two-factor authentication off, confirm your password and a current code.</p>
                    {creds}
                    <Button type="submit" size="sm" variant="danger" disabled={busy}>Turn off</Button>
                </form>
            ) : !setup ? (
                <div className="space-y-2">
                    <p className="text-fg-3">Add a code from an authenticator app to every sign-in.</p>
                    <Button type="button" size="sm" onClick={() => void start()} disabled={busy}>Set up</Button>
                </div>
            ) : (
                <form onSubmit={enable} className="space-y-2">
                    <p className="text-fg-3">Scan this in your authenticator app, or enter the key by hand.</p>
                    {setup.qr && <img src={setup.qr} alt="Two-factor QR code" width={176} height={176} className="bg-white p-1 rounded" />}
                    <p className="font-mono text-xs break-all select-all">{setup.secret}</p>
                    <details className="text-xs text-fg-3"><summary>Setup URI</summary>
                        <p className="font-mono break-all select-all">{setup.otpauth_uri}</p></details>
                    {creds}
                    <Button type="submit" size="sm" disabled={busy}>Turn on</Button>
                </form>
            ))}
            {error && <p role="alert" className="text-severity-critical">{error}</p>}
        </section>
    );
}
