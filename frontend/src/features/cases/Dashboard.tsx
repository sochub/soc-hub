import { useQuery } from '@tanstack/react-query';
import { api } from '../../api/client';
import { ArrowUpRight, ArrowDownRight, ChevronRight } from 'lucide-react';
import { cn } from '../../lib/utils';
import { Link, useNavigate } from 'react-router-dom';
import { useMemo, useState } from 'react';
import {
    LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
    PieChart, Pie, Cell,
} from 'recharts';
import PageContainer from '../../components/layout/PageContainer';
import { SLABadge } from './SLABadge';
import type { SLAStatus } from '../../types';
import {
    Card, CardHeader, CardTitle, CardContent, ErrorState, PageHeader, SeverityStamp, Skeleton, StatusStamp,
    SEVERITY_ORDER, TONE_VAR, severityTone,
} from '../../components/ui';
import { CASE_STATUSES, statusLabel } from './statuses';

interface CaseItem {
    id: number;
    title: string;
    description: string;
    severity: 'critical' | 'high' | 'medium' | 'low' | 'info';
    status: string;
    created_at: string;
    sla_overall_status?: SLAStatus | null;
    sla_response_status?: SLAStatus | null;
    sla_response_due_at?: string | null;
    sla_resolution_status?: SLAStatus | null;
    sla_resolution_due_at?: string | null;
}

interface Stats {
    total_cases: number;
    critical_cases: number;
    open_cases: number;
    resolution_rate: number;
    mttr_hours: number | null;
    cases_this_week: number;
    cases_last_week: number;
    active_ioc_count: number;
    total_artifacts: number;
    total_iocs: number;
    oldest_open_days: number;
    sla_compliance_pct: number | null;
    cases_over_time: { date: string; count: number }[];
    resolved_over_time: { date: string; count: number }[];
    cases_by_severity: { severity: string; count: number }[];
    cases_by_status: { status: string; count: number }[];
    iocs_by_type: { ioc_type: string; count: number }[];
    artifacts_by_type: { artifact_type: string; count: number }[];
    aging_buckets: { bucket: string; count: number }[];
    severity_status_matrix: { severity: string; status: string; count: number }[];
    top_artifacts: { value: string; artifact_type: string; case_count: number }[];
}

const STATUS_ORDER = CASE_STATUSES.map(s => s.value as string);
// "ip_address" -> "IP address"; known acronyms stay upper case.
const iocTypeLabel = (t: string) => t.split('_').map((w, i) => ['ip', 'url', 'md5', 'sha1', 'sha256'].includes(w) ? w.toUpperCase() : i ? w : w[0].toUpperCase() + w.slice(1)).join(' ');
const SEV_RANK: Record<string, number> = { critical: 0, high: 1, medium: 2, low: 3, info: 4 };
const isActive = (c: CaseItem) => c.status !== 'resolved' && c.status !== 'closed';
const atRisk = (c: CaseItem) => c.sla_overall_status === 'breached' || c.sla_overall_status === 'at_risk';

/** The SLA deadline that drives the case's overall state: the earliest due among the targets in that state. */
function slaDue(c: CaseItem): number | null {
    const st = c.sla_overall_status;
    const dues = ([[c.sla_response_status, c.sla_response_due_at], [c.sla_resolution_status, c.sla_resolution_due_at]] as const)
        .filter(([s, d]) => s === st && d).map(([, d]) => +new Date(d!));
    return dues.length ? Math.min(...dues) : null;
}
const fmtSpan = (ms: number) => {
    const m = Math.round(Math.abs(ms) / 60_000), d = Math.floor(m / 1440), h = Math.floor((m % 1440) / 60);
    return d ? `${d}d ${h}h` : h ? `${h}h ${m % 60}m` : `${m}m`;
};
// Charts read the theme tokens, so they follow light/dark like the rest of the page.
const sevColor = (s: string) => TONE_VAR[severityTone(s)];
const TOOLTIP_STYLE = {
    contentStyle: {
        background: 'var(--color-panel)', border: '1px solid var(--color-line-strong)', borderRadius: '6px',
        color: 'var(--color-fg)', fontSize: '12px', boxShadow: '0 8px 24px -12px rgb(0 0 0 / 0.35)',
    },
    labelStyle: { color: 'var(--color-fg-3)', fontSize: '12px' },
    itemStyle: { color: 'var(--color-fg)' },
};
const AXIS = { stroke: 'var(--color-line-strong)', tick: { fill: 'var(--color-fg-3)', fontSize: 12 } };
const GRID = 'var(--color-line)';

