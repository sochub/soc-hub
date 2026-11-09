import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api } from '../../api/client';
import PageContainer from '../../components/layout/PageContainer';
import { Button, Input } from '../../components/ui';
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

    const { data, isLoading, isError } = useQuery({
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
        <PageContainer>
            <div className="space-y-6">
            <h1 className="text-3xl font-bold text-fg tracking-tight">Time spent</h1>
            <div className="flex flex-wrap items-end gap-3">
                <label className="text-xs text-muted">From
                    <Input type="date" value={start} onChange={(e) => setStart(e.target.value)} className="mt-1" />
                </label>
                <label className="text-xs text-muted">To
                    <Input type="date" value={end} onChange={(e) => setEnd(e.target.value)} className="mt-1" />
                </label>
                <Button variant="secondary" disabled={!valid || busy} onClick={download}>Download CSV</Button>
            </div>
            {!valid && <p role="alert" className="text-sm text-severity-critical">Pick a valid date range.</p>}
            {error && <p role="alert" className="text-sm text-severity-critical">{error}</p>}
            {isError && <p role="alert" className="text-sm text-severity-critical">Could not load time metrics.</p>}
            {isLoading ? <p className="text-sm text-muted">Loading…</p> : valid && (
                <table className="w-full text-sm">
                    <thead>
                        <tr className="text-left text-xs uppercase tracking-wider text-muted border-b border-line">
                            <th className="py-2 pr-4">Analyst</th><th className="pr-4">Case</th><th className="pr-4">Severity</th><th className="text-right">Time</th>
                        </tr>
                    </thead>
                    <tbody>
                        {rows.map((r, i) => (
                            <tr key={i} className="border-b border-line/50 text-fg-2">
                                <td className="py-2 pr-4">{r.user_email}</td>
                                <td className="pr-4">#{r.case_id} {r.case_title}</td>
                                <td className="pr-4 capitalize">{r.severity}</td>
                                <td className="text-right">{formatDuration(r.seconds)}</td>
                            </tr>
                        ))}
                        {rows.length === 0 && <tr><td colSpan={4} className="py-4 text-muted">No time recorded in this range.</td></tr>}
                        <tr className="font-semibold text-fg"><td className="py-2" colSpan={3}>Total</td><td className="text-right">{formatDuration(total)}</td></tr>
                    </tbody>
                </table>
            )}
            </div>
        </PageContainer>
    );
}
