import { useState } from 'react';
import { Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { api } from '../../api/client';
import PageContainer from '../../components/layout/PageContainer';
import { Badge, Input, severityVariant } from '../../components/ui';

type Metric = 'current' | 'new' | 'open' | 'in_progress' | 'pending' | 'handled' | 'resolved' | 'closed';

interface Row {
    user_id: number; email: string; full_name: string | null; role: string;
    current: { new: number; open: number; in_progress: number; pending: number; total: number };
    handled: number; resolved: number; closed: number;
}
interface CaseRow { id: number; title: string; status: string; severity: string }

// Local calendar dates (toISOString would shift across midnight in non-UTC zones).
const ymd = (d: Date) =>
    `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
const monday = () => {
    const d = new Date();
    d.setDate(d.getDate() - ((d.getDay() + 6) % 7));
    return d;
};

const SNAPSHOT: [Metric, string][] = [['new', 'New'], ['open', 'Open'], ['in_progress', 'In progress'], ['pending', 'Pending'], ['current', 'Total']];
const PERIOD: [Metric, string][] = [['handled', 'Handled'], ['resolved', 'Resolved'], ['closed', 'Closed']];

export default function AnalystWorkload() {
    const [start, setStart] = useState(() => ymd(monday()));
    const [end, setEnd] = useState(() => ymd(new Date()));
    const [drill, setDrill] = useState<{ row: Row; metric: Metric; label: string } | null>(null);
    const valid = !!start && !!end && start <= end;

    const { data, isLoading, isError } = useQuery({
        queryKey: ['analyst-workload', start, end],
        queryFn: async () => (await api.get('/stats/analyst-workload', { params: { start, end } })).data as { analysts: Row[] },
        enabled: valid,
    });
    const cases = useQuery({
        queryKey: ['analyst-workload-cases', drill?.row.user_id, drill?.metric, start, end],
        queryFn: async () => (await api.get('/stats/analyst-workload/cases', {
            params: { user_id: drill!.row.user_id, metric: drill!.metric, start, end },
        })).data as { cases: CaseRow[] },
        enabled: valid && !!drill,
    });
    const rows = data?.analysts ?? [];
    const value = (r: Row, m: Metric) =>
        m === 'current' ? r.current.total : m in r.current ? r.current[m as keyof Row['current']] : r[m as 'handled' | 'resolved' | 'closed'];

    const cell = (r: Row, m: Metric, label: string) => {
        const n = value(r, m);
        const active = drill?.row.user_id === r.user_id && drill.metric === m;
        return (
            <td key={m} className="py-2 pr-4 text-right tabular-nums">
                {n === 0 ? <span className="text-muted">0</span> : (
                    <button type="button" onClick={() => setDrill(active ? null : { row: r, metric: m, label })}
                        className={`underline-offset-2 hover:underline ${active ? 'text-accent font-semibold' : 'text-fg'}`}
                        aria-label={`${label} cases for ${r.full_name || r.email}`}>
                        {n}
                    </button>
                )}
            </td>
        );
    };
    const range = start === end ? start : `${start} – ${end}`;

    return (
        <PageContainer>
            <div className="space-y-6">
            <div>
                <h1 className="text-3xl font-bold text-fg tracking-tight">Analyst workload</h1>
                <p className="text-sm text-muted mt-1">
                    Current workload is a live snapshot of cases each analyst owns. Period columns count distinct cases
                    for the selected dates: handled = owned at any time in the period; resolved and closed are credited
                    to whoever made the status change.
                </p>
            </div>
            <div className="flex flex-wrap items-end gap-3">
                <label className="text-xs text-muted">From
                    <Input type="date" value={start} onChange={(e) => { setStart(e.target.value); setDrill(null); }} className="mt-1" />
                </label>
                <label className="text-xs text-muted">To
                    <Input type="date" value={end} onChange={(e) => { setEnd(e.target.value); setDrill(null); }} className="mt-1" />
                </label>
            </div>
            {!valid && <p role="alert" className="text-sm text-severity-critical">Pick a valid date range.</p>}
            {isError && <p role="alert" className="text-sm text-severity-critical">Could not load analyst workload.</p>}
            {isLoading ? <p className="text-sm text-muted">Loading…</p> : valid && (
                <div className="overflow-x-auto">
                <table className="w-full text-sm">
                    <thead>
                        <tr className="text-xs uppercase tracking-wider text-muted">
                            <th />
                            <th colSpan={SNAPSHOT.length} className="pb-1 pr-4 text-right border-b border-line">Current (now)</th>
                            <th colSpan={PERIOD.length} className="pb-1 pl-4 text-right border-b border-line">{range}</th>
                        </tr>
                        <tr className="text-left text-xs uppercase tracking-wider text-muted border-b border-line">
                            <th className="py-2 pr-4">Analyst</th>
                            {[...SNAPSHOT, ...PERIOD].map(([m, l]) => <th key={m} className="pr-4 text-right">{l}</th>)}
                        </tr>
                    </thead>
                    <tbody>
                        {rows.map((r) => (
                            <tr key={r.user_id} className="border-b border-line/50 text-fg-2">
                                <td className="py-2 pr-4">
                                    <div className="text-fg">{r.full_name || r.email}</div>
                                    {r.full_name && <div className="text-xs text-muted">{r.email}</div>}
                                </td>
                                {SNAPSHOT.map(([m, l]) => cell(r, m, `Current ${l.toLowerCase()}`))}
                                {PERIOD.map(([m, l]) => cell(r, m, l))}
                            </tr>
                        ))}
                        {rows.length === 0 && <tr><td colSpan={9} className="py-4 text-muted">No analysts in this organization.</td></tr>}
                    </tbody>
                </table>
                </div>
            )}
            {drill && valid && (
                <section className="rounded-lg border border-line bg-raised p-4 space-y-3" aria-live="polite">
                    <div className="flex items-center justify-between gap-3">
                        <h2 className="text-sm font-semibold text-fg">
                            {drill.label} — {drill.row.full_name || drill.row.email}
                            {['handled', 'resolved', 'closed'].includes(drill.metric) && <span className="text-muted font-normal"> ({range})</span>}
                        </h2>
                        <button type="button" onClick={() => setDrill(null)} className="text-xs text-muted hover:text-fg">Close</button>
                    </div>
                    {cases.isLoading ? <p className="text-sm text-muted">Loading…</p>
                        : cases.isError ? <p role="alert" className="text-sm text-severity-critical">Could not load cases.</p>
                        : (
                            <ul className="divide-y divide-line/50">
                                {(cases.data?.cases ?? []).map((c) => (
                                    <li key={c.id} className="flex items-center gap-3 py-2 text-sm">
                                        <Link to={`/cases/${c.id}`} className="text-accent hover:underline">#{c.id} {c.title}</Link>
                                        <span className="ml-auto text-xs text-muted capitalize">{c.status.replace('_', ' ')}</span>
                                        <Badge variant={severityVariant(c.severity)} className="capitalize">{c.severity}</Badge>
                                    </li>
                                ))}
                                {cases.data?.cases.length === 0 && <li className="py-2 text-sm text-muted">No cases.</li>}
                            </ul>
                        )}
                </section>
            )}
            </div>
        </PageContainer>
    );
}
