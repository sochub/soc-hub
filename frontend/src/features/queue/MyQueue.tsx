import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import PageContainer from '../../components/layout/PageContainer';
import NotificationsPage from '../notifications/NotificationsPage';
import { SLABadge } from '../cases/SLABadge';
import { Button, Card, CardHeader, CardTitle, ErrorState, PageHeader, SeverityStamp, Skeleton, StatusStamp } from '../../components/ui';
import type { Case } from '../../types';
import { errMsg, sinceShort, useEscalationAction, useEscalationQueue } from '../escalation/api';
import type { EscalationQueueRow } from '../../types';
import { getPage, OPEN_CASE_STATUSES, type Page } from '../../api/paging';

const SEV_RANK: Record<string, number> = { critical: 0, high: 1, medium: 2, low: 3, info: 4 };
const bySeverity = (a: Case, b: Case) => (SEV_RANK[a.severity] ?? 9) - (SEV_RANK[b.severity] ?? 9);
const SECTION_CAP = 100;

function Section({ title, page, empty }: { title: string; page: Page<Case>; empty: string }) {
    const { items: cases, total } = page;
    return (
        <Card>
            <CardHeader>
                <CardTitle>{title} <span className="num ml-1 font-normal text-fg-3">{total}</span></CardTitle>
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
            {total > cases.length && (
                <p className="px-4 py-2 border-t border-line text-xs text-fg-3">
                    Showing {cases.length} of {total}, most severe first. <Link to="/cases" className="text-accent hover:underline">All cases</Link>
                </p>
            )}
        </Card>
    );
}

/** One My Queue section: open cases matching `params`, most severe first. */
function useOpenCases(name: string, params: Record<string, string>) {
    return useQuery({
        queryKey: ['cases', 'queue', name],
        queryFn: () => getPage<Case>('/cases/', { ...params, status: OPEN_CASE_STATUSES, sort: 'severity', limit: SECTION_CAP }),
    });
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
    const empty: Page<Case> = { items: [], total: 0 };
    const mineQ = useOpenCases('mine', { owner: 'me' });
    const slaQ = useOpenCases('sla', { sla: 'pressure' });
    const unassignedQ = useOpenCases('unassigned', { owner: 'unassigned' });
    const isLoading = mineQ.isLoading || slaQ.isLoading || unassignedQ.isLoading;
    const mine = mineQ.data ?? empty;
    const unassigned = unassignedQ.data ?? empty;
    const slaPage = slaQ.data ?? empty;
    const sla = {
        ...slaPage,
        items: [...slaPage.items].sort((a, b) =>
            Number(b.sla_overall_status === 'breached') - Number(a.sla_overall_status === 'breached') || bySeverity(a, b)),
    };
    const isError = mineQ.isError || slaQ.isError || unassignedQ.isError;
    const refetch = () => { mineQ.refetch(); slaQ.refetch(); unassignedQ.refetch(); };

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
                        <Section title="Assigned to me" page={mine} empty="Nothing assigned to you." />
                        <Section title="SLA at risk or breached" page={sla} empty="No SLA pressure. Nice." />
                        <Section title="Unassigned" page={unassigned} empty="Every open case has an owner." />
                    </div>
                    <NotificationsPage bare />
                </div>
            )}
        </PageContainer>
    );
}
