import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Bell, BellOff } from 'lucide-react';
import { api } from '../../api/client';
import { cn } from '../../lib/utils';

export default function FollowButton({ caseId, isOwner, ready }: { caseId: number; isOwner: boolean; ready: boolean }) {
    const qc = useQueryClient();
    const key = ['case-follow', String(caseId)];
    const { data, isLoading } = useQuery({
        queryKey: key,
        queryFn: async () => (await api.get(`/cases/${caseId}/follow`)).data.following as boolean,
        enabled: ready && !isOwner,
    });
    const toggle = useMutation({
        mutationFn: async (follow: boolean) => {
            if (follow) await api.put(`/cases/${caseId}/follow`);
            else await api.delete(`/cases/${caseId}/follow`);
        },
        onSettled: () => qc.invalidateQueries({ queryKey: key }),
    });

    // Wait for /users/me so owners never flash a Follow button.
    if (!ready) return null;
    const following = !!data;
    if (isOwner) {
        return (
            <span
                title="Owners always follow their case"
                className="inline-flex items-center gap-1.5 h-8 px-2.5 text-xs whitespace-nowrap rounded-md border border-line text-fg-3 cursor-default"
            >
                <Bell size={14} className="text-accent" aria-hidden /> Following (owner)
            </span>
        );
    }
    return (
        <button
            type="button"
            onClick={() => toggle.mutate(!following)}
            disabled={isLoading || toggle.isPending}
            aria-pressed={following}
            className={cn(
                // Quiet toggle: "Close case" is the one filled action in the header.
                'group inline-flex items-center gap-1.5 h-8 px-3 text-xs font-medium whitespace-nowrap rounded-md border border-line-strong bg-panel transition-colors disabled:opacity-50',
                following
                    ? 'text-fg hover:text-severity-critical hover:border-severity-critical/40'
                    : 'text-fg hover:bg-raised'
            )}
        >
            {following ? <Bell size={14} className="group-hover:hidden group-focus-visible:hidden" aria-hidden /> : <Bell size={14} aria-hidden />}
            {following && <BellOff size={14} className="hidden group-hover:block group-focus-visible:block" aria-hidden />}
            {following ? (
                <>
                    <span className="group-hover:hidden group-focus-visible:hidden">Following</span>
                    <span className="hidden group-hover:inline group-focus-visible:inline">Unfollow</span>
                </>
            ) : 'Follow'}
        </button>
    );
}
