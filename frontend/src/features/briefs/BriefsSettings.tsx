import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { Newspaper, Plus, Trash2 } from 'lucide-react';
import { api } from '../../api/client';
import { apiErrorMessage } from '../../api/errors';
import { cn } from '../../lib/utils';
import { Badge, Button, EmptyState, ErrorState, Input, Label, Select, Skeleton, useConfirm } from '../../components/ui';
import { formatDateTime } from '../../utils/datetime';
import { useMyTimezone } from '../../utils/useMyTimezone';
import { mfaErrorMessage } from '../profile/mfaErrors';
import SettingsPageHeader from '../settings/SettingsPageHeader';

type Kind = 'vulnerability' | 'incident' | 'news';
interface Source {
    id: number; name: string; url: string; kind: Kind; format: string; builtin: boolean; enabled: boolean;
    last_fetched_at: string | null; last_ok_at: string | null; last_error: string | null; item_count: number;
}

const KINDS: [Kind, string][] = [['vulnerability', 'Vulnerability'], ['incident', 'Incident'], ['news', 'News']];
const kindLabel = (k: string) => KINDS.find(([v]) => v === k)?.[1] ?? k;

/** The on/off switch. Returns whether the feature is on, so the page shows the sources only then. */
function BriefsSwitch({ enabled, correlate, loading }: { enabled: boolean; correlate: boolean; loading: boolean }) {
    const qc = useQueryClient();
    const [error, setError] = useState<string | null>(null);
    const toggle = useMutation({
        mutationFn: async (body: { briefs_enabled?: boolean; briefs_correlate?: boolean }) => (await api.put('/tenants/current/security', body)).data,
        // /users/me carries the flags the sidebar and the Briefs page read, so both follow right away.
        onSuccess: (d) => {
            setError(null); qc.setQueryData(['tenant-security'], d);
            void qc.invalidateQueries({ queryKey: ['currentUser'] }); void qc.invalidateQueries({ queryKey: ['briefs'] });
        },
        onError: (err) => setError(mfaErrorMessage(err, 'Could not update the setting.')),
    });
    return (
        <section className="rounded-md bg-panel border border-line">
            <div className="flex items-center justify-between px-4 h-11 border-b border-line">
                <h3 className="flex items-center gap-2 text-sm font-semibold text-fg">
                    <Newspaper size={15} className="text-accent" aria-hidden /> Security briefs
                </h3>
                {enabled && <Link to="/briefs" className="text-xs text-accent hover:underline">Open the Briefs page</Link>}
            </div>
            {loading ? (
                <Skeleton rows={2} className="p-4" />
            ) : (
                <div className="p-4 space-y-3">
                    <label className="flex items-center gap-2 text-sm text-fg cursor-pointer">
                        <input type="checkbox" checked={enabled} disabled={toggle.isPending}
                            onChange={(e) => toggle.mutate({ briefs_enabled: e.target.checked })}
                            className="border-line-strong text-accent focus:ring-accent" />
                        Turn on Security briefs
                    </label>
                    <p className="text-xs text-fg-3 max-w-[80ch]">
                        Adds a Briefs page with recent vulnerabilities and incidents from public security feeds, each with a short AI-written brief:
                        what happened, why it matters and lessons learned. While this is on, the deployment fetches those public feeds from the
                        internet every 30 minutes, and this organization&rsquo;s AI model writes the briefs (up to 40 a day). Feed text is sent to that model; no case data is.
                    </p>
                    {enabled && (
                        <>
                            <label className="flex items-center gap-2 text-sm text-fg cursor-pointer">
                                <input type="checkbox" checked={correlate} disabled={toggle.isPending}
                                    onChange={(e) => toggle.mutate({ briefs_correlate: e.target.checked })}
                                    className="border-line-strong text-accent focus:ring-accent" />
                                Correlate briefs with this organization&rsquo;s observables
                            </label>
                            <p className="text-xs text-fg-3 max-w-[80ch]">
                                Flags a brief when an indicator or CVE it mentions is one of your artifacts or IOCs, with the cases they are on.
                                Matching happens inside this deployment: nothing about your observables is sent anywhere, including to the AI model.
                            </p>
                        </>
                    )}
                    {error && <p role="alert" className="text-xs text-severity-critical">{error}</p>}
                </div>
            )}
        </section>
    );
}

