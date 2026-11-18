import { useEffect, useState } from 'react';
import { Link, Navigate, useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ArrowLeft, ChevronDown, LogOut, Upload, UserRound } from 'lucide-react';
import { Button, EmptyState, Label, SeverityStamp, Skeleton, StatusStamp, Textarea, buttonClass } from '../../components/ui';
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

/** Account menu in the guest top bar: who is signed in, two-factor settings and sign out. */
function AccountMenu() {
    const navigate = useNavigate();
    const { data: me } = useQuery({ queryKey: ['guest-me'], queryFn: async () => (await guestApi.get('/me')).data as GuestMe, enabled: guestSession.active() });
    return (
        <details className="relative group">
            <summary className="list-none [&::-webkit-details-marker]:hidden inline-flex items-center gap-1.5 h-8 px-2.5 rounded-md text-sm text-fg-2 hover:bg-raised hover:text-fg cursor-pointer">
                <UserRound size={15} aria-hidden /> <span className="max-w-[16ch] truncate">{me?.email ?? 'Account'}</span>
                <ChevronDown size={13} aria-hidden className="group-open:rotate-180 transition-transform" />
            </summary>
            <div className="absolute right-0 top-full mt-1 z-20 w-80 max-w-[calc(100vw-2rem)] rounded-md border border-line bg-panel p-3 space-y-3 shadow-sm">
                {me && <p className="text-xs text-fg-3 truncate">Signed in as <span className="text-fg">{me.email}</span></p>}
                <GuestSecurityPanel />
                <div className="border-t border-line pt-3">
                    <Button variant="secondary" size="sm" className="w-full"
                        onClick={() => { void guestSignOut().then(() => navigate('/guest/login')); }}>
                        <LogOut size={13} aria-hidden /> Sign out
                    </Button>
                </div>
            </div>
        </details>
    );
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
        <div className="mb-6 border border-line bg-panel rounded-md p-3 text-sm">
            {sent ? (
                <span role="status">We sent a link to <strong>{me.email}</strong> — open it to choose a password.</span>
            ) : (
                <span>Create an account to return without a link{' '}
                    <button type="button" className="text-accent underline-offset-4 hover:underline disabled:opacity-50" disabled={busy} onClick={send}>Set password</button></span>
            )}
            {error && <p role="alert" className="mt-1 text-severity-critical">{error}</p>}
        </div>
    );
}

