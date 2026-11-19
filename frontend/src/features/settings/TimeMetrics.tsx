import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api } from '../../api/client';
import { Button, ErrorState, Input, SeverityStamp, Skeleton } from '../../components/ui';
import SettingsPageHeader from './SettingsPageHeader';
import { formatDuration } from '../cases/timeApi';

interface Row { user_email: string; case_id: number; case_title: string; severity: string; seconds: number }

const day = (d: Date) => d.toISOString().slice(0, 10);

export default function TimeMetrics() {
    const [start, setStart] = useState(() => day(new Date(Date.now() - 30 * 86_400_000)));
    const [end, setEnd] = useState(() => day(new Date()));
    const [error, setError] = useState<string | null>(null);
    const [busy, setBusy] = useState(false);

    // The API takes dates and treats the end day as inclusive.
    const params = (format: string) => ({ start, end, format });
    const valid = !!start && !!end && start <= end;

    const { data, isLoading, isError, refetch } = useQuery({
        queryKey: ['time-metrics', start, end],
        queryFn: async () => (await api.get('/stats/time', { params: params('json') })).data as { rows: Row[] },
        enabled: valid,
    });
    const rows = data?.rows ?? [];
    const total = rows.reduce((s, r) => s + r.seconds, 0);

    const download = async () => {
        setBusy(true); setError(null);
        try {
            const resp = await api.get('/stats/time', { params: params('csv'), responseType: 'blob' });
            const url = URL.createObjectURL(resp.data as Blob);
            const link = document.createElement('a');
            link.href = url; link.download = `time-${start}-${end}.csv`;
            document.body.appendChild(link); link.click(); link.remove();
            setTimeout(() => URL.revokeObjectURL(url), 1000);
        } catch {
            setError('Could not download the CSV.');
        } finally { setBusy(false); }
    };

    return (
            <div className="space-y-6">
            <SettingsPageHeader title="Time spent" description="Time analysts logged on cases, by day range." />
            <div className="flex flex-wrap items-end gap-3">
                <label className="text-xs font-medium text-fg-2">From
                    <Input type="date" value={start} onChange={(e) => setStart(e.target.value)} className="mt-1" />
                </label>
                <label className="text-xs font-medium text-fg-2">To
                    <Input type="date" value={end} onChange={(e) => setEnd(e.target.value)} className="mt-1" />
                </label>
                <Button variant="secondary" disabled={!valid || busy} onClick={download}>Download CSV</Button>
            </div>
            {!valid && <p role="alert" className="text-sm text-severity-critical">Pick a valid date range.</p>}
            {error && <p role="alert" className="text-sm text-severity-critical">{error}</p>}
            {isError && <ErrorState message="Could not load time metrics." onRetry={() => refetch()} />}
            {isLoading ? <Skeleton rows={5} /> : valid && !isError && (
                <div className="rounded-md border border-line bg-panel overflow-x-auto">
                <table className="w-full text-sm">
                    <thead>
                        <tr className="text-left label-caps border-b border-line">
                            <th className="px-3 py-2">Analyst</th><th className="px-3 py-2">Case</th><th className="px-3 py-2">Severity</th><th className="px-3 py-2 text-right">Time</th>
                        </tr>
                    </thead>
                    <tbody className="divide-y divide-line">
                        {rows.map((r, i) => (
                            <tr key={i} className="text-fg-2 hover:bg-raised">
                                <td className="px-3 py-2.5">{r.user_email}</td>
                                <td className="px-3 py-2.5"><span className="num text-fg-3">#{r.case_id}</span> {r.case_title}</td>
                                <td className="px-3 py-2.5"><SeverityStamp severity={r.severity} /></td>
                                <td className="px-3 py-2.5 text-right num">{formatDuration(r.seconds)}</td>
                            </tr>
                        ))}
                        {rows.length === 0 && <tr><td colSpan={4} className="px-3 py-6 text-center text-fg-3">No time recorded in this range.</td></tr>}
                        <tr className="font-semibold text-fg"><td className="px-3 py-2.5" colSpan={3}>Total</td><td className="px-3 py-2.5 text-right num">{formatDuration(total)}</td></tr>
                    </tbody>
                </table>
                </div>
            )}
            </div>
    );
}