/** The stored error is an exception class or an HTTP status; say it in plain words. */
function fetchProblem(e: string): string {
    const http = /^HTTP (\d+)$/.exec(e);
    if (http) return http[1] === '404' ? 'The feed was not found at this address (HTTP 404).' : `The feed's server answered with an error (HTTP ${http[1]}).`;
    if (/Timeout/i.test(e)) return 'The feed took too long to answer.';
    if (/Connect|Network|Protocol|Read|Write/i.test(e)) return 'Could not connect to the feed.';
    return `Last fetch failed: ${e}.`;
}

function SourcesPanel() {
    const qc = useQueryClient();
    const tz = useMyTimezone();
    const { confirm, dialog } = useConfirm();
    const [adding, setAdding] = useState(false);
    const [form, setForm] = useState<{ name: string; url: string; kind: Kind }>({ name: '', url: '', kind: 'news' });
    const { data: sources = [], isLoading, isError, refetch } = useQuery({
        queryKey: ['briefs', 'sources'], queryFn: async () => (await api.get('/briefs/sources')).data as Source[],
    });
    const refresh = () => qc.invalidateQueries({ queryKey: ['briefs'] });
    const toggle = useMutation({ mutationFn: async (s: Source) => api.put(`/briefs/sources/${s.id}`, { enabled: !s.enabled }), onSuccess: refresh });
    const remove = useMutation({ mutationFn: async (id: number) => api.delete(`/briefs/sources/${id}`), onSuccess: refresh });
    const add = useMutation({
        mutationFn: async () => api.post('/briefs/sources', { ...form, name: form.name.trim(), url: form.url.trim() }),
        onSuccess: () => { setAdding(false); setForm({ name: '', url: '', kind: 'news' }); return refresh(); },
    });
    const valid = !!form.name.trim() && /^https:\/\/\S+$/i.test(form.url.trim());

    return (
        <div className="space-y-4">
            <div className="flex flex-wrap items-start gap-3">
                <p className="mr-auto text-sm text-fg-3 max-w-[80ch]">
                    Built-in sources are shared by every organization on this deployment; switching one off hides it for this organization only.
                    Your own RSS or Atom feeds are fetched over https about once an hour and are visible to this organization only.
                </p>
                {!adding && <Button size="sm" onClick={() => setAdding(true)}><Plus size={12} aria-hidden /> Add source</Button>}
            </div>

            {adding && (
                <form className="rounded-md border border-line bg-surface p-4 space-y-3" onSubmit={(e) => { e.preventDefault(); if (valid) add.mutate(); }}>
                    <div className="grid grid-cols-1 sm:grid-cols-[minmax(0,1fr)_minmax(0,2fr)_auto] gap-3">
                        <div><Label htmlFor="brief-src-name">Name</Label>
                            <Input id="brief-src-name" value={form.name} maxLength={120} required onChange={(e) => setForm({ ...form, name: e.target.value })} /></div>
                        <div><Label htmlFor="brief-src-url">Feed URL (https)</Label>
                            <Input id="brief-src-url" type="url" value={form.url} maxLength={2000} required placeholder="https://example.com/feed.xml"
                                onChange={(e) => setForm({ ...form, url: e.target.value })} /></div>
                        <div><Label htmlFor="brief-src-kind">Kind</Label>
                            <Select id="brief-src-kind" value={form.kind} onChange={(e) => setForm({ ...form, kind: e.target.value as Kind })}>
                                {KINDS.map(([v, label]) => <option key={v} value={v}>{label}</option>)}
                            </Select></div>
                    </div>
                    {form.url.trim() !== '' && !/^https:\/\//i.test(form.url.trim()) && <p role="alert" className="text-xs text-severity-high">The feed address must start with https://</p>}
                    {add.isError && <p role="alert" className="text-xs text-severity-critical">{apiErrorMessage(add.error, 'Could not add the source.')}</p>}
                    <div className="flex justify-end gap-2">
                        <Button size="sm" variant="ghost" onClick={() => { setAdding(false); add.reset(); }}>Cancel</Button>
                        <Button size="sm" type="submit" disabled={!valid || add.isPending}>{add.isPending ? 'Adding…' : 'Add source'}</Button>
                    </div>
                </form>
            )}

            {(toggle.isError || remove.isError) && <p role="alert" className="text-xs text-severity-critical">{apiErrorMessage(toggle.error ?? remove.error, 'Could not change the source.')}</p>}
            {isError ? <ErrorState message="Could not load sources." onRetry={() => refetch()} /> : isLoading ? <Skeleton rows={4} /> : !sources.length ? (
                <EmptyState title="No sources yet" body="Built-in sources appear after the first scheduled fetch (every 30 minutes). You can add your own feed now." />
            ) : (
                <ul className="space-y-2">
                    {sources.map((s) => (
                        <li key={s.id} className={cn('rounded-md border border-line bg-panel p-3', !s.enabled && 'opacity-70')}>
                            <div className="flex flex-wrap items-center gap-2">
                                <span className="text-sm font-medium text-fg">{s.name}</span>
                                <Badge>{kindLabel(s.kind)}</Badge>
                                {s.builtin && <Badge>Built-in</Badge>}
                                {!s.enabled && <Badge>Off</Badge>}
                                <span className="ml-auto flex items-center gap-3">
                                    <label className="flex items-center gap-1.5 text-xs text-fg-2">
                                        <input type="checkbox" checked={s.enabled} disabled={toggle.isPending} onChange={() => toggle.mutate(s)} /> Enabled
                                    </label>
                                    {!s.builtin && (
                                        <button type="button" aria-label={`Delete source ${s.name}`} title="Delete" className="text-muted hover:text-severity-critical"
                                            onClick={async () => {
                                                if (await confirm({ title: `Delete “${s.name}”?`, danger: true, confirmLabel: 'Delete source',
                                                    body: `Its ${s.item_count.toLocaleString()} item${s.item_count === 1 ? '' : 's'}, their AI briefs and read marks are deleted too.` })) remove.mutate(s.id);
                                            }}><Trash2 size={13} aria-hidden /></button>
                                    )}
                                </span>
                            </div>
                            <p className="mt-1 text-xs font-mono text-fg-3 break-all">{s.url}</p>
                            <p className="mt-1 text-xs text-fg-3">
                                <span className="num">{s.item_count.toLocaleString()}</span> item{s.item_count === 1 ? '' : 's'}
                                {' · '}{s.last_fetched_at ? `last fetched ${formatDateTime(s.last_fetched_at, tz)}` : 'not fetched yet'}
                            </p>
                            {/* A built-in that has never answered and isn't there (the sochub feed before anything is published) is not a fault to fix. */}
                            {s.last_error && (s.builtin && !s.last_ok_at && /HTTP 404/.test(s.last_error)
                                ? <p className="mt-1 text-xs text-fg-3">Nothing is published at this address yet.</p>
                                : <p className="mt-1 text-xs text-severity-high">Problem: {fetchProblem(s.last_error)}{s.last_ok_at && ` Last worked ${formatDateTime(s.last_ok_at, tz)}.`}</p>)}
                        </li>
                    ))}
                </ul>
            )}
            {dialog}
        </div>
    );
}


/** Settings → Briefs: everything an admin configures about Security briefs. The Briefs page only shows them. */
export default function BriefsSettings() {
    const { data, isLoading } = useQuery({
        queryKey: ['tenant-security'],
        queryFn: async () => (await api.get('/tenants/current/security')).data as { briefs_enabled: boolean; briefs_correlate: boolean },
    });
    const enabled = !!data?.briefs_enabled;
    return (
        <div className="space-y-6">
            <SettingsPageHeader title="Briefs" description="Security news and incident write-ups for analysts, with AI-written briefs. Switch it on and choose the feeds here; analysts read them on the Briefs page." />
            <BriefsSwitch enabled={enabled} correlate={!!data?.briefs_correlate} loading={isLoading || !data} />
            {enabled && (
                <section aria-labelledby="briefs-sources-title" className="space-y-3">
                    <h3 id="briefs-sources-title" className="text-sm font-semibold text-fg">Sources</h3>
                    <SourcesPanel />
                </section>
            )}
        </div>
    );
}
