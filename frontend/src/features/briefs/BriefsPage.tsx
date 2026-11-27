import { useState } from 'react';
import { useMutation, useQuery, useQueryClient, keepPreviousData } from '@tanstack/react-query';
import { Link, useSearchParams } from 'react-router-dom';
import { Bookmark, BookmarkCheck, CheckCheck, Crosshair, ExternalLink, Search, Sparkles, X } from 'lucide-react';
import { api } from '../../api/client';
import { apiErrorMessage } from '../../api/errors';
import { cn } from '../../lib/utils';
import type { User } from '../../types';
import PageContainer from '../../components/layout/PageContainer';
import { Badge, Button, EmptyState, ErrorState, Input, PageHeader, Pager, Select, Skeleton } from '../../components/ui';
import { formatDateTime } from '../../utils/datetime';
import { useMyTimezone } from '../../utils/useMyTimezone';
import { relativeTime } from '../notifications/api';

type Kind = 'vulnerability' | 'incident' | 'news';
interface AiBrief { status: 'ready' | 'failed'; what_happened: string | null; why_it_matters: string | null; lessons: string[]; model: string | null; created_at: string | null }
interface Indicator { type: string; value: string }
interface Match { value: string; type: string; cases: { id: number; title: string }[]; ioc: boolean }
interface BriefItem {
    id: number; title: string; summary: string; url: string | null; published_at: string; kind: Kind; cves: string[];
    indicators: Indicator[]; matches: Match[];
    source: { id: number; name: string }; brief: AiBrief | null; read: boolean; saved: boolean;
}

