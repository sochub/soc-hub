import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Bell, BellOff } from 'lucide-react';
import { api } from '../../api/client';
import { cn } from '../../lib/utils';

export default function FollowButton({ caseId, isOwner }: { caseId: number; isOwner: boolean }) {
    const qc = useQueryClient();
    const key = ['case-follow', String(caseId)];
    const { data, isLoading } = useQuery({
        queryKey: key,
        queryFn: async () => (await api.get(`/cases/${caseId}/follow`)).data.following as boolean,
    });
    const toggle = useMutation({
        mutationFn: async (follow: boolean) => {
            if (follow) await api.put(`/cases/${caseId}/follow`);
            else await api.delete(`/cases/${caseId}/follow`);
        },
        onSettled: () => qc.invalidateQueries({ queryKey: key }),
    });

    const following = !!data;
    if (isOwner) {
        return (
            <span
                title="Owners always follow their case"
                className="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium border bg-accent text-white border-accent opacity-60 cursor-default"
            >
                <Bell size={14} /> Following (owner)
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
                'group inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium border transition-colors disabled:opacity-50',
                following
                    ? 'bg-accent text-white border-accent hover:bg-red-600 hover:border-red-600'
                    : 'bg-white text-accent border-accent hover:bg-accent/10'
            )}
        >
            {following ? <Bell size={14} className="group-hover:hidden" /> : <Bell size={14} />}
            {following && <BellOff size={14} className="hidden group-hover:block" />}
            {following ? (
                <>
                    <span className="group-hover:hidden">Following</span>
                    <span className="hidden group-hover:inline">Unfollow</span>
                </>
            ) : 'Follow'}
        </button>
    );
}
