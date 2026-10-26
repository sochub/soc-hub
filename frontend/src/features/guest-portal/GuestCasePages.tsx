import { useEffect, useState } from 'react';
import { Link, Navigate, useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Upload } from 'lucide-react';
import { Badge, Button, Label } from '../../components/ui';
import { GuestShell } from './GuestAuthPages';
import { guestApi, guestError, guestSession, guestSignOut } from './guestApi';
import GuestSecurityPanel from './GuestSecurityPanel';
import { Markdown, MarkdownField } from '../../components/Markdown';

interface GuestCaseItem { id: number; title: string; tenant_name: string; permission: string }
interface GuestCase { id: number; title: string; status: string; severity: string; guest_summary: string | null; permission: string }
interface GuestEvent { id: number; event_type: string; content: string; created_at: string; author: string; from_guest: boolean }
interface GuestFile { id: number; filename: string; size_bytes: number; sha256: string; created_at: string }
interface GuestMe { email: string; has_account: boolean; mfa_enabled: boolean; amr: string }

function RequireGuest({ children }: { children: React.ReactNode }) {
    return guestSession.active() ? <>{children}</> : <Navigate to="/guest/login" replace />;
}

function SignOut() {
    const navigate = useNavigate();
    return <button type="button" className="text-xs text-fg-3 hover:text-fg"
        onClick={() => { void guestSignOut().then(() => navigate('/guest/login')); }}>Sign out</button>;
}

function CreateAccountBanner() {
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [sent, setSent] = useState(false);
    const { data: me } = useQuery({ queryKey: ['guest-me'], queryFn: async () => (await guestApi.get('/me')).data as GuestMe, enabled: guestSession.active() });
    if (!me || me.amr !== 'link' || me.has_account) return null;
    // The account is only created from the emailed link, so a forwarded invite can't claim this address.
    const send = async () => {
        setBusy(true); setError(null);
        try { await guestApi.post('/auth/register'); setSent(true); }
        catch (err) { setError(guestError(err, 'Could not send the email.')); }
        finally { setBusy(false); }
    };
    return (
        <div className="mb-6 border border-line bg-panel rounded-lg p-3 text-sm">
            {sent ? (
                <span role="status">We sent a link to <strong>{me.email}</strong> — open it to choose a password.</span>
            ) : (
                <span>Create an account to return without a link{' '}
                    <button type="button" className="text-accent disabled:opacity-50" disabled={busy} onClick={send}>Set password</button></span>
            )}
            {error && <p role="alert" className="mt-1 text-severity-critical">{error}</p>}
        </div>
    );
}

export function GuestHomePage() {
    const navigate = useNavigate();
    const [params] = useSearchParams();
    const showAll = params.get('all') === '1';  // came back from a case: don't bounce a single-case guest
    const { data, isLoading } = useQuery({
        queryKey: ['guest-cases'], queryFn: async () => (await guestApi.get('/cases')).data as GuestCaseItem[],
        enabled: guestSession.active(),
    });
    useEffect(() => {
        if (!showAll && data?.length === 1) navigate(`/guest/cases/${data[0].id}`, { replace: true });
    }, [data, navigate, showAll]);
    return <RequireGuest><GuestShell>
        <div className="flex justify-between items-center mb-4"><h1 className="text-lg font-semibold">Your cases</h1><SignOut /></div>
        <CreateAccountBanner />
        <GuestSecurityPanel />
        {isLoading ? <p className="font-mono text-xs text-muted">$ loading…</p> : !data?.length ? (
            <p className="text-sm text-fg-3">You don't have access to any active cases. Your access may have expired.</p>
        ) : (
            <ul className="space-y-2">{data.map((c) => (
                <li key={c.id}><Link to={`/guest/cases/${c.id}`} className="block bg-panel border border-line rounded-lg p-3 hover:bg-raised/60">
                    <span className="font-medium">#{c.id} {c.title}</span>
                    <span className="block text-xs text-fg-3">{c.tenant_name} · {c.permission}</span>
                </Link></li>
            ))}</ul>
        )}
    </GuestShell></RequireGuest>;
}