const KINDS: [Kind, string][] = [['vulnerability', 'Vulnerability'], ['incident', 'Incident'], ['news', 'News']];
const kindLabel = (k: string) => KINDS.find(([v]) => v === k)?.[1] ?? k;
const PAGE_SIZE = 30;
// Feed text is untrusted: a link is only rendered when it is plain http(s) (never javascript:, data:, ...).
const safeHref = (url: string | null) => (url && /^https?:\/\//i.test(url) ? url : null);
const chip = 'inline-flex items-center gap-1 h-7 px-2 rounded-sm border text-xs focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent';
const chipOn = 'border-accent/40 bg-accent/10 text-fg hover:border-accent';
const chipOff = 'border-line bg-panel text-fg-2 hover:border-line-strong';

function AiBriefBlock({ brief }: { brief: AiBrief }) {
    return (
        <section aria-label="AI-written brief" className="mt-3 rounded-md border border-line bg-surface p-3 space-y-2.5">
            <p className="flex items-center gap-1.5 label-caps"><Sparkles size={12} aria-hidden /> AI-written brief{brief.model && ` · ${brief.model}`}</p>
            <div><h3 className="text-xs font-semibold text-fg-2">What happened</h3><p className="text-sm text-fg">{brief.what_happened}</p></div>
            <div><h3 className="text-xs font-semibold text-fg-2">Why it matters</h3><p className="text-sm text-fg">{brief.why_it_matters}</p></div>
            <div>
                <h3 className="text-xs font-semibold text-fg-2">Lessons learned</h3>
                <ul className="list-disc pl-5 text-sm text-fg space-y-0.5">{brief.lessons.map((l, i) => <li key={i}>{l}</li>)}</ul>
            </div>
        </section>
    );
}

const typeLabel = (t: string) => (t === 'cve' ? 'CVE' : t === 'ip' ? 'IP' : t === 'url' ? 'URL' : t.replace(/_/g, ' '));
const INDICATORS_SHOWN = 8;

/** Values of this brief that are among the organization's observables, with the cases they are on. */
function MatchBlock({ matches }: { matches: Match[] }) {
    return (
        <section aria-label="Matches with your observables" className="mb-3 rounded-md border border-severity-high/40 bg-severity-high/5 p-3">
            <p className="flex items-center gap-1.5 text-xs font-semibold text-severity-high"><Crosshair size={13} aria-hidden /> Matches your observables</p>
            <ul className="mt-2 space-y-1.5">
                {matches.map((m) => (
                    <li key={`${m.type}:${m.value}`} className="flex flex-wrap items-baseline gap-x-2 gap-y-1 text-xs">
                        <span className="font-mono text-fg break-all">{m.value}</span>
                        <span className="text-fg-3">{typeLabel(m.type)}</span>
                        {m.ioc && <Badge>IOC</Badge>}
                        {m.cases.map((c) => <Link key={c.id} to={`/cases/${c.id}`} className="text-accent hover:underline">Case #{c.id} {c.title}</Link>)}
                        {!m.cases.length && <span className="text-fg-3">not on a case</span>}
                    </li>
                ))}
            </ul>
        </section>
    );
}

/** Indicators the feed text mentions. Untrusted values: plain text only, never links, nothing to click through. */
function IndicatorChips({ indicators }: { indicators: Indicator[] }) {
    const [all, setAll] = useState(false);
    const shown = all ? indicators : indicators.slice(0, INDICATORS_SHOWN);
    return (
        <div className="mt-2">
            <p className="text-xs text-fg-3">Indicators in this brief</p>
            <ul aria-label="Indicators in this brief" className="mt-1 flex flex-wrap items-center gap-1.5">
                {shown.map((i) => (
                    <li key={`${i.type}:${i.value}`} title={typeLabel(i.type)}
                        className="inline-flex max-w-full items-center h-6 px-2 rounded-sm border border-line bg-raised text-xs font-mono text-fg-2">
                        <span className="truncate">{i.value}</span><span className="sr-only"> ({typeLabel(i.type)})</span>
                    </li>
                ))}
                {indicators.length > INDICATORS_SHOWN && (
                    <li><Button size="sm" variant="ghost" className="h-6 px-2" aria-expanded={all} onClick={() => setAll((v) => !v)}>
                        {all ? 'Show fewer' : `+${indicators.length - INDICATORS_SHOWN} more`}</Button></li>
                )}
            </ul>
        </div>
    );
}

function ItemCard({ item, canBrief, tz, onRead, onFilter }: {
    item: BriefItem; canBrief: boolean; tz: string | null; onRead: () => void; onFilter: (next: Record<string, string>) => void;
}) {
    const qc = useQueryClient();
    const [open, setOpen] = useState(false);
    const href = safeHref(item.url);
    const ready = item.brief?.status === 'ready';
    const state = useMutation({
        mutationFn: async (body: { read?: boolean; saved?: boolean }) => (await api.post(`/briefs/${item.id}/state`, body)).data,
        onSuccess: () => qc.invalidateQueries({ queryKey: ['briefs'] }),
    });
    const briefMe = useMutation({
        mutationFn: async () => (await api.post(`/briefs/${item.id}/brief`)).data as AiBrief,
        onSuccess: () => { onRead(); return qc.invalidateQueries({ queryKey: ['briefs'] }); },
    });
    const expand = () => { setOpen((v) => !v); if (!open) onRead(); };
    const summaryId = `brief-summary-${item.id}`;

    return (
        <article aria-label={item.title} className={cn('rounded-md border bg-panel p-4', item.read ? 'border-line' : 'border-line-strong')}>
            {item.matches.length > 0 && <MatchBlock matches={item.matches} />}
            <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-fg-3">
                {/* Unread is said in words too, never by the dot or the weight alone. */}
                {!item.read && <span className="inline-flex items-center gap-1 font-semibold text-accent"><span aria-hidden className="h-1.5 w-1.5 rounded-full bg-accent" />Unread</span>}
                <Badge>{kindLabel(item.kind)}</Badge>
                <button type="button" onClick={() => onFilter({ source: String(item.source.id) })} title={`Show only ${item.source.name}`}
                    className="text-fg-2 hover:text-accent hover:underline">{item.source.name}</button>
                <time dateTime={item.published_at} title={formatDateTime(item.published_at, tz)} className="num">{relativeTime(item.published_at, tz)}</time>
                <span className="ml-auto flex items-center gap-1">
                    <Button size="sm" variant="ghost" disabled={state.isPending} onClick={() => state.mutate({ read: !item.read })}>
                        {item.read ? 'Mark unread' : 'Mark read'}
                    </Button>
                    <button type="button" aria-pressed={item.saved} aria-label={item.saved ? 'Remove from saved' : 'Save for later'}
                        title={item.saved ? 'Saved. Click to remove.' : 'Save for later'} disabled={state.isPending}
                        onClick={() => state.mutate({ saved: !item.saved })}
                        className={cn('inline-flex h-8 w-8 items-center justify-center rounded-md hover:bg-raised focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent',
                            item.saved ? 'text-accent' : 'text-muted hover:text-fg')}>
                        {item.saved ? <BookmarkCheck size={16} aria-hidden /> : <Bookmark size={16} aria-hidden />}
                    </button>
                </span>
            </div>

            <h2 className={cn('mt-1.5 text-sm text-fg text-pretty', item.read ? 'font-medium' : 'font-semibold')}>
                {href ? (
                    <a href={href} target="_blank" rel="noopener noreferrer" onClick={onRead} className="hover:text-accent hover:underline">
                        {item.title} <ExternalLink size={12} aria-hidden className="inline align-baseline text-muted" /><span className="sr-only"> (opens the original in a new tab)</span>
                    </a>
                ) : item.title}
            </h2>

            {item.cves.length > 0 && (
                <ul aria-label="CVEs mentioned" className="mt-2 flex flex-wrap gap-1.5">
                    {item.cves.map((c) => (
                        <li key={c}><button type="button" onClick={() => onFilter({ cve: c })} title={`Show briefs that mention ${c}`}
                            className={cn(chip, chipOff, 'h-6 font-mono')}>{c}</button></li>
                    ))}
                </ul>
            )}

            {item.indicators.length > 0 && <IndicatorChips indicators={item.indicators} />}

            {ready && item.brief ? (
                <>
                    <AiBriefBlock brief={item.brief} />
                    {item.summary && (
                        <details className="mt-2 text-sm" onToggle={(e) => { if (e.currentTarget.open) onRead(); }}>
                            <summary className="cursor-pointer text-xs text-fg-3 hover:text-fg">Original summary</summary>
                            <p className="mt-1 text-fg-2 whitespace-pre-line">{item.summary}</p>
                        </details>
                    )}
                </>
            ) : (
                <>
                    {item.summary && <p id={summaryId} className={cn('mt-2 text-sm text-fg-2', !open && 'line-clamp-3')}>{item.summary}</p>}
                    <div className="mt-2 flex flex-wrap items-center gap-2">
                        {item.summary.length > 280 && (
                            <Button size="sm" variant="ghost" aria-expanded={open} aria-controls={summaryId} onClick={expand}>{open ? 'Show less' : 'Show more'}</Button>
                        )}
                        {canBrief && (
                            <Button size="sm" variant="secondary" disabled={briefMe.isPending} onClick={() => briefMe.mutate()}>
                                <Sparkles size={12} aria-hidden />{briefMe.isPending ? 'Writing brief…' : item.brief ? 'Try again' : 'Brief me'}
                            </Button>
                        )}
                        {briefMe.isError
                            ? <p role="alert" className="text-xs text-severity-critical">{apiErrorMessage(briefMe.error, 'Could not write the brief.')}</p>
                            : item.brief?.status === 'failed' && !briefMe.isPending && (
                                <p className="text-xs text-fg-3">The AI model could not write a brief for this item{item.brief.model && ` (${item.brief.model})`}.</p>
                            )}
                    </div>
                </>
            )}
            {state.isError && <p role="alert" className="mt-2 text-xs text-severity-critical">{apiErrorMessage(state.error, 'Could not update this item.')}</p>}
        </article>
    );
}

function BriefList({ canBrief, correlate }: { canBrief: boolean; correlate: boolean }) {
    const qc = useQueryClient();
    const tz = useMyTimezone();
    const [sp, setSp] = useSearchParams();
    const kind = sp.get('kind') ?? '';
    const source = sp.get('source') ?? '';
    const cve = sp.get('cve') ?? '';
    const q = sp.get('q') ?? '';
    const unread = sp.get('unread') === '1';
    const saved = sp.get('saved') === '1';
    const matched = correlate && sp.get('matched') === '1';  // the server ignores it too when correlation is off
    const page = Math.max(0, (Number(sp.get('page')) || 1) - 1);
    const [search, setSearch] = useState(q);

    // Filters live in the URL so a view can be shared; any change but the page itself returns to page 1.
    const setParams = (next: Record<string, string | null>) => setSp((prev) => {
        const p = new URLSearchParams(prev);
        if (!('page' in next)) p.delete('page');
        for (const [k, v] of Object.entries(next)) { if (!v || (k === 'page' && v === '1')) p.delete(k); else p.set(k, v); }
        return p;
    }, { replace: true });

    const filters = {
        ...(kind && { kind }), ...(Number(source) > 0 && { source_id: Number(source) }), ...(cve && { cve }), ...(q && { q }),
        ...(unread && { unread: true }), ...(saved && { saved: true }), ...(matched && { matched: true }),
    };
    const filtersActive = Object.keys(filters).length > 0;
    const clear = () => { setSearch(''); setParams({ kind: null, source: null, cve: null, q: null, unread: null, saved: null, matched: null }); };
    const params = { ...filters, skip: page * PAGE_SIZE, limit: PAGE_SIZE };
    const { data, isLoading, isError, refetch } = useQuery({
        queryKey: ['briefs', 'list', params],
        queryFn: async () => {
            const r = await api.get<BriefItem[]>('/briefs/', { params });
            return { items: r.data, total: Number(r.headers['x-total-count'] ?? r.data.length) };
        },
        placeholderData: keepPreviousData,
    });
    const items = data?.items ?? [];
    const total = data?.total ?? 0;
    const refresh = () => qc.invalidateQueries({ queryKey: ['briefs'] });
    const markRead = useMutation({ mutationFn: async (id: number) => api.post(`/briefs/${id}/state`, { read: true }), onSuccess: refresh });
    const readAll = useMutation({ mutationFn: async () => api.post('/briefs/read-all', null, { params: filters }), onSuccess: refresh });
    const sourceName = items.find((i) => String(i.source.id) === source)?.source.name;

    return (
        <div className="space-y-4">
            <div className="flex flex-wrap items-center gap-2">
                <p className="mr-auto text-sm text-fg-3 num">{isLoading ? ' ' : total === 0 ? '0 briefs' : `Showing ${page * PAGE_SIZE + 1}–${page * PAGE_SIZE + items.length} of ${total.toLocaleString()}`}</p>
                <form role="search" className="relative" onSubmit={(e) => { e.preventDefault(); setParams({ q: search.trim() || null }); }}>
                    <Search size={14} aria-hidden className="absolute left-2.5 top-1/2 -translate-y-1/2 text-muted pointer-events-none" />
                    <Input inputSize="sm" type="search" aria-label="Search titles and summaries" placeholder="Search, then Enter" className="pl-8 w-56"
                        value={search} onChange={(e) => { setSearch(e.target.value); if (!e.target.value && q) setParams({ q: null }); }} />
                </form>
                <Select inputSize="sm" className="w-auto" aria-label="Kind" value={kind} onChange={(e) => setParams({ kind: e.target.value || null })}>
                    <option value="">All kinds</option>
                    {KINDS.map(([v, label]) => <option key={v} value={v}>{label}</option>)}
                </Select>
                <button type="button" aria-pressed={unread} onClick={() => setParams({ unread: unread ? null : '1' })} className={cn(chip, 'h-8', unread ? chipOn : chipOff)}>Unread only</button>
                {correlate && (
                    <button type="button" aria-pressed={matched} onClick={() => setParams({ matched: matched ? null : '1' })} className={cn(chip, 'h-8', matched ? chipOn : chipOff)}>
                        <Crosshair size={12} aria-hidden />Matches my observables
                    </button>
                )}
                <button type="button" aria-pressed={saved} onClick={() => setParams({ saved: saved ? null : '1' })} className={cn(chip, 'h-8', saved ? chipOn : chipOff)}>
                    <Bookmark size={12} aria-hidden />Saved
                </button>
                <Button size="sm" variant="secondary" disabled={readAll.isPending || total === 0} onClick={() => readAll.mutate()}
                    title={filtersActive ? 'Marks everything that matches the current filters as read' : undefined}>
                    <CheckCheck size={12} aria-hidden />Mark all read
                </Button>
            </div>

            {(source || cve) && (
                <ul aria-label="Active filters" className="flex flex-wrap items-center gap-1.5">
                    {source && <li><button type="button" onClick={() => setParams({ source: null })} aria-label="Remove filter: source" className={cn(chip, chipOn)}>
                        Source: {sourceName ?? `#${source}`}<X size={12} aria-hidden /></button></li>}
                    {cve && <li><button type="button" onClick={() => setParams({ cve: null })} aria-label={`Remove filter: ${cve}`} className={cn(chip, chipOn)}>
                        <span className="font-mono">{cve}</span><X size={12} aria-hidden /></button></li>}
                </ul>
            )}
            {(markRead.isError || readAll.isError) && <p role="alert" className="text-xs text-severity-critical">{apiErrorMessage(markRead.error ?? readAll.error, 'Could not mark as read.')}</p>}

            {isError ? <ErrorState message="Could not load briefs." onRetry={() => refetch()} /> : isLoading ? <Skeleton rows={6} /> : items.length === 0 ? (
                filtersActive
                    ? <EmptyState title="No briefs match these filters" action={<Button size="sm" variant="secondary" onClick={clear}>Clear filters</Button>} />
                    : <EmptyState title="No briefs yet" body="Feeds are fetched every 30 minutes. New items from your sources will show up here." />
            ) : (
                <div className="space-y-2">
                    {items.map((i) => (
                        <ItemCard key={i.id} item={i} canBrief={canBrief} tz={tz}
                            onRead={() => { if (!i.read) markRead.mutate(i.id); }} onFilter={(next) => setParams(next)} />
                    ))}
                </div>
            )}
            <Pager page={page} pageSize={PAGE_SIZE} total={total} onPage={(n) => setParams({ page: String(n + 1) })} />
        </div>
    );
}

export default function BriefsPage() {
    const { data: me, isLoading } = useQuery({ queryKey: ['currentUser'], queryFn: async () => (await api.get('/users/me')).data as User, staleTime: 300_000 });
    const isAdmin = me?.role === 'admin' || me?.role === 'super_admin' || !!me?.is_super_admin;
    const canBrief = isAdmin || me?.role === 'analyst';
    const header = <PageHeader title="Security briefs" description="Recent vulnerabilities and incidents from public security feeds, with a short AI-written brief of each: what happened, why it matters, and what to learn from it." />;

    if (isLoading) return <PageContainer>{header}<Skeleton rows={6} /></PageContainer>;
    if (!me?.briefs_enabled) {
        return (
            <PageContainer>{header}
                <EmptyState title="Security briefs are off for this organization"
                    body={isAdmin ? <>Turn them on in <Link to="/settings/briefs" className="text-accent hover:underline">Settings → Briefs</Link>.</> : 'An admin can turn them on in Settings → Briefs.'} />
            </PageContainer>
        );
    }
    // Only the briefs: the switch and the sources are in Settings → Briefs.
    return <PageContainer>{header}<BriefList canBrief={canBrief} correlate={!!me.briefs_correlate} /></PageContainer>;
}
