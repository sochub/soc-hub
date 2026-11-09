import { useMutation, useQueryClient } from '@tanstack/react-query';
import { AlertTriangle, Bot, ExternalLink } from 'lucide-react';
import { api } from '../../api/client';
import { cn } from '../../lib/utils';
import type { Case } from '../../types';

interface Props { caseData: Case; canEdit: boolean }

export default function WarRoomPill({ caseData, canEdit }: Props) {
    const qc = useQueryClient();
    const toggle = useMutation({
        mutationFn: async () => (await api.patch(`/cases/${caseData.id}/war-room`, { listen: !caseData.slack_listen })).data,
        onSuccess: () => qc.invalidateQueries({ queryKey: ['case'] }),
    });
    const on = !!caseData.slack_listen;
    const channel = caseData.slack_channel_name ? `#${caseData.slack_channel_name}` : 'Slack war room';
    // Compact on purpose: the full channel name (e.g. #inc-14156-unauthorized-mfa-push-activity) squeezed the case title.
    const chip = 'inline-flex items-center gap-1.5 h-8 rounded-sm border border-line-strong px-2.5 text-xs whitespace-nowrap';
    return (
        <span className="inline-flex items-center gap-1">
            {caseData.war_room_url ? (
                <a href={caseData.war_room_url} target="_blank" rel="noopener noreferrer" title={`Open ${channel} in Slack`}
                    className={cn(chip, 'text-fg-2 hover:bg-raised hover:text-fg')}>
                    <span className="h-2 w-2 rounded-full bg-emerald-400" aria-hidden />
                    War room <ExternalLink size={12} aria-hidden />
                    <span className="sr-only">{channel} (opens Slack)</span>
                </a>
            ) : (
                <span title={channel} className={cn(chip, 'text-fg-2')}>
                    <span className="h-2 w-2 rounded-full bg-emerald-400" aria-hidden /> War room
                </span>
            )}
            <button type="button" disabled={!canEdit || toggle.isPending} onClick={() => toggle.mutate()} aria-pressed={on}
                aria-label={`SOC-bot ${on ? 'on' : 'off'}`}
                title={`SOC-bot is ${on ? 'listening to' : 'not listening to'} ${channel}${canEdit ? ', click to toggle' : ''}`}
                className={cn('inline-flex items-center justify-center h-8 w-8 rounded-sm border border-line-strong disabled:cursor-default',
                    on ? 'text-accent hover:bg-raised' : 'text-fg-3 hover:bg-raised hover:text-fg-2')}>
                <Bot size={14} />
            </button>
            {on && caseData.war_room_silent && (
                <span title="SOC-bot hasn't received messages. Check the Slack app's message events (see Slack setup)"
                    className="text-amber-400" role="img" aria-label="SOC-bot has not received messages">
                    <AlertTriangle size={14} />
                </span>
            )}
        </span>
    );
}
