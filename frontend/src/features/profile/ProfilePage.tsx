import { useEffect, useMemo, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Loader2, ShieldCheck, ShieldOff } from 'lucide-react';
import { api } from '../../api/client';
import { setSessionToken } from '../../api/queryClient';
import PageContainer from '../../components/layout/PageContainer';
import Modal, { btnDanger, btnPrimary, btnSecondary, modalInput, modalLabel } from '../../components/layout/Modal';
import Avatar from '../../components/Avatar';
import type { User } from '../../types';
import MfaSetupDialog from './MfaSetupDialog';
import { mfaErrorMessage } from './mfaErrors';

const card = 'bg-white border border-zinc-200';
const cardHead = 'px-4 h-11 flex items-center border-b border-zinc-200 text-sm font-semibold text-zinc-900';
const MAX_AVATAR = 2 * 1024 * 1024;

function apiError(err: unknown, fallback: string): string {
    const d = (err as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
    if (typeof d === 'string') return d;
    if (Array.isArray(d) && d[0]?.msg) return String(d[0].msg).replace(/^Value error, /, '');
    return fallback;
}

function ProfileCard({ me }: { me: User }) {
    const qc = useQueryClient();
    const fileRef = useRef<HTMLInputElement>(null);
    const [name, setName] = useState(me.full_name ?? '');
    const [title, setTitle] = useState(me.job_title ?? '');
    const [tz, setTz] = useState(me.timezone ?? '');
    const [tzFilter, setTzFilter] = useState('');
    const [preview, setPreview] = useState<string | null>(null);
    const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);

    useEffect(() => () => { if (preview) URL.revokeObjectURL(preview); }, [preview]);

    const zones = useMemo(() => {
        try { return Intl.supportedValuesOf('timeZone'); } catch { return ['UTC']; }
    }, []);
    const shown = useMemo(() => {
        const f = tzFilter.trim().toLowerCase();
        const list = f ? zones.filter((z) => z.toLowerCase().includes(f)) : zones;
        return tz && !list.includes(tz) ? [tz, ...list] : list;
    }, [zones, tzFilter, tz]);

    const save = useMutation({
        mutationFn: async () => (await api.put('/users/me', { full_name: name, job_title: title, timezone: tz })).data,
        onSuccess: () => { setMsg({ ok: true, text: 'Profile saved.' }); void qc.invalidateQueries({ queryKey: ['currentUser'] }); },
        onError: (e) => setMsg({ ok: false, text: apiError(e, 'Could not save profile.') }),
    });

    const upload = useMutation({
        mutationFn: async (file: File) => {
            const fd = new FormData();
            fd.append('file', file);
            return (await api.put('/users/me/avatar', fd, { headers: { 'Content-Type': 'multipart/form-data' } })).data;
        },
        onSuccess: () => { setPreview(null); void qc.invalidateQueries({ queryKey: ['currentUser'] }); },
        onError: (e) => { setPreview(null); setMsg({ ok: false, text: apiError(e, 'Could not upload image.') }); },
    });

    const remove = useMutation({
        mutationFn: async () => { await api.delete('/users/me/avatar'); },
        onSuccess: () => void qc.invalidateQueries({ queryKey: ['currentUser'] }),
        onError: (e) => setMsg({ ok: false, text: apiError(e, 'Could not remove image.') }),
    });

    const onFile = (file?: File) => {
        if (!file) return;
        if (file.size > MAX_AVATAR) { setMsg({ ok: false, text: 'Image must be PNG, JPEG or WebP up to 2 MB' }); return; }
        setMsg(null);
        setPreview(URL.createObjectURL(file));
        upload.mutate(file);
        if (fileRef.current) fileRef.current.value = '';
    };

    return (
        <section className={card}>
            <h2 className={cardHead}>Profile</h2>
            <div className="p-4 space-y-5">
                <div className="flex items-center gap-4">
                    {preview
                        ? <img src={preview} alt="" className="w-16 h-16 object-cover" />
                        : <Avatar userId={me.id} name={me.full_name || me.email} hasAvatar={me.has_avatar} size={64} />}
                    <div className="flex flex-wrap gap-2">
                        <input ref={fileRef} type="file" accept="image/png,image/jpeg,image/webp" className="hidden"
                            aria-label="Choose avatar image" onChange={(e) => onFile(e.target.files?.[0])} />
                        <button type="button" className={btnSecondary} disabled={upload.isPending} onClick={() => fileRef.current?.click()}>
                            {upload.isPending && <Loader2 size={14} className="animate-spin" />} Upload photo
                        </button>
                        {me.has_avatar && (
                            <button type="button" className={btnSecondary} disabled={remove.isPending} onClick={() => remove.mutate()}>Remove</button>
                        )}
                    </div>
                </div>
                <div className="grid sm:grid-cols-2 gap-4">
                    <div>
                        <label htmlFor="pf-name" className={modalLabel}>Name</label>
                        <input id="pf-name" className={modalInput} value={name} maxLength={200} onChange={(e) => setName(e.target.value)} />
                    </div>
                    <div>
                        <label htmlFor="pf-title" className={modalLabel}>Job title</label>
                        <input id="pf-title" className={modalInput} value={title} maxLength={100} onChange={(e) => setTitle(e.target.value)} />
                    </div>
                    <div>
                        <label htmlFor="pf-email" className={modalLabel}>Email</label>
                        <input id="pf-email" className={`${modalInput} bg-zinc-50 text-zinc-500`} value={me.email} readOnly />
                    </div>
                    <div>
                        <label htmlFor="pf-tzf" className={modalLabel}>Timezone</label>
                        <input id="pf-tzf" className={`${modalInput} mb-1.5`} placeholder="Filter timezones…" value={tzFilter}
                            onChange={(e) => setTzFilter(e.target.value)} />
                        <select aria-label="Timezone" className={modalInput} value={tz} onChange={(e) => setTz(e.target.value)}>
                            <option value="">Browser default</option>
                            {shown.map((z) => <option key={z} value={z}>{z}</option>)}
                        </select>
                    </div>
                </div>
                <div className="flex items-center gap-3">
                    <button type="button" className={btnPrimary} disabled={save.isPending || !name.trim()} onClick={() => { setMsg(null); save.mutate(); }}>
                        {save.isPending && <Loader2 size={14} className="animate-spin" />} Save changes
                    </button>
                    {msg && <p role={msg.ok ? 'status' : 'alert'} className={`text-sm ${msg.ok ? 'text-emerald-700' : 'text-red-600'}`}>{msg.text}</p>}
                </div>
            </div>
        </section>
    );
}

