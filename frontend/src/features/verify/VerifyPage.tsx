import { useState } from 'react';
import { useParams } from 'react-router-dom';
import { useMutation, useQuery } from '@tanstack/react-query';
import { api } from '../../api/client';

type Question = { state: 'open'; organization: string; message: string; buttons: string[]; expires_at: string | null } | { state: 'closed' };

/** Public page an email_ask_user email links to. Loading it records nothing; the answer is a button press. */
export default function VerifyPage() {
    const { token = '' } = useParams();
    const [answered, setAnswered] = useState<string | null>(null);
    const { data, isLoading, isError } = useQuery({
        queryKey: ['user-verification', token], retry: false,
        queryFn: async () => (await api.get(`/user-verification/${encodeURIComponent(token)}`)).data as Question,
    });
    const answer = useMutation({
        mutationFn: async (index: number) => (await api.post(`/user-verification/${encodeURIComponent(token)}`, { index })).data as { response: string },
        onSuccess: (r) => setAnswered(r.response),
    });
    const box = 'w-full max-w-md bg-panel border border-line rounded-xl p-6 space-y-4';
    return (
        <div className="min-h-screen bg-canvas text-fg flex items-center justify-center p-4">
            {isLoading ? <p className="text-sm text-muted">Loading…</p>
                : answered ? (
                    <div className={box} role="status">
                        <h1 className="text-lg font-semibold">Thanks, your answer was sent</h1>
                        <p className="text-sm text-fg-2">You answered “{answered}”. You can close this page. If something looks wrong, contact your security team.</p>
                    </div>
                ) : isError || !data || data.state === 'closed' ? (
                    <div className={box}>
                        <h1 className="text-lg font-semibold">This link has expired</h1>
                        <p className="text-sm text-fg-2">The question was already answered or is no longer open. If you still have concerns, contact your security team.</p>
                    </div>
                ) : (
                    <div className={box}>
                        <p className="label-mono">{data.organization} · security check</p>
                        <h1 className="text-lg font-semibold">Please confirm this activity</h1>
                        <p className="text-sm text-fg-2 whitespace-pre-wrap border-l-2 border-line pl-3">{data.message}</p>
                        <div className="flex flex-col gap-2">
                            {data.buttons.map((b, i) => (
                                <button key={b} onClick={() => answer.mutate(i)} disabled={answer.isPending}
                                    className={i === 0 ? 'px-4 py-2 rounded-lg bg-accent text-on-accent font-medium hover:bg-accent-hover disabled:opacity-50'
                                        : 'px-4 py-2 rounded-lg border border-line-strong text-fg hover:bg-raised disabled:opacity-50'}>{b}</button>
                            ))}
                        </div>
                        {answer.isError && <p role="alert" className="text-sm text-severity-critical">This question was already answered or has expired.</p>}
                        <p className="text-xs text-muted">Never share passwords or codes. This page only records your answer.</p>
                    </div>
                )}
        </div>
    );
}
