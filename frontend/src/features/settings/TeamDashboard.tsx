import { Link, useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { ArrowDownRight, ArrowUpRight, CheckCircle2 } from 'lucide-react';
import {
    AreaChart, Area, BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer,
} from 'recharts';
import { api } from '../../api/client';
import { cn, fmtDuration } from '../../lib/utils';
import { ErrorState } from '../../components/ui';

interface Kpi { value: number | null; previous: number | null }
interface CaseRef { id: number; title: string; severity: string }
interface Insight { id: string; level: 'critical' | 'warning' | 'info'; title: string; detail: string; count: number; cases: CaseRef[] }
interface TeamInsights {
    previous_start: string; previous_end: string;
    kpis: Record<'open_backlog' | 'unassigned_open' | 'created' | 'closed' | 'mtta_minutes' | 'mttr_minutes' | 'sla_breaches', Kpi>
        & { sla_breaches_split: { response: number; resolution: number } };
    trend: { date: string; created: number; closed: number }[];
    backlog_by_analyst: { user_id: number | null; name: string; new: number; open: number; in_progress: number; pending: number; total: number }[];
    aging: { bucket: string; critical: number; high: number; medium: number; low: number; info: number; total: number }[];
    outcomes: { outcome: string; label: string; count: number }[];
    false_positive_rate: number | null;
    closed_classified: number;
    insights: Insight[];
}

// Theme tokens (see index.css), so charts follow the app palette.
const tok = (n: string) => `var(--color-${n})`;
const SEVERITIES = ['critical', 'high', 'medium', 'low', 'info'] as const;
// Statuses are states, not ranks: no severity colours here (the severity ramp owns red/orange/amber/blue).
const STATUSES: [string, string, string][] = [
    ['new', 'New', tok('accent')], ['open', 'Open', tok('fg-2')],
    ['in_progress', 'In progress', tok('lavender')], ['pending', 'Pending', tok('muted')],
];
const OUTCOME_COLORS: Record<string, string> = {
    false_positive: tok('severity-medium'), true_positive_with_impact: tok('severity-critical'),
    true_positive_without_impact: tok('severity-high'), legitimate: tok('lime'), unknown: tok('fg-3'), not_set: tok('muted'),
};
const TOOLTIP = {
    contentStyle: {
        background: tok('raised'), border: `1px solid ${tok('line-strong')}`, borderRadius: '6px',
        color: tok('fg'), fontSize: '11px', fontFamily: 'var(--font-sans)',
    },
    labelStyle: { color: tok('fg-3'), fontFamily: 'var(--font-sans)', fontSize: '12px' },
    cursor: { fill: 'rgb(164 168 255 / 0.06)' },
};
const AXIS = { stroke: tok('muted'), fontSize: 12, fontFamily: 'var(--font-sans)', tickLine: false };
const GRID = { strokeDasharray: '2 2', stroke: tok('line') };
const LEGEND = { wrapperStyle: { fontSize: 11, color: tok('fg-3') }, iconSize: 8 };

function Panel({ title, action, className, children }: {
    title: string; action?: React.ReactNode; className?: string; children: React.ReactNode;
}) {
    return (
        <section className={cn('bg-panel border border-line rounded-md flex flex-col min-w-0', className)}>
            <div className="flex items-center justify-between gap-2 px-4 min-h-10 py-2 border-b border-line">
                <h2 className="text-sm font-semibold text-fg">{title}</h2>
                {action}
            </div>
            <div className="p-4 flex-1 min-h-0">{children}</div>
        </section>
    );
}

const Empty = ({ children }: { children: React.ReactNode }) =>
    <div className="h-full min-h-24 flex items-center justify-center text-sm text-muted">{children}</div>;

/** `better`: which direction of change is good (colours the delta); null = neutral. No `compare` = live count. */
function KpiTile({ label, kpi, better, duration, sub, compare, to }: {
    label: string; kpi: Kpi; better: 'up' | 'down' | null; duration?: boolean; sub?: string; compare?: string; to?: string;
}) {
    const fmt = (v: number | null) => (duration ? fmtDuration(v) : v == null ? '—' : String(v));
    const diff = kpi.value != null && kpi.previous != null ? kpi.value - kpi.previous : null;
    const good = diff == null || diff === 0 || better == null ? null : (diff > 0) === (better === 'up');
    return (
        <Tile to={to}>
            <p className="label-mono truncate" title={label}>{label}</p>
            <p className="num text-2xl font-semibold mt-2 leading-none text-fg">{fmt(kpi.value)}</p>
            <p className="mt-2 text-xs min-h-4">
                {!compare ? <span className="text-muted">live</span>
                    : diff == null ? <span className="text-muted">no data {compare}</span> : (
                        <span className={cn('inline-flex items-center gap-0.5',
                            good === null ? 'text-fg-3' : good ? 'text-lime' : 'text-severity-critical')}
                            title={`${compare}: ${fmt(kpi.previous)}`}>
                            {diff > 0 ? <ArrowUpRight size={12} /> : diff < 0 ? <ArrowDownRight size={12} /> : null}
                            {diff === 0 ? 'no change' : duration ? fmtDuration(Math.abs(diff)) : Math.abs(diff)}
                            <span className="text-muted ml-1">{compare}</span>
                        </span>
                    )}
            </p>
            {sub && <p className="mt-1 text-xs text-muted">{sub}</p>}
        </Tile>
    );
}

/** A KPI with a `to` opens the cases behind it. */
function Tile({ to, children }: { to?: string; children: React.ReactNode }) {
    const cls = 'block bg-panel border border-line rounded-md p-4 min-w-0';
    return to ? <Link to={to} className={cn(cls, 'hover:border-accent/50 hover:bg-raised transition-colors')}>{children}</Link>
        : <div className={cls}>{children}</div>;
}

const LEVEL_DOT: Record<Insight['level'], string> = {
    critical: 'bg-severity-critical', warning: 'bg-severity-medium', info: 'bg-accent',
};

function Insights({ items }: { items: Insight[] }) {
    if (items.length === 0) {
        return (
            <p className="flex items-center gap-2 text-sm text-fg-2">
                <CheckCircle2 size={16} className="text-lime shrink-0" /> All clear. No rule fired for this range.
            </p>
        );
    }
    return (
        <ul className="divide-y divide-line/50">
            {items.map((i) => (
                <li key={i.id} className="py-2.5 first:pt-0 last:pb-0">
                    <div className="flex items-start gap-2">
                        <span className={cn('mt-1.5 h-2 w-2 shrink-0 rounded-full', LEVEL_DOT[i.level])} aria-label={i.level} />
                        <div className="min-w-0">
                            <p className="text-sm text-fg font-medium">{i.title}</p>
                            <p className="text-xs text-fg-3">{i.detail}</p>
                            {i.cases.length > 0 && (
                                <div className="mt-1.5 flex flex-wrap gap-1.5">
                                    {i.cases.map((c) => (
                                        <Link key={c.id} to={`/cases/${c.id}`} title={c.title}
                                            className="inline-flex max-w-56 items-center gap-1 rounded border border-line px-1.5 py-0.5 text-xs text-fg-2 hover:border-accent/50 hover:text-accent">
                                            <span className="h-1.5 w-1.5 shrink-0" style={{ background: tok(`severity-${c.severity}`) }} />
                                            <span className="num">#{c.id}</span>
                                            <span className="truncate">{c.title}</span>
                                        </Link>
                                    ))}
                                    {i.count > i.cases.length && <span className="text-xs text-muted self-center">+{i.count - i.cases.length} more</span>}
                                </div>
                            )}
                        </div>
                    </div>
                </li>
            ))}
        </ul>
    );
}

const shortDate = (d: string) => d.slice(5);
const truncate = (s: string) => (s.length > 16 ? `${s.slice(0, 15)}…` : s);

export default function TeamDashboard({ start, end }: { start: string; end: string }) {
    const { data, isLoading, isError, refetch } = useQuery({
        queryKey: ['team-insights', start, end],
        queryFn: async () => (await api.get('/stats/team-insights', { params: { start, end } })).data as TeamInsights,
    });
    const navigate = useNavigate();
    const range = `from=${start}&to=${end}`;
    if (isLoading) return <p className="text-sm text-muted">Loading dashboard…</p>;
    if (isError || !data) return <ErrorState message="Could not load the team dashboard. Check that the API is reachable, then retry." onRetry={() => refetch()} />;

    const k = data.kpis;
    const prev = 'vs prev';
    const hasBacklog = data.backlog_by_analyst.length > 0;
    const fp = data.false_positive_rate;
    const outcomeMax = Math.max(1, ...data.outcomes.map((o) => o.count));

    return (
        <div className="space-y-3">
            <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 2xl:grid-cols-7 gap-3">
                <KpiTile label="Open backlog" kpi={k.open_backlog} better="down" compare="vs range start" to="/cases?status=active" />
                <KpiTile label="Unassigned open" kpi={k.unassigned_open} better={null} to="/cases?status=active&owner=none" />
                <KpiTile label="Created" kpi={k.created} better={null} compare={prev} to={`/cases?${range}`} />
                <KpiTile label="Closed" kpi={k.closed} better="up" compare={prev} to="/cases?status=closed" />
                <KpiTile label="Median time to ack" kpi={k.mtta_minutes} better="down" duration compare={prev} />
                <KpiTile label="Median time to resolve" kpi={k.mttr_minutes} better="down" duration compare={prev} />
                <KpiTile label="SLA breaches" kpi={k.sla_breaches} better="down" compare={prev} to="/cases?sla=breached"
                    sub={`${k.sla_breaches_split.response} response · ${k.sla_breaches_split.resolution} resolution`} />
            </div>
            <p className="text-xs text-muted">
                Previous period: {data.previous_start} – {data.previous_end} (UTC). Backlog and unassigned are live counts.
            </p>

            <Panel title="Insights">
                <Insights items={data.insights} />
            </Panel>

            <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
                <Panel title="Created vs closed per day" className="h-72">
                    <ResponsiveContainer width="100%" height="100%">
                        <AreaChart data={data.trend} margin={{ top: 6, right: 12, left: -18, bottom: 0 }} className="cursor-pointer"
                            onClick={(st: { activeLabel?: string | number }) => st?.activeLabel && navigate(`/cases?from=${st.activeLabel}&to=${st.activeLabel}`)}>
                            <CartesianGrid {...GRID} vertical={false} />
                            <XAxis dataKey="date" tickFormatter={shortDate} {...AXIS} minTickGap={16} />
                            <YAxis allowDecimals={false} {...AXIS} axisLine={false} />
                            <Tooltip {...TOOLTIP} cursor={{ stroke: tok('line-strong') }} />
                            <Legend {...LEGEND} />
                            <Area type="monotone" dataKey="created" name="Created" stroke={tok('accent')} fill={tok('accent')} fillOpacity={0.12} strokeWidth={2} isAnimationActive={false} />
                            <Area type="monotone" dataKey="closed" name="Closed" stroke={tok('lime')} fill={tok('lime')} fillOpacity={0.08} strokeWidth={2} isAnimationActive={false} />
                        </AreaChart>
                    </ResponsiveContainer>
                </Panel>

                <Panel title="Open backlog by analyst" className="min-h-72">
                    {!hasBacklog ? <Empty>No open cases.</Empty> : (
                        <div style={{ height: Math.max(200, data.backlog_by_analyst.length * 30 + 50) }}>
                            <ResponsiveContainer width="100%" height="100%">
                                <BarChart layout="vertical" data={data.backlog_by_analyst} margin={{ top: 0, right: 12, left: 0, bottom: 0 }}>
                                    <CartesianGrid {...GRID} horizontal={false} />
                                    <XAxis type="number" allowDecimals={false} {...AXIS} axisLine={false} />
                                    <YAxis type="category" dataKey="name" tickFormatter={truncate} {...AXIS} axisLine={false} width={110} interval={0} />
                                    <Tooltip {...TOOLTIP} />
                                    <Legend {...LEGEND} />
                                    {STATUSES.map(([key, label, color]) => (
                                        <Bar key={key} dataKey={key} name={label} stackId="s" fill={color} isAnimationActive={false} className="cursor-pointer"
                                            onClick={(_: unknown, i: number) => { const r = data.backlog_by_analyst[i]; if (r) navigate(`/cases?status=${key}&owner=${r.user_id ?? 'none'}`); }} />
                                    ))}
                                </BarChart>
                            </ResponsiveContainer>
                        </div>
                    )}
                </Panel>

                <Panel title="Open case aging" className="h-72">
                    {data.aging.every((b) => b.total === 0) ? <Empty>No open cases.</Empty> : (
                        <ResponsiveContainer width="100%" height="100%">
                            <BarChart data={data.aging} margin={{ top: 6, right: 8, left: -20, bottom: 0 }}>
                                <CartesianGrid {...GRID} vertical={false} />
                                <XAxis dataKey="bucket" {...AXIS} interval={0} />
                                <YAxis allowDecimals={false} {...AXIS} axisLine={false} />
                                <Tooltip {...TOOLTIP} />
                                <Legend {...LEGEND} />
                                {SEVERITIES.map((s) => (
                                    <Bar key={s} dataKey={s} name={s[0].toUpperCase() + s.slice(1)} stackId="a" fill={tok(`severity-${s}`)} isAnimationActive={false} className="cursor-pointer"
                                        onClick={(_: unknown, i: number) => { const b = data.aging[i]; if (b) navigate(`/cases?status=active&severity=${s}&age=${encodeURIComponent(b.bucket)}`); }} />
                                ))}
                            </BarChart>
                        </ResponsiveContainer>
                    )}
                </Panel>

                <Panel title="Outcomes of closed cases" className="min-h-72"
                    action={<span className="num text-[11px] text-fg-3" title="False positives / closed cases with a known outcome">
                        FP rate {fp == null ? '—' : `${Math.round(fp * 100)}%`}
                        {data.closed_classified > 0 && <span className="text-muted"> of {data.closed_classified}</span>}
                    </span>}>
                    {data.outcomes.length === 0 ? <Empty>No cases closed in this range.</Empty> : (
                        <ul className="space-y-1.5">
                            {data.outcomes.map((o) => (
                                <li key={o.outcome}>
                                    <Link to={`/cases?status=closed&outcome=${o.outcome}`} aria-label={`${o.label}: ${o.count} closed cases`}
                                        className="flex items-center gap-3 rounded-sm -mx-1 px-1 py-0.5 hover:bg-raised">
                                        <span className="text-xs text-fg-2 w-40 shrink-0 truncate">{o.label}</span>
                                        <div className="flex-1 h-4 bg-raised rounded-sm overflow-hidden">
                                            <div className="h-full" style={{ width: `${(o.count / outcomeMax) * 100}%`, background: OUTCOME_COLORS[o.outcome] ?? tok('accent') }} />
                                        </div>
                                        <span className="num text-sm text-fg w-8 text-right">{o.count}</span>
                                    </Link>
                                </li>
                            ))}
                        </ul>
                    )}
                </Panel>
            </div>
        </div>
    );
}