function PasswordCard({ me }: { me: User }) {
    const [cur, setCur] = useState('');
    const [next, setNext] = useState('');
    const [again, setAgain] = useState('');
    const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);

    const change = useMutation({
        mutationFn: async () => (await api.post('/users/me/password', { current_password: cur, new_password: next })).data as { access_token: string },
        onSuccess: (d) => {
            setSessionToken(d.access_token);
            setCur(''); setNext(''); setAgain('');
            setMsg({ ok: true, text: 'Password changed. Other sessions were signed out.' });
        },
        onError: (e) => setMsg({ ok: false, text: mfaErrorMessage(e, apiError(e, 'Could not change password.')) }),
    });

    const mismatch = again.length > 0 && next !== again;
    return (
        <section className={card}>
            <h2 className={cardHead}>Password</h2>
            <div className="p-4">
                {me.has_password === false ? (
                    <p className="text-sm text-zinc-600">This account signs in through single sign-on and has no password.</p>
                ) : (
                    <form className="space-y-4 max-w-md" onSubmit={(e) => { e.preventDefault(); setMsg(null); change.mutate(); }}>
                        <div>
                            <label htmlFor="pw-cur" className={modalLabel}>Current password</label>
                            <input id="pw-cur" type="password" autoComplete="current-password" className={modalInput} value={cur} onChange={(e) => setCur(e.target.value)} />
                        </div>
                        <div>
                            <label htmlFor="pw-new" className={modalLabel}>New password</label>
                            <input id="pw-new" type="password" autoComplete="new-password" className={modalInput} value={next} onChange={(e) => setNext(e.target.value)} />
                            <p className="text-xs text-zinc-400 mt-1">At least 12 characters; avoid common or reused passwords.</p>
                        </div>
                        <div>
                            <label htmlFor="pw-again" className={modalLabel}>Confirm new password</label>
                            <input id="pw-again" type="password" autoComplete="new-password" className={modalInput} value={again} onChange={(e) => setAgain(e.target.value)} />
                            {mismatch && <p className="text-xs text-red-600 mt-1">Passwords do not match.</p>}
                        </div>
                        <div className="flex items-center gap-3">
                            <button type="submit" className={btnPrimary} disabled={change.isPending || !cur || !next || next !== again}>
                                {change.isPending && <Loader2 size={14} className="animate-spin" />} Change password
                            </button>
                            {msg && <p role={msg.ok ? 'status' : 'alert'} className={`text-sm ${msg.ok ? 'text-emerald-700' : 'text-red-600'}`}>{msg.text}</p>}
                        </div>
                    </form>
                )}
            </div>
        </section>
    );
}

