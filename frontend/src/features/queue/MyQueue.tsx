import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { api } from '../../api/client';
import PageContainer from '../../components/layout/PageContainer';
import NotificationsPage from '../notifications/NotificationsPage';
import { SLABadge } from '../cases/SLABadge';
import { cn } from '../../lib/utils';
import type { Case, User } from '../../types';

const SEV_RANK: Record<string, number> = { critical: 0, high: 1, medium: 2, low: 3, info: 4 };
const SEV_DOT: Record<string, string> = {
    critical: 'bg-severity-critical', high: 'bg-severity-high', medium: 'bg-severity-medium',
    low: 'bg-severity-low', info: 'bg-severity-info',
};
const isOpen = (c: Case) => c.status !== 'resolved' && c.status !== 'closed';
const bySeverity = (a: Case, b: Case) => (SEV_RANK[a.severity] ?? 9) - (SEV_RANK[b.severity] ?? 9);

function Section({ title, cases, empty }: { title: string; cases: Case[]; empty: string }) {
    return (
        <section className="bg-panel border border-line rounded-lg">
            <header className="px-4 py-2.5 border-b border-line flex items-center justify-between">
                <span className="label-mono">// {title}</span>
                <span className="num text-xs text-fg-3">{cases.length}</span>
            </header>
            {cases.length === 0 ? (
                <p className="px-4 py-6 text-center text-sm text-muted">{empty}</p>
            ) : (
                <ul className="divide-y divide-line">
                    {cases.map(c => (
                        <li key={c.id}>
                            <Link to={`/cases/${c.id}`} className="flex items-center gap-3 px-4 py-2.5 hover:bg-raised transition-colors">
                                <span className={cn('w-2 h-2 rounded-full shrink-0', SEV_DOT[c.severity] ?? 'bg-muted')} title={c.severity} />
                                <span className="font-mono text-xs text-muted w-12 shrink-0">#{c.id}</span>
                                <span className="text-sm text-fg truncate flex-1">{c.title}</span>
                                <span className="text-xs text-fg-3 capitalize hidden sm:inline">{c.status.replace('_', ' ')}</span>
                                <SLABadge status={c.sla_overall_status} />
                            </Link>
                        </li>
                    ))}
                </ul>
            )}
        </section>
    );
}

/** Personal work queue: what needs me now, plus the notification inbox. */
export default function MyQueue() {
    const { data: me } = useQuery({
        queryKey: ['currentUser'],
        queryFn: async () => (await api.get('/users/me')).data as User,
        staleTime: 300_000,
    });
    const { data: cases = [], isLoading } = useQuery<Case[]>({
        queryKey: ['cases'],
        queryFn: async () => (await api.get('/cases/')).data,
    });

    const open = cases.filter(isOpen);
    const mine = open.filter(c => c.owner_id === me?.id).sort(bySeverity);
    const sla = open.filter(c => c.sla_overall_status === 'breached' || c.sla_overall_status === 'at_risk')
        .sort((a, b) => Number(b.sla_overall_status === 'breached') - Number(a.sla_overall_status === 'breached') || bySeverity(a, b));
    const unassigned = open.filter(c => c.owner_id == null).sort(bySeverity);

    return (
        <PageContainer width="wide">
            <div>
                <h1 className="font-display text-2xl font-semibold text-fg tracking-tight">My Queue</h1>
                <p className="text-sm text-fg-3 mt-0.5">What needs you right now.</p>
            </div>
            {isLoading ? (
                <div className="h-40 bg-panel border border-line rounded-lg animate-pulse" />
            ) : (
                <div className="grid grid-cols-1 xl:grid-cols-[minmax(0,1fr)_420px] gap-4 items-start">
                    <div className="space-y-4">
                        <Section title="assigned to me" cases={mine} empty="Nothing assigned to you." />
                        <Section title="sla at risk / breached" cases={sla} empty="No SLA pressure. Nice." />
                        <Section title="unassigned" cases={unassigned} empty="Every open case has an owner." />
                    </div>
                    <NotificationsPage bare />
                </div>
            )}
        </PageContainer>
    );
}