export function GuestCasePage() {
    const { id } = useParams();
    const qc = useQueryClient();
    const [comment, setComment] = useState('');
    const [error, setError] = useState<string | null>(null);
    const caseQ = useQuery({ queryKey: ['guest-case', id], queryFn: async () => (await guestApi.get(`/cases/${id}`)).data as GuestCase, retry: false, enabled: guestSession.active() });
    const timeline = useQuery({ queryKey: ['guest-timeline', id], queryFn: async () => (await guestApi.get(`/cases/${id}/timeline`)).data as GuestEvent[], enabled: caseQ.isSuccess });
    const files = useQuery({ queryKey: ['guest-files', id], queryFn: async () => (await guestApi.get(`/cases/${id}/attachments`)).data as GuestFile[], enabled: caseQ.isSuccess });
    const refresh = () => { setError(null); qc.invalidateQueries({ queryKey: ['guest-timeline', id] }); qc.invalidateQueries({ queryKey: ['guest-files', id] }); };
    const post = useMutation({
        mutationFn: async () => guestApi.post(`/cases/${id}/comments`, { content: comment }),
        onSuccess: () => { setComment(''); refresh(); }, onError: (e) => setError(guestError(e, 'Could not post the comment.')),
    });
    const upload = useMutation({
        mutationFn: async (f: File) => { const fd = new FormData(); fd.append('file', f); return guestApi.post(`/cases/${id}/attachments`, fd); },
        onSuccess: refresh, onError: (e) => setError(guestError(e, 'Could not upload the file.')),
    });

    if (caseQ.isError) return <RequireGuest><GuestShell><p className="text-sm">This case isn't available. Your access may have been revoked or expired. <Link to="/guest?all=1" className="text-accent">Back</Link></p></GuestShell></RequireGuest>;
    const c = caseQ.data;
    const canWrite = c?.permission === 'contributor';
    return <RequireGuest><GuestShell>
        <div className="flex justify-between items-center mb-4"><Link to="/guest?all=1" className="text-xs text-fg-3">← Your cases</Link><SignOut /></div>
        <CreateAccountBanner />
        <GuestSecurityPanel />
        {!c ? <p className="font-mono text-xs text-muted">$ loading…</p> : (<div className="space-y-6">
            <header className="space-y-2">
                <h1 className="text-xl font-semibold">#{c.id} {c.title}</h1>
                <div className="flex gap-2"><Badge>{c.status.replace('_', ' ')}</Badge><Badge variant={c.severity as 'low'}>{c.severity}</Badge></div>
                <p className="text-sm text-fg-2 whitespace-pre-wrap">{c.guest_summary || 'No summary shared yet.'}</p>
            </header>

            {error && <p role="alert" className="text-sm text-severity-critical">{error}</p>}

            {canWrite && (
                <section className="bg-panel border border-line rounded-lg p-4 space-y-3">
                    <label className="relative flex flex-col items-center justify-center gap-2 border border-dashed border-line rounded-lg p-6 text-sm text-fg-3 cursor-pointer hover:bg-raised/40"
                        onDragOver={(e) => e.preventDefault()}
                        onDrop={(e) => { e.preventDefault(); const f = e.dataTransfer.files?.[0]; if (f) upload.mutate(f); }}>
                        <Upload size={18} /> {upload.isPending ? 'Uploading…' : 'Drop evidence here or click to choose a file'}
                        <input type="file" className="sr-only" onChange={(e) => { const f = e.target.files?.[0]; if (f) upload.mutate(f); e.target.value = ''; }} />
                    </label>
                    <form onSubmit={(e) => { e.preventDefault(); if (comment.trim()) post.mutate(); }} className="space-y-2">
                        <Label htmlFor="g-comment">Add a comment for the SOC team</Label>
                        <MarkdownField value={comment}><textarea id="g-comment" rows={3} maxLength={10000} value={comment} onChange={(e) => setComment(e.target.value)}
                            className="w-full bg-raised border border-line rounded-md p-2 text-sm text-fg" /></MarkdownField>
                        <Button type="submit" size="sm" disabled={post.isPending || !comment.trim()}>Post comment</Button>
                    </form>
                </section>
            )}

            {!!files.data?.length && (
                <section>
                    <h2 className="text-sm font-semibold mb-2">Your uploads</h2>
                    <ul className="text-xs text-fg-3 space-y-1">{files.data.map((f) => (
                        <li key={f.id}>{f.filename} · {(f.size_bytes / 1024).toFixed(1)} KB · sha256 {f.sha256.slice(0, 12)}…</li>))}</ul>
                </section>
            )}

            <section>
                <h2 className="text-sm font-semibold mb-2">Updates</h2>
                {!timeline.data?.length ? <p className="text-sm text-fg-3">No updates shared yet.</p> : (
                    <ol className="space-y-3">{timeline.data.map((e) => (
                        <li key={e.id} className="bg-panel border border-line rounded-lg p-3 text-sm">
                            <div className="text-xs text-fg-3 mb-1">{e.author} · {new Date(e.created_at).toLocaleString()}</div>
                            <Markdown content={e.content} />
                        </li>))}</ol>
                )}
            </section>
        </div>)}
    </GuestShell></RequireGuest>;
}
