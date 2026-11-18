import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { api } from '../../api/client';
import PageContainer from '../../components/layout/PageContainer';
import NotificationsPage from '../notifications/NotificationsPage';
import { SLABadge } from '../cases/SLABadge';
import { Button, Card, CardHeader, CardTitle, ErrorState, PageHeader, SeverityStamp, Skeleton, StatusStamp } from '../../components/ui';
import type { Case, User } from '../../types';
import { errMsg, sinceShort, useEscalationAction, useEscalationQueue } from '../escalation/api';
import type { EscalationQueueRow } from '../../types';

const SEV_RANK: Record<string, number> = { critical: 0, high: 1, medium: 2, low: 3, info: 4 };
const isOpen = (c: Case) => c.status !== 'resolved' && c.status !== 'closed';
const bySeverity = (a: Case, b: Case) => (SEV_RANK[a.severity] ?? 9) - (SEV_RANK[b.severity] ?? 9);

function Section({ title, cases, empty }: { title: string; cases: Case[]; empty: string }) {
    return (
        <Card>
            <CardHeader>
                <CardTitle>{title} <span className="num ml-1 font-normal text-fg-3">{cases.length}</span></CardTitle>
            </CardHeader>
            {cases.length === 0 ? (
                <p className="px-4 py-6 text-center text-sm text-fg-3">{empty}</p>
            ) : (
                <ul className="divide-y divide-line">
                    {cases.map(c => (
                        <li key={c.id}>
                            <Link to={`/cases/${c.id}`} className="flex items-center gap-3 px-4 py-2.5 hover:bg-raised transition-colors">
                                <SeverityStamp severity={c.severity} className="shrink-0" />
                                <span className="num text-xs text-fg-3 w-12 shrink-0">#{c.id}</span>
                                <span className="text-sm text-fg truncate flex-1">{c.title}</span>
                                <StatusStamp status={c.status} className="hidden sm:inline-flex" />
                                <SLABadge status={c.sla_overall_status} />
                            </Link>
                        </li>
                    ))}
                </ul>
            )}
        </Card>
    );
}

function AcceptButton({ row }: { row: EscalationQueueRow }) {
    const accept = useEscalationAction(row.case_id, 'accept');
    return (
        <span className="flex items-center gap-2 shrink-0">
            {accept.isError && <span role="alert" className="text-xs text-severity-critical">{errMsg(accept.error, 'Could not accept')}</span>}
            <Button size="sm" variant="secondary" disabled={accept.isPending} onClick={() => accept.mutate({})}>Accept</Button>
        </span>
    );
}

function WaitingForMyGroups() {
    const { data: rows = [] } = useEscalationQueue();
    return (
        <Card>
            <CardHeader>
                <CardTitle>Waiting for my groups <span className="num ml-1 font-normal text-fg-3">{rows.length}</span></CardTitle>
            </CardHeader>
            {rows.length === 0 ? (
                <p className="px-4 py-6 text-center text-sm text-fg-3">No escalations waiting on your groups.</p>
            ) : (
                <ul className="divide-y divide-line">
                    {rows.map(r => (
                        <li key={r.id} className="flex items-center gap-3 px-4 py-2.5">
                            <SeverityStamp severity={r.severity} className="shrink-0" />
                            <Link to={`/cases/${r.case_id}`} className="flex-1 min-w-0 hover:underline">
                                <span className="num text-xs text-fg-3 mr-2">#{r.case_id}</span>
                                <span className="text-sm text-fg truncate">{r.case_title}</span>
                                <span className="block text-xs text-fg-3">
                                    Tier {r.tier_position} · {r.group_name ?? r.tier_name}{r.requested_by_name ? ` · from ${r.requested_by_name}` : ''} · {sinceShort(r.created_at)}
                                </span>
                            </Link>
                            <AcceptButton row={r} />
                        </li>
                    ))}
                </ul>
            )}
        </Card>
    );
}

/** Personal work queue: what needs me now, plus the notification inbox. */
export default function MyQueue() {
    const { data: me } = useQuery({
        queryKey: ['currentUser'],
        queryFn: async () => (await api.get('/users/me')).data as User,
        staleTime: 300_000,
    });
    const { data: cases = [], isLoading, isError, refetch } = useQuery<Case[]>({
        queryKey: ['cases'],
        queryFn: async () => (await api.get('/cases/', { params: { limit: 500 } })).data,
    });

    const open = cases.filter(isOpen);
    const mine = open.filter(c => c.owner_id === me?.id).sort(bySeverity);
    const sla = open.filter(c => c.sla_overall_status === 'breached' || c.sla_overall_status === 'at_risk')
        .sort((a, b) => Number(b.sla_overall_status === 'breached') - Number(a.sla_overall_status === 'breached') || bySeverity(a, b));
    const unassigned = open.filter(c => c.owner_id == null).sort(bySeverity);

    return (
        <PageContainer width="wide">
            <PageHeader title="My queue" description="What needs you right now." className="mb-0" />
            {isLoading ? (
                <Skeleton rows={8} />
            ) : isError ? (
                <ErrorState message="Could not load your cases." onRetry={() => refetch()} />
            ) : (
                <div className="grid grid-cols-1 xl:grid-cols-[minmax(0,1fr)_420px] gap-4 items-start">
                    <div className="space-y-4">
                        <WaitingForMyGroups />
                        <Section title="Assigned to me" cases={mine} empty="Nothing assigned to you." />
                        <Section title="SLA at risk or breached" cases={sla} empty="No SLA pressure. Nice." />
                        <Section title="Unassigned" cases={unassigned} empty="Every open case has an owner." />
                    </div>
                    <NotificationsPage bare />
                </div>
            )}
        </PageContainer>
    );
}
