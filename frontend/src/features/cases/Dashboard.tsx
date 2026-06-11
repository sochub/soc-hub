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
    critical: '#dc2626', high: '#ea580c', medium: '#d97706', low: '#2563eb', info: '#64748b',
};
const STATUS_COLORS: Record<string, string> = {
    new: '#71717a', open: '#2563eb', in_progress: '#7c3aed',
    pending: '#d97706', resolved: '#059669', closed: '#475569',
};
const SEVERITY_ORDER = ['critical', 'high', 'medium', 'low', 'info'];
const STATUS_ORDER = ['new', 'open', 'in_progress', 'pending', 'resolved', 'closed'];

const TOOLTIP_STYLE = {
    contentStyle: {
        background: '#ffffff', border: '1px solid #e4e4e7', borderRadius: '0',
        color: '#18181b', fontSize: '11px', fontFamily: 'Roboto Mono, monospace',
        boxShadow: '0 4px 16px -4px rgba(24,24,27,0.15)',
    },
    labelStyle: { color: '#71717a', fontFamily: 'Roboto Mono, monospace', fontSize: '10px' },
};
const AXIS = { stroke: '#a1a1aa', fontSize: 10, fontFamily: 'Roboto Mono, monospace' };

/* ---------- small building blocks ---------- */

function Panel({ title, className, children, action }: {
    title: string; className?: string; children: React.ReactNode; action?: React.ReactNode;
}) {
    return (
        <section className={cn('bg-white border border-zinc-200 flex flex-col', className)}>
            <div className="flex items-center justify-between px-4 h-10 border-b border-zinc-200 shrink-0">
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
                {to && <Filter size={12} className="text-zinc-300 group-hover:text-accent-500 transition-colors" />}
            </div>
            <p className={cn('num text-3xl font-semibold mt-2 leading-none',
                accent ? 'text-accent-600' : 'text-zinc-900')}>{value}</p>
            {sub && <div className="mt-2 text-xs text-zinc-500">{sub}</div>}
        </>
    );
    const cls = cn(
        'group block bg-white border border-zinc-200 p-4 transition-all duration-150',
        'hover:border-accent-400 hover:shadow-console-hover hover:-translate-y-px',
        to && 'cursor-pointer',
    );
    return to ? <Link to={to} className={cls}>{inner}</Link> : <div className={cls}>{inner}</div>;
}