/* ---------- small building blocks ---------- */

function Panel({ title, className, children, action }: {
    title: string; className?: string; children: React.ReactNode; action?: React.ReactNode;
}) {
    return (
        <Card className={cn('flex flex-col', className)}>
            <CardHeader className="shrink-0">
                <CardTitle>{title}</CardTitle>
                {action}
            </CardHeader>
            <CardContent className="flex-1 min-h-0">{children}</CardContent>
        </Card>
    );
}

function StatTile({ label, value, sub, tone, to }: {
    label: string; value: React.ReactNode; sub?: React.ReactNode; tone?: string; to?: string;
}) {
    const inner = (
        <>
            <div className="flex items-center justify-between gap-2">
                <p className="text-xs font-medium text-fg-3">{label}</p>
                {to && <ChevronRight size={14} aria-hidden className="text-fg-3 group-hover:text-accent transition-colors" />}
            </div>
            <p className="num text-2xl font-semibold mt-1.5 leading-none text-fg" style={tone ? { color: tone } : undefined}>{value}</p>
            {sub && <div className="mt-2 text-xs text-fg-3">{sub}</div>}
        </>
    );
    // A cell in a ledger sheet: the grid's 1px gaps over a line-coloured ground draw the hairlines.
    const cls = cn('group block bg-panel px-4 py-3 transition-colors', to && 'hover:bg-raised');
    return to ? <Link to={to} className={cls}>{inner}</Link> : <div className={cls}>{inner}</div>;
}

/** In a list already scoped to SLA risk, "BREACHED" on every row says nothing; how far over (or how soon due) does. */
function SlaClock({ c, now }: { c: CaseItem; now: number }) {
    const due = slaDue(c);
    if (due == null) return c.sla_overall_status ? <SLABadge status={c.sla_overall_status} /> : null;
    const over = now - due;
    return (
        <span className={cn('num font-mono text-xs shrink-0 w-24 text-right', over > 0 ? 'text-severity-critical' : 'text-severity-high')}
            title={`SLA ${over > 0 ? 'breached' : 'due'} ${new Date(due).toLocaleString()}`}>
            {over > 0 ? `${fmtSpan(over)} over` : `due in ${fmtSpan(over)}`}
        </span>
    );
}

function SeverityDot({ s }: { s: string }) {
    return <span aria-hidden className="inline-block w-2 h-2 shrink-0 rounded-[2px]" style={{ background: sevColor(s) }} />;
}

/* ---------- dashboard ---------- */