function DisableMfaModal({ open, onClose }: { open: boolean; onClose: () => void }) {
    const qc = useQueryClient();
    const [pw, setPw] = useState('');
    const [code, setCode] = useState('');
    const [error, setError] = useState<string | null>(null);

    const disable = useMutation({
        mutationFn: async () => (await api.post('/users/me/mfa/disable', { current_password: pw, code })).data as { access_token: string },
        onSuccess: (d) => { setSessionToken(d.access_token); void qc.invalidateQueries({ queryKey: ['currentUser'] }); onClose(); },
        onError: (e) => setError(mfaErrorMessage(e)),
    });

    return (
        <Modal open={open} onClose={onClose} title="Turn off two-factor authentication" size="md"
            footer={<>
                <button type="button" className={btnSecondary} onClick={onClose}>Cancel</button>
                <button type="submit" form="mfa-off-form" className={btnDanger} disabled={disable.isPending || !pw || code.length !== 6}>
                    {disable.isPending && <Loader2 size={14} className="animate-spin" />} Turn off
                </button>
            </>}>
            <form id="mfa-off-form" className="space-y-4" onSubmit={(e) => { e.preventDefault(); setError(null); disable.mutate(); }}>
                <div>
                    <label htmlFor="off-pw" className={modalLabel}>Current password</label>
                    <input id="off-pw" type="password" autoComplete="current-password" className={modalInput} value={pw} onChange={(e) => setPw(e.target.value)} />
                </div>
                <div>
                    <label htmlFor="off-code" className={modalLabel}>6-digit code</label>
                    <input id="off-code" className={`${modalInput} font-mono tracking-widest`} inputMode="numeric" autoComplete="one-time-code" maxLength={6}
                        value={code} onChange={(e) => setCode(e.target.value.replace(/\D/g, '').slice(0, 6))} />
                </div>
                {error && <p role="alert" className="text-sm text-red-600">{error}</p>}
            </form>
        </Modal>
    );
}

function MfaCard({ me }: { me: User }) {
    const qc = useQueryClient();
    const [setupOpen, setSetupOpen] = useState(false);
    const [offOpen, setOffOpen] = useState(false);
    const enabled = !!me.mfa_enabled;

    return (
        <section className={card}>
            <h2 className={cardHead}>Two-factor authentication</h2>
            <div className="p-4 flex flex-wrap items-center justify-between gap-3">
                <div className="flex items-center gap-2 text-sm">
                    {enabled ? <ShieldCheck size={16} className="text-emerald-600" /> : <ShieldOff size={16} className="text-zinc-400" />}
                    <span className="text-zinc-700">{enabled ? 'Two-factor authentication is on.' : 'Two-factor authentication is off.'}</span>
                </div>
                {!enabled && <button type="button" className={btnPrimary} onClick={() => setSetupOpen(true)}>Turn on</button>}
                {enabled && !me.mfa_required_by_tenant && me.has_password !== false && (
                    <button type="button" className={btnSecondary} onClick={() => setOffOpen(true)}>Turn off</button>
                )}
                {enabled && me.mfa_required_by_tenant && (
                    <p className="text-xs text-zinc-500 w-full">Your organization requires two-factor authentication, so it cannot be turned off.</p>
                )}
                {enabled && !me.mfa_required_by_tenant && me.has_password === false && (
                    <p className="text-xs text-zinc-500 w-full">Two-factor for single sign-on accounts can only be reset by an admin.</p>
                )}
            </div>
            <MfaSetupDialog open={setupOpen} onClose={() => setSetupOpen(false)}
                onEnabled={() => { setSetupOpen(false); void qc.invalidateQueries({ queryKey: ['currentUser'] }); }} />
            {/* Mounted only while open so password/code state is discarded on close. */}
            {offOpen && <DisableMfaModal open onClose={() => setOffOpen(false)} />}
        </section>
    );
}

export default function ProfilePage() {
    const { data: me, isLoading } = useQuery({
        queryKey: ['currentUser'],
        queryFn: async () => (await api.get('/users/me')).data as User,
    });
    return (
        <PageContainer width="narrow">
            <h1 className="text-lg font-semibold text-zinc-900">Your profile</h1>
            {isLoading || !me
                ? <p className="font-mono text-xs text-zinc-400">$ loading…</p>
                : <>
                    <ProfileCard key={me.id} me={me} />
                    <PasswordCard me={me} />
                    <MfaCard me={me} />
                </>}
        </PageContainer>
    );
}