const guestImageSrc = (src: string) => src.replace(/^\/api\/v1\/cases\/(\d+)\/attachments\/(\d+)\/raw$/, '/api/v1/guest/cases/$1/attachments/$2/raw');

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
    const orgs = [...new Set((data ?? []).map((c) => c.tenant_name))];
    return <RequireGuest><GuestShell orgName={orgs.length === 1 ? orgs[0] : null} account={<AccountMenu />}>
        <h1 className="text-2xl font-semibold mb-5">Your cases</h1>
        <CreateAccountBanner />
        {isLoading ? <Skeleton rows={3} /> : !data?.length ? (
            <EmptyState className="rounded-md border border-line bg-panel" title="No active cases"
                body="You don't have access to any active cases. Your access may have expired." />
        ) : (
            <ul className="rounded-md border border-line bg-panel divide-y divide-line">{data.map((c) => (
                <li key={c.id}><Link to={`/guest/cases/${c.id}`} className="block px-4 py-3 hover:bg-raised">
                    <span className="font-medium"><span className="num text-fg-3">#{c.id}</span> {c.title}</span>
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
    const cases = useQuery({ queryKey: ['guest-cases'], queryFn: async () => (await guestApi.get('/cases')).data as GuestCaseItem[], enabled: guestSession.active() });
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

    const orgName = cases.data?.find((x) => String(x.id) === id)?.tenant_name ?? null;
    const shell = (children: React.ReactNode) => (
        <RequireGuest><GuestShell orgName={orgName} account={<AccountMenu />}>{children}</GuestShell></RequireGuest>
    );

    if (caseQ.isError) return shell(
        <EmptyState title="This case isn't available" body="Your access may have been revoked or expired."
            action={<Link to="/guest?all=1" className={buttonClass('secondary', 'sm')}>Back to your cases</Link>} />,
    );
    const c = caseQ.data;
    const canWrite = c?.permission === 'contributor';
    const uploads = files.data ?? [];
    return shell(<>
        <Link to="/guest?all=1" className="inline-flex items-center gap-1 text-sm text-fg-3 hover:text-fg mb-4"><ArrowLeft size={14} aria-hidden /> Your cases</Link>
        <CreateAccountBanner />
        {!c ? <Skeleton rows={6} /> : (<div className="space-y-8">
            <header className="space-y-3">
                <h1 className="text-2xl font-semibold text-balance"><span className="num text-fg-3 font-normal">#{c.id}</span> {c.title}</h1>
                <div className="flex flex-wrap gap-2"><StatusStamp status={c.status} /><SeverityStamp severity={c.severity} /></div>
            </header>

            <section aria-labelledby="g-summary">
                <h2 id="g-summary" className="label-caps mb-2">Summary</h2>
                <p className="text-sm text-fg-2 whitespace-pre-wrap">{c.guest_summary || 'No summary shared yet.'}</p>
            </section>

            {error && <p role="alert" className="text-sm text-severity-critical">{error}</p>}

            {canWrite && (
                <section aria-labelledby="g-evidence" className="space-y-3">
                    <h2 id="g-evidence" className="text-sm font-semibold">Add evidence</h2>
                    <label className="flex flex-col items-center justify-center gap-2 rounded-md border border-dashed border-line-strong bg-panel p-6 text-sm text-fg-3 cursor-pointer hover:bg-raised focus-within:outline-2 focus-within:outline-offset-2 focus-within:outline-accent"
                        onDragOver={(e) => e.preventDefault()}
                        onDrop={(e) => { e.preventDefault(); const f = e.dataTransfer.files?.[0]; if (f) upload.mutate(f); }}>
                        <Upload size={18} aria-hidden /> {upload.isPending ? 'Uploading…' : 'Drop evidence here or click to choose a file'}
                        <input type="file" className="sr-only" onChange={(e) => { const f = e.target.files?.[0]; if (f) upload.mutate(f); e.target.value = ''; }} />
                    </label>
                </section>
            )}

            {(canWrite || uploads.length > 0) && (
                <section aria-labelledby="g-uploads">
                    <h2 id="g-uploads" className="text-sm font-semibold mb-2">Your uploads</h2>
                    <div className="rounded-md border border-line bg-panel overflow-x-auto">
                        {files.isLoading ? <Skeleton rows={2} className="p-3" /> : uploads.length === 0 ? (
                            <EmptyState className="py-8" title="Nothing uploaded yet" body="Files you add here go straight to the response team." />
                        ) : (
                            <table className="w-full text-sm">
                                <thead><tr className="border-b border-line label-caps text-left">
                                    <th className="px-3 py-2">File</th><th className="px-3 py-2">Size</th><th className="px-3 py-2">SHA-256</th><th className="px-3 py-2">Uploaded</th>
                                </tr></thead>
                                <tbody className="divide-y divide-line">{uploads.map((f) => (
                                    <tr key={f.id}>
                                        <td className="px-3 py-2.5 text-fg break-all">{f.filename}</td>
                                        <td className="px-3 py-2.5 num text-fg-3 whitespace-nowrap">{(f.size_bytes / 1024).toFixed(1)} KB</td>
                                        <td className="px-3 py-2.5 font-mono text-xs text-fg-3" title={f.sha256}>{f.sha256.slice(0, 12)}…</td>
                                        <td className="px-3 py-2.5 num text-xs text-fg-3 whitespace-nowrap">{new Date(f.created_at).toLocaleString()}</td>
                                    </tr>))}</tbody>
                            </table>
                        )}
                    </div>
                </section>
            )}

            {canWrite && (
                <section aria-labelledby="g-comment-h">
                    <h2 id="g-comment-h" className="sr-only">Comment</h2>
                    <form onSubmit={(e) => { e.preventDefault(); if (comment.trim()) post.mutate(); }} className="space-y-2">
                        <Label htmlFor="g-comment">Add a comment for the SOC team</Label>
                        <MarkdownField value={comment}><Textarea id="g-comment" rows={3} maxLength={10000} value={comment} onChange={(e) => setComment(e.target.value)} /></MarkdownField>
                        <Button type="submit" size="sm" disabled={post.isPending || !comment.trim()}>Post comment</Button>
                    </form>
                </section>
            )}

            <section aria-labelledby="g-updates">
                <h2 id="g-updates" className="text-sm font-semibold mb-2">Updates</h2>
                {!timeline.data?.length ? <p className="text-sm text-fg-3">No updates shared yet.</p> : (
                    <ol className="space-y-3">{timeline.data.map((e) => (
                        <li key={e.id} className="bg-panel border border-line rounded-md p-3 text-sm">
                            <div className="text-xs text-fg-3 mb-1">{e.author} · <span className="num">{new Date(e.created_at).toLocaleString()}</span></div>
                            <Markdown content={e.content} transformImageSrc={guestImageSrc} />
                        </li>))}</ol>
                )}
            </section>
        </div>)}
    </>);
}