export default function Dashboard() {
    const navigate = useNavigate();
    const [now] = useState(Date.now); // reference time for "over by" / "due in", fixed per visit

    const casesQ = useQuery<CaseItem[]>({
        queryKey: ['cases'],
        queryFn: async () => (await api.get('/cases/', { params: { limit: 500 } })).data,
    });
    const statsQ = useQuery<Stats>({
        queryKey: ['stats'],
        queryFn: async () => (await api.get('/stats/')).data,
    });
    const cases = casesQ.data;
    const stats = statsQ.data;

    // Lead with what needs attention: SLA breached first, then at risk, then active critical/high by severity.
    const active = useMemo(() => (cases ?? []).filter(isActive), [cases]);
    const priorityCases = useMemo(() =>
        active.filter(c => atRisk(c) || c.severity === 'critical' || c.severity === 'high')
            .sort((a, b) =>
                Number(b.sla_overall_status === 'breached') - Number(a.sla_overall_status === 'breached')
                || Number(atRisk(b)) - Number(atRisk(a))
                || (slaDue(a) ?? Infinity) - (slaDue(b) ?? Infinity) // the one that went over first leads
                || (SEV_RANK[a.severity] ?? 9) - (SEV_RANK[b.severity] ?? 9)),
        [active]);
    const counts = useMemo(() => ({
        active: active.length,
        critical: active.filter(c => c.severity === 'critical').length,
        high: active.filter(c => c.severity === 'high').length,
        sla: active.filter(atRisk).length,
    }), [active]);

    const recentCases = useMemo(() =>
        [...(cases ?? [])].sort((a, b) => +new Date(b.created_at) - +new Date(a.created_at)).slice(0, 7),
        [cases]);

    // merge opened + resolved trends by date
    const trend = useMemo(() => {
        const m = new Map<string, { date: string; opened: number; resolved: number }>();
        for (const d of stats?.cases_over_time ?? []) m.set(d.date, { date: d.date, opened: d.count, resolved: 0 });
        for (const d of stats?.resolved_over_time ?? []) {
            const e = m.get(d.date) ?? { date: d.date, opened: 0, resolved: 0 };
            e.resolved = d.count; m.set(d.date, e);
        }
        return [...m.values()].sort((a, b) => a.date.localeCompare(b.date));
    }, [stats]);

    const matrixMax = useMemo(() =>
        Math.max(1, ...(stats?.severity_status_matrix ?? []).map(c => c.count)), [stats]);
    const matrixLookup = useMemo(() => {
        const m: Record<string, number> = {};
        for (const c of stats?.severity_status_matrix ?? []) m[`${c.severity}|${c.status}`] = c.count;
        return m;
    }, [stats]);

    const agingMax = Math.max(1, ...(stats?.aging_buckets ?? []).map(b => b.count));
    const iocMax = Math.max(1, ...(stats?.iocs_by_type ?? []).map(t => t.count));

    const header = (
        <PageHeader
            title="Overview"
            className="mb-0"
        />
    );

    if (casesQ.isLoading || statsQ.isLoading) {
        return (
            <PageContainer width="wide">
                {header}
                <div className="grid grid-cols-1 lg:grid-cols-3 gap-3">
                    <Card className="lg:col-span-2 p-4"><Skeleton rows={6} /></Card>
                    <div className="grid grid-cols-2 gap-3">
                        {[0, 1, 2, 3].map(i => <div key={i} className="h-28 rounded-md bg-panel border border-line animate-pulse" />)}
                    </div>
                </div>
                <div className="grid grid-cols-1 lg:grid-cols-3 gap-3">
                    <div className="lg:col-span-2 h-72 rounded-md bg-panel border border-line animate-pulse" />
                    <div className="h-72 rounded-md bg-panel border border-line animate-pulse" />
                </div>
            </PageContainer>
        );
    }

    if (casesQ.isError || statsQ.isError) {
        return (
            <PageContainer width="wide">
                {header}
                <ErrorState message="Could not load the overview." onRetry={() => { casesQ.refetch(); statsQ.refetch(); }} />
            </PageContainer>
        );
    }

    const weekDelta = stats && stats.cases_last_week > 0
        ? Math.round(((stats.cases_this_week - stats.cases_last_week) / stats.cases_last_week) * 100)
        : null;
    const fmtDate = (s: string) => new Date(s).toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
    // Monday (UTC), matching the backend's cases_this_week.
    const monthAgo = new Date(now - 29 * 86_400_000).toISOString().slice(0, 10);
    const weekStart = (() => { const d = new Date(); d.setUTCDate(d.getUTCDate() - ((d.getUTCDay() + 6) % 7)); return d.toISOString().slice(0, 10); })();
    // Charts: clicking a point or bar opens the cases behind it (lists and links beside each chart give the keyboard path).
    const openDay = (st: { activeLabel?: string | number }) => { if (st?.activeLabel) navigate(`/cases?from=${st.activeLabel}&to=${st.activeLabel}`); };

    return (
        <PageContainer width="wide">
            {header}

            {/* Priority first: what needs attention now, with filtered drill-downs beside it */}
            <div className="grid grid-cols-1 lg:grid-cols-3 gap-3">
                <Card className="lg:col-span-2">
                    <CardHeader>
                        <CardTitle>Priority and SLA at risk <span className="num ml-1 font-normal text-fg-3">{priorityCases.length}</span></CardTitle>
                        <Link to="/cases?status=active&sla=at_risk,breached" className="text-xs text-accent hover:underline">View SLA at risk</Link>
                    </CardHeader>
                    {priorityCases.length === 0 ? (
                        <p className="px-4 py-8 text-center text-sm text-fg-3">Queue clear: no SLA pressure and no open critical or high cases.</p>
                    ) : (
                        <ul className="divide-y divide-line">
                            {priorityCases.slice(0, 8).map(c => (
                                <li key={c.id}>
                                    <Link to={`/cases/${c.id}`}
                                        className="flex items-center gap-3 px-4 py-2.5 hover:bg-raised transition-colors group">
                                        <SeverityStamp severity={c.severity} className="shrink-0" />
                                        <span className="num text-xs text-fg-3 shrink-0">#{c.id}</span>
                                        <span className="text-sm text-fg truncate flex-1 group-hover:text-accent">{c.title}</span>
                                        <StatusStamp status={c.status} className="hidden sm:inline-flex" />
                                        <SlaClock c={c} now={now} />
                                    </Link>
                                </li>
                            ))}
                            {priorityCases.length > 8 && (
                                <li className="px-4 py-2 text-xs text-fg-3">
                                    <span className="num">{priorityCases.length - 8}</span> more.{' '}
                                    <Link to="/cases?status=active" className="text-accent hover:underline">See all active cases</Link>
                                </li>
                            )}
                        </ul>
                    )}
                </Card>

                <div className="grid grid-cols-2 gap-px bg-line border border-line rounded-md overflow-hidden content-start self-start">
                    <StatTile label="SLA at risk or breached" value={counts.sla} to="/cases?status=active&sla=at_risk,breached"
                        tone={counts.sla ? TONE_VAR.critical : undefined} />
                    <StatTile label="Critical, active" value={counts.critical} to="/cases?severity=critical&status=active"
                        tone={counts.critical ? TONE_VAR.critical : undefined} />
                    <StatTile label="High, active" value={counts.high} to="/cases?severity=high&status=active"
                        tone={counts.high ? TONE_VAR.high : undefined} />
                    <StatTile label="Active cases" value={counts.active} to="/cases?status=active"
                        sub={<span>Oldest open <span className="num text-fg-2">{stats?.oldest_open_days ?? 0}d</span></span>} />
                </div>
            </div>

            {/* Health metrics */}
            <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-6 gap-px bg-line border border-line rounded-md overflow-hidden">
                <StatTile label="Total cases" value={stats?.total_cases ?? 0} to="/cases" />
                <StatTile label="Open" value={stats?.open_cases ?? 0} to="/cases?status=open" />
                <StatTile label="Resolution rate" value={`${stats?.resolution_rate ?? 0}%`} to="/cases?status=resolved" />
                <StatTile label="MTTR" value={stats?.mttr_hours != null ? `${stats.mttr_hours}h` : '—'} to="/cases?status=resolved" />
                <StatTile label="SLA compliance" value={stats?.sla_compliance_pct != null ? `${stats.sla_compliance_pct}%` : '—'} to="/cases?sla=breached"
                    tone={stats?.sla_compliance_pct != null && stats.sla_compliance_pct < 90 ? TONE_VAR.critical : undefined} />
                <StatTile label="This week" value={stats?.cases_this_week ?? 0} to={`/cases?from=${weekStart}`}
                    sub={weekDelta != null && (
                        <span className={cn('inline-flex items-center gap-0.5 num',
                            weekDelta >= 0 ? 'text-severity-critical' : 'text-lime')}>
                            {weekDelta >= 0 ? <ArrowUpRight size={12} aria-hidden /> : <ArrowDownRight size={12} aria-hidden />}
                            {Math.abs(weekDelta)}% vs last week
                        </span>
                    )} />
            </div>

            {/* Row: trend + severity donut */}
            <div className="grid grid-cols-1 lg:grid-cols-3 gap-3">
                <Panel title="Cases opened vs resolved, last 30 days" className="lg:col-span-2 h-88"
                    action={<Link to="/cases" className="text-xs text-accent hover:underline">All cases</Link>}>
                    <ResponsiveContainer width="100%" height="88%">
                        <LineChart data={trend} margin={{ top: 6, right: 12, left: -18, bottom: 0 }} onClick={openDay} className="cursor-pointer">
                            <CartesianGrid strokeDasharray="2 2" stroke={GRID} vertical={false} />
                            <XAxis dataKey="date" tickFormatter={fmtDate} {...AXIS} tickLine={false} />
                            <YAxis allowDecimals={false} {...AXIS} tickLine={false} axisLine={false} />
                            <Tooltip {...TOOLTIP_STYLE} labelFormatter={(v) => fmtDate(String(v))} />
                            <Line type="linear" dataKey="opened" stroke={TONE_VAR.accent} strokeWidth={2} dot={false} name="Opened" />
                            <Line type="linear" dataKey="resolved" stroke={TONE_VAR.ok} strokeWidth={2} dot={false} name="Resolved" />
                        </LineChart>
                    </ResponsiveContainer>
                    {/* Legend doubles as the keyboard path: clicking a day in the chart filters to that day. */}
                    <ul className="flex gap-4 text-xs mt-1">
                        <li><Link to={`/cases?from=${monthAgo}`} className="inline-flex items-center gap-1.5 text-fg-2 hover:text-accent"><span aria-hidden className="w-3 h-0.5" style={{ background: TONE_VAR.accent }} />Opened</Link></li>
                        <li><Link to="/cases?status=resolved" className="inline-flex items-center gap-1.5 text-fg-2 hover:text-accent"><span aria-hidden className="w-3 h-0.5" style={{ background: TONE_VAR.ok }} />Resolved</Link></li>
                    </ul>
                </Panel>

                <Panel title="By severity" className="h-80">
                    <div className="flex h-full">
                        <ResponsiveContainer width="60%" height="100%">
                            <PieChart>
                                <Pie isAnimationActive={false} data={stats?.cases_by_severity ?? []} dataKey="count" nameKey="severity"
                                    cx="50%" cy="50%" innerRadius={42} outerRadius={64} paddingAngle={2} stroke="none">
                                    {(stats?.cases_by_severity ?? []).map(e => (
                                        <Cell key={e.severity} fill={sevColor(e.severity)} className="cursor-pointer"
                                            onClick={() => navigate(`/cases?severity=${e.severity}`)} />
                                    ))}
                                </Pie>
                                <Tooltip {...TOOLTIP_STYLE} />
                            </PieChart>
                        </ResponsiveContainer>
                        <ul className="flex-1 flex flex-col justify-center gap-1.5 pr-1">
                            {SEVERITY_ORDER.map(s => {
                                const c = (stats?.cases_by_severity ?? []).find(x => x.severity === s)?.count ?? 0;
                                return (
                                    <li key={s}>
                                        <Link to={`/cases?severity=${s}`} className="flex items-center justify-between text-xs rounded-sm px-1 -mx-1 py-0.5 hover:bg-raised">
                                            <span className="flex items-center gap-2 text-fg-2 capitalize"><SeverityDot s={s} />{s}</span>
                                            <span className="num text-fg">{c}</span>
                                        </Link>
                                    </li>
                                );
                            })}
                        </ul>
                    </div>
                </Panel>
            </div>

            {/* Row: heatmap + aging */}
            <div className="grid grid-cols-1 lg:grid-cols-3 gap-3">
                <Panel title="Severity by status" className="lg:col-span-2">
                    <div className="overflow-x-auto">
                        <table className="w-full border-collapse">
                            <thead>
                                <tr>
                                    <th className="label-caps text-left pb-2 pr-2"><span className="sr-only">Severity</span></th>
                                    {STATUS_ORDER.map(st => (
                                        <th key={st} scope="col" className="label-caps pb-2 px-1 text-center">
                                            <Link to={`/cases?status=${st}`} className="hover:text-accent">{statusLabel(st)}</Link>
                                        </th>
                                    ))}
                                </tr>
                            </thead>
                            <tbody>
                                {SEVERITY_ORDER.map(sev => (
                                    <tr key={sev}>
                                        <th scope="row" className="pr-2 py-0.5 text-left font-normal">
                                            <Link to={`/cases?severity=${sev}`} className="flex items-center gap-1.5 text-xs text-fg-2 capitalize hover:text-accent"><SeverityDot s={sev} />{sev}</Link>
                                        </th>
                                        {STATUS_ORDER.map(st => {
                                            const v = matrixLookup[`${sev}|${st}`] ?? 0;
                                            const intensity = v / matrixMax;
                                            return (
                                                <td key={st} className="px-1 py-0.5">
                                                    <Link to={`/cases?severity=${sev}&status=${st}`}
                                                        className="h-8 flex items-center justify-center rounded-sm border border-line num text-xs hover:border-line-strong"
                                                        style={{
                                                            background: v ? `color-mix(in srgb, var(--color-accent) ${Math.round(10 + intensity * 75)}%, transparent)` : 'var(--color-surface)',
                                                            color: intensity > 0.55 ? 'var(--color-on-accent)' : v ? 'var(--color-fg)' : 'var(--color-muted)',
                                                        }}
                                                        aria-label={`${sev}, ${statusLabel(st)}: ${v} cases`}
                                                        title={`${sev} / ${statusLabel(st)}: ${v}`}>
                                                        {v || '·'}
                                                    </Link>
                                                </td>
                                            );
                                        })}
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                </Panel>

                <Panel title="Open case aging">
                    <ul className="space-y-2.5 mt-1">
                        {(stats?.aging_buckets ?? []).map(b => (
                            <li key={b.bucket}>
                                <Link to={`/cases?status=active&age=${encodeURIComponent(b.bucket)}`}
                                    aria-label={`Open for ${b.bucket}: ${b.count} cases`}
                                    className="flex items-center gap-3 rounded-sm -mx-1 px-1 py-0.5 hover:bg-raised group">
                                    <span className="num text-xs text-fg-3 w-12 text-right shrink-0">{b.bucket}</span>
                                    <div className="flex-1 h-5 bg-raised rounded-sm overflow-hidden">
                                        <div className="h-full bg-accent group-hover:bg-accent-hover transition-all"
                                            style={{ width: `${(b.count / agingMax) * 100}%` }} />
                                    </div>
                                    <span className="num text-sm text-fg w-6 text-right">{b.count}</span>
                                </Link>
                            </li>
                        ))}
                    </ul>
                </Panel>
            </div>

            {/* Row: indicators. Case status is already covered by the severity-by-status grid above. */}
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
                <Panel title="IOCs by type"
                    action={<Link to="/observables/iocs" className="num text-xs text-fg-3 hover:text-accent">{stats?.total_iocs ?? 0} IOCs · {stats?.total_artifacts ?? 0} artifacts</Link>}>
                    {(stats?.iocs_by_type?.length ?? 0) === 0 ? <p className="text-sm text-fg-3">No IOCs yet.</p> : (
                        <ul className="space-y-1.5">
                            {[...stats!.iocs_by_type].sort((a, b) => b.count - a.count).map(t => (
                                <li key={t.ioc_type}>
                                    <Link to={`/observables/iocs?type=${t.ioc_type}`} aria-label={`${iocTypeLabel(t.ioc_type)}: ${t.count} IOCs`}
                                        className="flex items-center gap-3 rounded-sm -mx-1 px-1 py-0.5 hover:bg-raised group">
                                        <span className="text-xs text-fg-2 w-28 shrink-0 truncate">{iocTypeLabel(t.ioc_type)}</span>
                                        <div className="flex-1 h-4 bg-raised rounded-sm overflow-hidden">
                                            <div className="h-full bg-accent group-hover:bg-accent-hover" style={{ width: `${(t.count / iocMax) * 100}%` }} />
                                        </div>
                                        <span className="num text-sm text-fg w-8 text-right">{t.count}</span>
                                    </Link>
                                </li>
                            ))}
                        </ul>
                    )}
                </Panel>

                <Panel title="Top shared indicators" className="overflow-hidden">
                    {(stats?.top_artifacts?.length ?? 0) === 0 ? (
                        <p className="text-sm text-fg-3">No artifacts shared across cases yet.</p>
                    ) : (
                        <ul className="divide-y divide-line -mt-1">
                            {stats!.top_artifacts.map((a, i) => (
                                <li key={i}>
                                    <Link to={`/observables?q=${encodeURIComponent(a.value)}`}
                                        className="flex items-center justify-between gap-2 py-2 -mx-1 px-1 rounded-sm hover:bg-raised group">
                                        <span className="flex items-center gap-2 min-w-0">
                                            <span className="text-xs text-fg-3 border border-line rounded-sm px-1 shrink-0">{a.artifact_type}</span>
                                            <span className="font-mono text-xs text-fg truncate group-hover:text-accent">{a.value}</span>
                                        </span>
                                        <span className="num text-xs text-fg-3 shrink-0">{a.case_count} cases</span>
                                    </Link>
                                </li>
                            ))}
                        </ul>
                    )}
                </Panel>
            </div>

            <Panel title="Recent cases" action={<Link to="/cases" className="text-xs text-accent hover:underline">All cases</Link>}>
                {recentCases.length === 0 ? (
                    <p className="text-sm text-fg-3">No cases yet.</p>
                ) : (
                    <ul className="divide-y divide-line -my-2">
                        {recentCases.map(c => (
                            <li key={c.id}>
                                <Link to={`/cases/${c.id}`} className="flex items-center gap-3 py-2 -mx-2 px-2 rounded-sm hover:bg-raised transition-colors group">
                                    <SeverityStamp severity={c.severity} className="shrink-0" />
                                    <span className="text-sm text-fg truncate flex-1 group-hover:text-accent">{c.title}</span>
                                    <StatusStamp status={c.status} className="hidden sm:inline-flex" />
                                    <span className="num text-xs text-fg-3 shrink-0 w-14 text-right">{fmtDate(c.created_at)}</span>
                                </Link>
                            </li>
                        ))}
                    </ul>
                )}
            </Panel>
        </PageContainer>
    );
}
