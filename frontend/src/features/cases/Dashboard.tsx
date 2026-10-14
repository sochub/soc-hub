import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '../../api/client';
import { Plus, ArrowUpRight, ArrowDownRight, Filter } from 'lucide-react';
import { cn } from '../../lib/utils';
import { Link } from 'react-router-dom';
import { useState, useMemo } from 'react';
import NewCaseModal from './NewCaseModal';
import {
    LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
    PieChart, Pie, Cell, BarChart, Bar,
} from 'recharts';
import PageContainer from '../../components/layout/PageContainer';

interface CaseItem {
    id: number;
    title: string;
    description: string;
    severity: 'critical' | 'high' | 'medium' | 'low' | 'info';
    status: string;
    created_at: string;
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

const SEVERITY_COLORS: Record<string, string> = {
    critical: '#f87171', high: '#fb923c', medium: '#fbbf24', low: '#8fc3ff', info: '#8e8e95',
};
const STATUS_COLORS: Record<string, string> = {
    new: '#8e8e95', open: '#8fc3ff', in_progress: '#a4a8ff',
    pending: '#fbbf24', resolved: '#d6e37f', closed: '#6b6b72',
};
const SEVERITY_ORDER = ['critical', 'high', 'medium', 'low', 'info'];
const STATUS_ORDER = ['new', 'open', 'in_progress', 'pending', 'resolved', 'closed'];

const TOOLTIP_STYLE = {
    contentStyle: {
        background: '#18181b', border: '1px solid rgba(255,255,255,0.12)', borderRadius: '6px',
        color: '#f2f2f0', fontSize: '11px', fontFamily: 'Space Mono, monospace',
        boxShadow: '0 8px 24px -8px rgba(0,0,0,0.6)',
    },
    labelStyle: { color: '#8e8e95', fontFamily: 'Space Mono, monospace', fontSize: '10px' },
};
const AXIS = { stroke: '#6b6b72', fontSize: 10, fontFamily: 'Space Mono, monospace' };

/* ---------- small building blocks ---------- */

function Panel({ title, className, children, action }: {
    title: string; className?: string; children: React.ReactNode; action?: React.ReactNode;
}) {
    return (
        <section className={cn('bg-panel border border-line flex flex-col', className)}>
            <div className="flex items-center justify-between px-4 h-10 border-b border-line shrink-0">
                <h3 className="label-mono">// {title}</h3>
                {action}
            </div>
            <div className="p-4 flex-1 min-h-0">{children}</div>
        </section>
    );
}

function StatTile({ label, value, sub, accent, to }: {
    label: string; value: React.ReactNode; sub?: React.ReactNode; accent?: boolean; to?: string;
}) {
    const inner = (
        <>
            <div className="flex items-center justify-between">
                <p className="label-mono">{label}</p>
                {to && <Filter size={12} className="text-muted group-hover:text-accent transition-colors" />}
            </div>
            <p className={cn('num text-3xl font-semibold mt-2 leading-none',
                accent ? 'text-accent' : 'text-fg')}>{value}</p>
            {sub && <div className="mt-2 text-xs text-fg-3">{sub}</div>}
        </>
    );
    const cls = cn(
        'group block bg-panel border border-line p-4 transition-all duration-150',
        'hover:border-accent/40 hover:shadow-console-hover hover:-translate-y-px',
        to && 'cursor-pointer',
    );
    return to ? <Link to={to} className={cls}>{inner}</Link> : <div className={cls}>{inner}</div>;
}

function SeverityDot({ s }: { s: string }) {
    return <span className="inline-block w-2 h-2 shrink-0" style={{ background: SEVERITY_COLORS[s] ?? '#8e8e95' }} />;
}

/* ---------- dashboard ---------- */

export default function Dashboard() {
    const [showNewCaseModal, setShowNewCaseModal] = useState(false);
    const queryClient = useQueryClient();

    const { data: cases, isLoading: casesLoading } = useQuery<CaseItem[]>({
        queryKey: ['cases'],
        queryFn: async () => (await api.get('/cases/')).data,
    });
    const { data: stats, isLoading: statsLoading } = useQuery<Stats>({
        queryKey: ['stats'],
        queryFn: async () => (await api.get('/stats/')).data,
    });

    const createCaseMutation = useMutation({
        mutationFn: async (data: { title: string; description: string; severity: string; artifacts: any[]; playbook_template_id?: string }) => {
            const response = await api.post('/cases/', { title: data.title, description: data.description, severity: data.severity });
            if (data.artifacts?.length) {
                await Promise.all(data.artifacts.map(a =>
                    api.post('/artifacts/', { case_id: response.data.id, artifact_type: a.type, value: a.value, isolated: a.isolated ?? false })));
            }
            if (data.playbook_template_id) {
                await api.post(`/cases/${response.data.id}/apply-playbook/${data.playbook_template_id}`);
            }
            return response.data;
        },
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['cases'] });
            queryClient.invalidateQueries({ queryKey: ['stats'] });
            setShowNewCaseModal(false);
        },
    });

    const priorityCases = useMemo(() =>
        (cases ?? []).filter(c => (c.severity === 'critical' || c.severity === 'high') && c.status !== 'resolved' && c.status !== 'closed').slice(0, 6),
        [cases]);

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

    if (casesLoading || statsLoading) {
        return (
            <PageContainer width="wide">
                <div className="flex items-center justify-center h-[60vh]">
                    <p className="font-mono text-xs text-muted animate-pulse">$ loading telemetry…</p>
                </div>
            </PageContainer>
        );
    }

    const weekDelta = stats && stats.cases_last_week > 0
        ? Math.round(((stats.cases_this_week - stats.cases_last_week) / stats.cases_last_week) * 100)
        : null;
    const fmtDate = (s: string) => new Date(s).toLocaleDateString(undefined, { month: 'short', day: 'numeric' });

    return (
        <PageContainer width="wide" className="space-y-0">
            {/* Header */}
            <div className="flex items-end justify-between mb-5 flex-wrap gap-3">
                <div>
                    <h1 className="text-xl font-semibold tracking-tight text-fg">Overview</h1>
                    <p className="label-mono mt-1">soc_hub // case telemetry</p>
                </div>
                <button onClick={() => setShowNewCaseModal(true)}
                    className="inline-flex items-center gap-1.5 h-9 px-3.5 bg-accent text-on-accent text-sm font-medium hover:bg-accent-hover transition-colors">
                    <Plus size={15} /> New Case
                </button>
            </div>

            {/* KPI strip */}
            <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-7 gap-3 mb-3">
                <StatTile label="total cases" value={stats?.total_cases ?? 0} to="/cases" />
                <StatTile label="open" value={stats?.open_cases ?? 0} accent to="/cases?status=open"
                    sub={<span>oldest <span className="num text-fg-2">{stats?.oldest_open_days ?? 0}d</span></span>} />
                <StatTile label="critical" value={stats?.critical_cases ?? 0} to="/cases?severity=critical"
                    sub={<span className="text-severity-critical font-mono text-[11px]">needs triage</span>} />
                <StatTile label="resolution rate" value={`${stats?.resolution_rate ?? 0}%`} />
                <StatTile label="mttr" value={stats?.mttr_hours != null ? `${stats.mttr_hours}h` : '—'} />
                <StatTile label="sla compliance" value={stats?.sla_compliance_pct != null ? `${stats.sla_compliance_pct}%` : '—'} />
                <StatTile label="this week" value={stats?.cases_this_week ?? 0}
                    sub={weekDelta != null && (
                        <span className={cn('inline-flex items-center gap-0.5 font-mono text-[11px]',
                            weekDelta >= 0 ? 'text-severity-critical' : 'text-severity-low')}>
                            {weekDelta >= 0 ? <ArrowUpRight size={12} /> : <ArrowDownRight size={12} />}
                            {Math.abs(weekDelta)}% vs last
                        </span>
                    )} />
            </div>

            {/* Row: trend + severity donut */}
            <div className="grid grid-cols-1 lg:grid-cols-3 gap-3 mb-3">
                <Panel title="cases opened vs resolved · 30d" className="lg:col-span-2 h-72">
                    <ResponsiveContainer width="100%" height="100%">
                        <LineChart data={trend} margin={{ top: 6, right: 12, left: -18, bottom: 0 }}>
                            <CartesianGrid strokeDasharray="2 2" stroke="rgba(255,255,255,0.06)" vertical={false} />
                            <XAxis dataKey="date" tickFormatter={fmtDate} {...AXIS} tickLine={false} />
                            <YAxis allowDecimals={false} {...AXIS} tickLine={false} axisLine={false} />
                            <Tooltip {...TOOLTIP_STYLE} labelFormatter={(v: any) => fmtDate(String(v))} />
                            <Line type="monotone" dataKey="opened" stroke="#a4a8ff" strokeWidth={2} dot={false} name="opened" />
                            <Line type="monotone" dataKey="resolved" stroke="#d6e37f" strokeWidth={2} dot={false} name="resolved" />
                        </LineChart>
                    </ResponsiveContainer>
                </Panel>

                <Panel title="by severity" className="h-72">
                    <div className="flex h-full">
                        <ResponsiveContainer width="60%" height="100%">
                            <PieChart>
                                <Pie isAnimationActive={false} data={stats?.cases_by_severity ?? []} dataKey="count" nameKey="severity"
                                    cx="50%" cy="50%" innerRadius={42} outerRadius={64} paddingAngle={2} stroke="none">
                                    {(stats?.cases_by_severity ?? []).map(e => (
                                        <Cell key={e.severity} fill={SEVERITY_COLORS[e.severity] ?? '#8e8e95'} />
                                    ))}
                                </Pie>
                                <Tooltip {...TOOLTIP_STYLE} />
                            </PieChart>
                        </ResponsiveContainer>
                        <ul className="flex-1 flex flex-col justify-center gap-1.5 pr-1">
                            {SEVERITY_ORDER.map(s => {
                                const c = (stats?.cases_by_severity ?? []).find(x => x.severity === s)?.count ?? 0;
                                return (
                                    <li key={s} className="flex items-center justify-between text-xs">
                                        <span className="flex items-center gap-2 text-fg-2 capitalize"><SeverityDot s={s} />{s}</span>
                                        <span className="num text-fg">{c}</span>
                                    </li>
                                );
                            })}
                        </ul>
                    </div>
                </Panel>
            </div>

            {/* Row: heatmap + aging */}
            <div className="grid grid-cols-1 lg:grid-cols-3 gap-3 mb-3">
                <Panel title="severity × status heatmap" className="lg:col-span-2">
                    <div className="overflow-x-auto">
                        <table className="w-full border-collapse">
                            <thead>
                                <tr>
                                    <th className="label-mono text-left font-normal pb-2 pr-2"> </th>
                                    {STATUS_ORDER.map(st => (
                                        <th key={st} className="label-mono font-normal pb-2 px-1 text-center">{st.replace('_', ' ')}</th>
                                    ))}
                                </tr>
                            </thead>
                            <tbody>
                                {SEVERITY_ORDER.map(sev => (
                                    <tr key={sev}>
                                        <td className="pr-2 py-0.5">
                                            <span className="flex items-center gap-1.5 text-xs text-fg-2 capitalize"><SeverityDot s={sev} />{sev}</span>
                                        </td>
                                        {STATUS_ORDER.map(st => {
                                            const v = matrixLookup[`${sev}|${st}`] ?? 0;
                                            const intensity = v / matrixMax;
                                            return (
                                                <td key={st} className="px-1 py-0.5">
                                                    <div className="h-8 flex items-center justify-center border border-line num text-xs"
                                                        style={{
                                                            background: v ? `rgba(164,168,255,${0.08 + intensity * 0.72})` : 'rgba(255,255,255,0.02)',
                                                            color: intensity > 0.55 ? '#0a0a0a' : v ? '#c4c7ff' : '#3f3f46',
                                                        }}
                                                        title={`${sev} / ${st}: ${v}`}>
                                                        {v || '·'}
                                                    </div>
                                                </td>
                                            );
                                        })}
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                </Panel>

                <Panel title="open case aging">
                    <ul className="space-y-2.5 mt-1">
                        {(stats?.aging_buckets ?? []).map(b => (
                            <li key={b.bucket} className="flex items-center gap-3">
                                <span className="label-mono w-12 text-right shrink-0">{b.bucket}</span>
                                <div className="flex-1 h-5 bg-raised relative">
                                    <div className="h-full bg-accent/80 transition-all"
                                        style={{ width: `${(b.count / agingMax) * 100}%` }} />
                                </div>
                                <span className="num text-sm text-fg w-6 text-right">{b.count}</span>
                            </li>
                        ))}
                    </ul>
                </Panel>
            </div>

            {/* Row: status bar + ioc/artifact insights */}
            <div className="grid grid-cols-1 lg:grid-cols-3 gap-3 mb-3">
                <Panel title="cases by status" className="h-64">
                    <ResponsiveContainer width="100%" height="100%">
                        <BarChart data={stats?.cases_by_status ?? []} margin={{ top: 6, right: 8, left: -20, bottom: 0 }}>
                            <CartesianGrid strokeDasharray="2 2" stroke="rgba(255,255,255,0.06)" vertical={false} />
                            <XAxis dataKey="status" tickFormatter={(s: string) => s.replace('_', ' ')} {...AXIS} tickLine={false} interval={0} angle={-20} textAnchor="end" height={42} />
                            <YAxis allowDecimals={false} {...AXIS} tickLine={false} axisLine={false} />
                            <Tooltip {...TOOLTIP_STYLE} cursor={{ fill: 'rgba(164,168,255,0.06)' }} />
                            <Bar dataKey="count" radius={[0, 0, 0, 0]}>
                                {(stats?.cases_by_status ?? []).map(e => <Cell key={e.status} fill={STATUS_COLORS[e.status] ?? '#a4a8ff'} />)}
                            </Bar>
                        </BarChart>
                    </ResponsiveContainer>
                </Panel>

                <Panel title="indicators" className="h-64"
                    action={<span className="num text-[11px] text-fg-3">{stats?.total_iocs ?? 0} ioc · {stats?.total_artifacts ?? 0} art</span>}>
                    <ResponsiveContainer width="100%" height="100%">
                        <BarChart layout="vertical" data={[...(stats?.iocs_by_type ?? [])].sort((a, b) => b.count - a.count)}
                            margin={{ top: 4, right: 12, left: 8, bottom: 0 }}>
                            <CartesianGrid strokeDasharray="2 2" stroke="rgba(255,255,255,0.06)" horizontal={false} />
                            <XAxis type="number" allowDecimals={false} {...AXIS} tickLine={false} axisLine={false} />
                            <YAxis type="category" dataKey="ioc_type" {...AXIS} tickLine={false} axisLine={false} width={66} />
                            <Tooltip {...TOOLTIP_STYLE} cursor={{ fill: 'rgba(164,168,255,0.06)' }} />
                            <Bar dataKey="count" fill="#a4a8ff" />
                        </BarChart>
                    </ResponsiveContainer>
                </Panel>

                <Panel title="top shared indicators" className="h-64 overflow-hidden">
                    {(stats?.top_artifacts?.length ?? 0) === 0 ? (
                        <p className="font-mono text-xs text-muted">no linked artifacts yet</p>
                    ) : (
                        <ul className="divide-y divide-line -mt-1">
                            {stats!.top_artifacts.map((a, i) => (
                                <li key={i} className="flex items-center justify-between gap-2 py-2">
                                    <span className="flex items-center gap-2 min-w-0">
                                        <span className="font-mono text-[9px] uppercase text-muted border border-line px-1 shrink-0">{a.artifact_type}</span>
                                        <span className="num text-xs text-fg truncate">{a.value}</span>
                                    </span>
                                    <span className="num text-xs text-fg-3 shrink-0">{a.case_count}×</span>
                                </li>
                            ))}
                        </ul>
                    )}
                </Panel>
            </div>

            {/* Row: timeline + priority */}
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
                <Panel title="recent activity">
                    {recentCases.length === 0 ? (
                        <p className="font-mono text-xs text-muted">no cases yet</p>
                    ) : (
                        <ol className="relative border-l border-line ml-1.5 mt-1">
                            {recentCases.map(c => (
                                <li key={c.id} className="ml-4 pb-3 last:pb-0 group">
                                    <span className="absolute left-[-5px] mt-1 w-2.5 h-2.5 border-2 border-panel"
                                        style={{ background: SEVERITY_COLORS[c.severity] ?? '#8e8e95' }} />
                                    <Link to={`/cases/${c.id}`} className="block hover:bg-surface -mx-1 px-1 py-0.5 transition-colors">
                                        <div className="flex items-center justify-between gap-2">
                                            <span className="text-sm text-fg truncate group-hover:text-accent">{c.title}</span>
                                            <span className="num text-[10px] text-muted shrink-0">{fmtDate(c.created_at)}</span>
                                        </div>
                                        <span className="font-mono text-[10px] uppercase text-muted">{c.severity} · {c.status.replace('_', ' ')}</span>
                                    </Link>
                                </li>
                            ))}
                        </ol>
                    )}
                </Panel>

                <Panel title="priority queue" className=""
                    action={<span className="num text-[11px] text-severity-critical">{priorityCases.length} active</span>}>
                    {priorityCases.length === 0 ? (
                        <p className="font-mono text-xs text-muted">queue clear — no critical/high open</p>
                    ) : (
                        <ul className="divide-y divide-line -mt-1">
                            {priorityCases.map(c => (
                                <li key={c.id}>
                                    <Link to={`/cases/${c.id}`}
                                        className="flex items-center justify-between gap-3 py-2.5 -mx-1 px-1 hover:bg-surface transition-colors group">
                                        <span className="flex items-center gap-2.5 min-w-0">
                                            <SeverityDot s={c.severity} />
                                            <span className="text-sm text-fg truncate group-hover:text-accent">{c.title}</span>
                                        </span>
                                        <span className="flex items-center gap-2 shrink-0">
                                            <span className="font-mono text-[10px] uppercase px-1.5 py-0.5 border"
                                                style={{ color: SEVERITY_COLORS[c.severity], borderColor: SEVERITY_COLORS[c.severity] + '55' }}>{c.severity}</span>
                                            <ArrowUpRight size={13} className="text-muted group-hover:text-accent" />
                                        </span>
                                    </Link>
                                </li>
                            ))}
                        </ul>
                    )}
                </Panel>
            </div>

            <NewCaseModal
                show={showNewCaseModal}
                onClose={() => setShowNewCaseModal(false)}
                onSubmit={(data: any) => createCaseMutation.mutate(data)}
                isSubmitting={createCaseMutation.isPending}
            />
        </PageContainer>
    );
}