function SeverityDot({ s }: { s: string }) {
    return <span className="inline-block w-2 h-2 shrink-0" style={{ background: SEVERITY_COLORS[s] ?? '#64748b' }} />;
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
            <div className="flex items-center justify-center h-[60vh]">
                <p className="font-mono text-xs text-zinc-400 animate-pulse">$ loading telemetry…</p>
            </div>
        );
    }

    const weekDelta = stats && stats.cases_last_week > 0
        ? Math.round(((stats.cases_this_week - stats.cases_last_week) / stats.cases_last_week) * 100)
        : null;
    const fmtDate = (s: string) => new Date(s).toLocaleDateString(undefined, { month: 'short', day: 'numeric' });

    return (
        <div className="p-4 sm:p-6 max-w-[1600px] mx-auto">
            {/* Header */}
            <div className="flex items-end justify-between mb-5 flex-wrap gap-3">
                <div>
                    <h1 className="text-xl font-semibold tracking-tight text-zinc-900">Operations Overview</h1>
                    <p className="label-mono mt-1">soc_hub // case telemetry</p>
                </div>
                <button onClick={() => setShowNewCaseModal(true)}
                    className="inline-flex items-center gap-1.5 h-9 px-3.5 bg-accent-600 text-white text-sm font-medium hover:bg-accent-700 transition-colors">
                    <Plus size={15} /> New Case
                </button>
            </div>

            {/* KPI strip */}
            <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-6 gap-3 mb-3">
                <StatTile label="total cases" value={stats?.total_cases ?? 0} to="/cases" />
                <StatTile label="open" value={stats?.open_cases ?? 0} accent to="/cases?status=open"
                    sub={<span>oldest <span className="num text-zinc-700">{stats?.oldest_open_days ?? 0}d</span></span>} />
                <StatTile label="critical" value={stats?.critical_cases ?? 0} to="/cases?severity=critical"
                    sub={<span className="text-severity-critical font-mono text-[11px]">needs triage</span>} />
                <StatTile label="resolution rate" value={`${stats?.resolution_rate ?? 0}%`} />
                <StatTile label="mttr" value={stats?.mttr_hours != null ? `${stats.mttr_hours}h` : '—'} />
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
                            <CartesianGrid strokeDasharray="2 2" stroke="#e4e4e7" vertical={false} />
                            <XAxis dataKey="date" tickFormatter={fmtDate} {...AXIS} tickLine={false} />
                            <YAxis allowDecimals={false} {...AXIS} tickLine={false} axisLine={false} />
                            <Tooltip {...TOOLTIP_STYLE} labelFormatter={(v: any) => fmtDate(String(v))} />
                            <Line type="monotone" dataKey="opened" stroke="#2563eb" strokeWidth={2} dot={false} name="opened" />
                            <Line type="monotone" dataKey="resolved" stroke="#059669" strokeWidth={2} dot={false} name="resolved" />
                        </LineChart>
                    </ResponsiveContainer>
                </Panel>

                <Panel title="by severity" className="h-72">
                    <div className="flex h-full">
                        <ResponsiveContainer width="60%" height="100%">
                            <PieChart>
                                <Pie data={stats?.cases_by_severity ?? []} dataKey="count" nameKey="severity"
                                    cx="50%" cy="50%" innerRadius={42} outerRadius={64} paddingAngle={2} stroke="none">
                                    {(stats?.cases_by_severity ?? []).map(e => (
                                        <Cell key={e.severity} fill={SEVERITY_COLORS[e.severity] ?? '#64748b'} />
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
                                        <span className="flex items-center gap-2 text-zinc-600 capitalize"><SeverityDot s={s} />{s}</span>
                                        <span className="num text-zinc-900">{c}</span>
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
                                            <span className="flex items-center gap-1.5 text-xs text-zinc-600 capitalize"><SeverityDot s={sev} />{sev}</span>
                                        </td>
                                        {STATUS_ORDER.map(st => {
                                            const v = matrixLookup[`${sev}|${st}`] ?? 0;
                                            const intensity = v / matrixMax;
                                            return (
                                                <td key={st} className="px-1 py-0.5">
                                                    <div className="h-8 flex items-center justify-center border border-zinc-100 num text-xs"
                                                        style={{
                                                            background: v ? `rgba(37,99,235,${0.08 + intensity * 0.62})` : '#fafafa',
                                                            color: intensity > 0.55 ? '#fff' : v ? '#1e3a8a' : '#d4d4d8',
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
                                <div className="flex-1 h-5 bg-zinc-100 relative">
                                    <div className="h-full bg-accent-500/80 transition-all"
                                        style={{ width: `${(b.count / agingMax) * 100}%` }} />
                                </div>
                                <span className="num text-sm text-zinc-900 w-6 text-right">{b.count}</span>
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
                            <CartesianGrid strokeDasharray="2 2" stroke="#e4e4e7" vertical={false} />
                            <XAxis dataKey="status" tickFormatter={(s: string) => s.replace('_', ' ')} {...AXIS} tickLine={false} interval={0} angle={-20} textAnchor="end" height={42} />
                            <YAxis allowDecimals={false} {...AXIS} tickLine={false} axisLine={false} />
                            <Tooltip {...TOOLTIP_STYLE} cursor={{ fill: 'rgba(37,99,235,0.05)' }} />
                            <Bar dataKey="count" radius={[0, 0, 0, 0]}>
                                {(stats?.cases_by_status ?? []).map(e => <Cell key={e.status} fill={STATUS_COLORS[e.status] ?? '#2563eb'} />)}
                            </Bar>
                        </BarChart>
                    </ResponsiveContainer>
                </Panel>

                <Panel title="indicators" className="h-64"
                    action={<span className="num text-[11px] text-zinc-500">{stats?.total_iocs ?? 0} ioc · {stats?.total_artifacts ?? 0} art</span>}>
                    <ResponsiveContainer width="100%" height="100%">
                        <BarChart layout="vertical" data={[...(stats?.iocs_by_type ?? [])].sort((a, b) => b.count - a.count)}
                            margin={{ top: 4, right: 12, left: 8, bottom: 0 }}>
                            <CartesianGrid strokeDasharray="2 2" stroke="#e4e4e7" horizontal={false} />
                            <XAxis type="number" allowDecimals={false} {...AXIS} tickLine={false} axisLine={false} />
                            <YAxis type="category" dataKey="ioc_type" {...AXIS} tickLine={false} axisLine={false} width={66} />
                            <Tooltip {...TOOLTIP_STYLE} cursor={{ fill: 'rgba(37,99,235,0.05)' }} />
                            <Bar dataKey="count" fill="#2563eb" />
                        </BarChart>
                    </ResponsiveContainer>
                </Panel>

                <Panel title="top shared indicators" className="h-64 overflow-hidden">
                    {(stats?.top_artifacts?.length ?? 0) === 0 ? (
                        <p className="font-mono text-xs text-zinc-400">no linked artifacts yet</p>
                    ) : (
                        <ul className="divide-y divide-zinc-100 -mt-1">
                            {stats!.top_artifacts.map((a, i) => (
                                <li key={i} className="flex items-center justify-between gap-2 py-2">
                                    <span className="flex items-center gap-2 min-w-0">
                                        <span className="font-mono text-[9px] uppercase text-zinc-400 border border-zinc-200 px-1 shrink-0">{a.artifact_type}</span>
                                        <span className="num text-xs text-zinc-800 truncate">{a.value}</span>
                                    </span>
                                    <span className="num text-xs text-zinc-500 shrink-0">{a.case_count}×</span>
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
                        <p className="font-mono text-xs text-zinc-400">no cases yet</p>
                    ) : (
                        <ol className="relative border-l border-zinc-200 ml-1.5 mt-1">
                            {recentCases.map(c => (
                                <li key={c.id} className="ml-4 pb-3 last:pb-0 group">
                                    <span className="absolute -left-[5px] mt-1 w-2.5 h-2.5 border-2 border-white"
                                        style={{ background: SEVERITY_COLORS[c.severity] ?? '#64748b' }} />
                                    <Link to={`/cases/${c.id}`} className="block hover:bg-zinc-50 -mx-1 px-1 py-0.5 transition-colors">
                                        <div className="flex items-center justify-between gap-2">
                                            <span className="text-sm text-zinc-800 truncate group-hover:text-accent-700">{c.title}</span>
                                            <span className="num text-[10px] text-zinc-400 shrink-0">{fmtDate(c.created_at)}</span>
                                        </div>
                                        <span className="font-mono text-[10px] uppercase text-zinc-400">{c.severity} · {c.status.replace('_', ' ')}</span>
                                    </Link>
                                </li>
                            ))}
                        </ol>
                    )}
                </Panel>

                <Panel title="priority queue" className=""
                    action={<span className="num text-[11px] text-severity-critical">{priorityCases.length} active</span>}>
                    {priorityCases.length === 0 ? (
                        <p className="font-mono text-xs text-zinc-400">queue clear — no critical/high open</p>
                    ) : (
                        <ul className="divide-y divide-zinc-100 -mt-1">
                            {priorityCases.map(c => (
                                <li key={c.id}>
                                    <Link to={`/cases/${c.id}`}
                                        className="flex items-center justify-between gap-3 py-2.5 -mx-1 px-1 hover:bg-zinc-50 transition-colors group">
                                        <span className="flex items-center gap-2.5 min-w-0">
                                            <SeverityDot s={c.severity} />
                                            <span className="text-sm text-zinc-800 truncate group-hover:text-accent-700">{c.title}</span>
                                        </span>
                                        <span className="flex items-center gap-2 shrink-0">
                                            <span className="font-mono text-[10px] uppercase px-1.5 py-0.5 border"
                                                style={{ color: SEVERITY_COLORS[c.severity], borderColor: SEVERITY_COLORS[c.severity] + '55' }}>{c.severity}</span>
                                            <ArrowUpRight size={13} className="text-zinc-300 group-hover:text-accent-600" />
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
        </div>
    );
}
