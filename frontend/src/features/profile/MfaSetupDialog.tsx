import { useEffect, useState } from 'react';
import QRCode from 'qrcode';
import { Check, Copy, Loader2 } from 'lucide-react';
import { api } from '../../api/client';
import Modal, { btnPrimary, btnSecondary, modalInput, modalLabel } from '../../components/layout/Modal';
import { setSessionToken } from '../../api/queryClient';
import { mfaErrorMessage } from './mfaErrors';

interface Props {
    open: boolean;
    onClose: () => void;
    onEnabled: (token: string) => void;
    /** Forced setup during login: sent as the Bearer token instead of the stored one. */
    authToken?: string;
    /** Ask for the current password (enabling from a normal session on a password account). */
    requirePassword?: boolean;
    /** Forced setup only: the login challenge expired or was already used (401). */
    onExpired?: () => void;
}

const isUnauthorized = (err: unknown) => (err as { response?: { status?: number } })?.response?.status === 401;

export default function MfaSetupDialog({ open, onClose, onEnabled, authToken, requirePassword, onExpired }: Props) {
    const [qr, setQr] = useState<string | null>(null);
    const [pw, setPw] = useState('');
    const [secret, setSecret] = useState<string | null>(null);
    const [code, setCode] = useState('');
    const [error, setError] = useState<string | null>(null);
    const [loading, setLoading] = useState(false);
    const [busy, setBusy] = useState(false);
    const [copied, setCopied] = useState(false);

    const headers = authToken ? { Authorization: `Bearer ${authToken}` } : undefined;

    useEffect(() => {
        if (!open) return;
        let cancelled = false;
        setLoading(true);
        (async () => {
            try {
                const { data } = await api.post('/users/me/mfa/setup', undefined, { headers });
                const img = await QRCode.toDataURL(data.otpauth_uri as string, { margin: 1, width: 192 });
                if (cancelled) return;
                setSecret(data.secret as string);
                setQr(img);
            } catch (err) {
                if (cancelled) return;
                if (authToken && isUnauthorized(err) && onExpired) { onExpired(); return; }
                setError(mfaErrorMessage(err, 'Could not start two-factor setup.'));
            } finally {
                if (!cancelled) setLoading(false);
            }
        })();
        return () => {
            cancelled = true;
            // Drop all sensitive state when closed or unmounted.
            setQr(null); setSecret(null); setCode(''); setPw(''); setError(null); setCopied(false);
        };
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [open, authToken]);

    const copy = async () => {
        if (!secret) return;
        try {
            await navigator.clipboard.writeText(secret);
            setCopied(true);
            window.setTimeout(() => setCopied(false), 1500);
        } catch {
            setError('Could not copy. Select the key and copy it manually.');
        }
    };

    const confirm = async () => {
        setBusy(true);
        setError(null);
        try {
            const body = requirePassword ? { code, current_password: pw } : { code };
            const { data } = await api.post('/users/me/mfa/enable', body, { headers });
            const token = data.access_token as string;
            if (!authToken) setSessionToken(token);
            onEnabled(token);
        } catch (err) {
            if (authToken && isUnauthorized(err) && onExpired) { onExpired(); return; }
            setError(mfaErrorMessage(err));
            setCode('');
        } finally {
            setBusy(false);
        }
    };

    return (
        <Modal open={open} onClose={onClose} title="Turn on two-factor authentication" size="md"
            footer={<>
                <button type="button" className={btnSecondary} onClick={onClose}>Cancel</button>
                <button type="submit" form="mfa-setup-form" className={btnPrimary} disabled={busy || loading || !secret || code.length !== 6 || (!!requirePassword && !pw)}>
                    {busy && <Loader2 size={14} className="animate-spin" />} Confirm
                </button>
            </>}>
            <form id="mfa-setup-form" className="space-y-4" onSubmit={(e) => { e.preventDefault(); void confirm(); }}>
                <p className="text-sm text-zinc-600">
                    Scan the QR code with an authenticator app, then enter the 6-digit code it shows.
                </p>
                {loading && <p className="font-mono text-xs text-zinc-400">$ generating key…</p>}
                {qr && <img src={qr} alt="Scan with your authenticator app" width={192} height={192} className="border border-zinc-200" />}
                {secret && (
                    <div>
                        <span className={modalLabel}>Can't scan? Enter this key manually</span>
                        <div className="flex items-center gap-2">
                            <code className="font-mono text-sm bg-zinc-50 border border-zinc-200 px-2 py-1.5 break-all select-all">{secret}</code>
                            <button type="button" className={btnSecondary} onClick={() => void copy()} aria-label="Copy key">
                                {copied ? <Check size={14} /> : <Copy size={14} />}
                            </button>
                        </div>
                    </div>
                )}
                {requirePassword && (
                    <div>
                        <label htmlFor="mfa-pw" className={modalLabel}>Current password</label>
                        <input id="mfa-pw" type="password" autoComplete="current-password" className={modalInput}
                            value={pw} onChange={(e) => setPw(e.target.value)} />
                    </div>
                )}
                <div>
                    <label htmlFor="mfa-code" className={modalLabel}>6-digit code</label>
                    <input id="mfa-code" className={`${modalInput} font-mono tracking-widest`} inputMode="numeric"
                        autoComplete="one-time-code" maxLength={6} value={code}
                        onChange={(e) => setCode(e.target.value.replace(/\D/g, '').slice(0, 6))} />
                </div>
                {error && <p role="alert" className="text-sm text-red-600">{error}</p>}
            </form>
        </Modal>
    );
}
