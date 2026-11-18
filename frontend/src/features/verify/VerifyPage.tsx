import { useState } from 'react';
import { useParams } from 'react-router-dom';
import { useMutation, useQuery } from '@tanstack/react-query';
import { api } from '../../api/client';
import { Button, Skeleton } from '../../components/ui';
import AuthLayout from '../auth/AuthLayout';

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
    if (isLoading) return <AuthLayout title="Security check" wide><Skeleton rows={3} /></AuthLayout>;
    if (answered) {
        return (
            <AuthLayout title="Thanks, your answer was sent" wide>
                <p role="status" className="text-sm text-fg-2">You answered “{answered}”. You can close this page. If something looks wrong, contact your security team.</p>
            </AuthLayout>
        );
    }
    if (isError || !data || data.state === 'closed') {
        return (
            <AuthLayout title="This link has expired" wide
                description="The question was already answered or is no longer open. If you still have concerns, contact your security team." />
        );
    }
    return (
        <AuthLayout title="Please confirm this activity" description={`${data.organization} · security check`} wide
            footer="Never share passwords or codes. This page only records your answer.">
            <p className="text-sm text-fg-2 whitespace-pre-wrap border-l-2 border-line pl-3">{data.message}</p>
            <div className="flex flex-col gap-2">
                {data.buttons.map((b, i) => (
                    <Button key={b} size="lg" className="w-full" variant={i === 0 ? 'primary' : 'secondary'}
                        onClick={() => answer.mutate(i)} disabled={answer.isPending}>{b}</Button>
                ))}
            </div>
            {answer.isError && <p role="alert" className="text-sm text-severity-critical">This question was already answered or has expired.</p>}
        </AuthLayout>
    );
}
