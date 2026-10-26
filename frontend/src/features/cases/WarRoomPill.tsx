import { useMutation, useQueryClient } from '@tanstack/react-query';
import { AlertTriangle, Bot } from 'lucide-react';
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
    const label = caseData.slack_channel_name ? `#${caseData.slack_channel_name}` : 'Slack war room';
    return (
        <span className="inline-flex items-center gap-1.5 rounded-sm border border-line px-2.5 py-0.5 text-xs">
            <span className="h-2 w-2 rounded-full bg-emerald-400" aria-hidden />
            {caseData.war_room_url ? (
                <a href={caseData.war_room_url} target="_blank" rel="noopener noreferrer"
                    className="font-mono text-fg hover:text-accent">{label}</a>
            ) : <span className="font-mono text-fg">{label}</span>}
            <span className="text-fg-3">·</span>
            <button type="button" disabled={!canEdit || toggle.isPending} onClick={() => toggle.mutate()}
                title={canEdit ? 'Toggle whether SOC-bot listens to this channel' : undefined}
                className={cn('inline-flex items-center gap-1 disabled:cursor-default', on ? 'text-fg-2 hover:text-fg' : 'text-fg-3 hover:text-fg-2')}>
                <Bot size={12} /> SOC-bot {on ? 'on' : 'off'}
            </button>
            {on && caseData.war_room_silent && (
                <span title="SOC-bot hasn't received messages. Check the Slack app's message events (see Slack setup)"
                    className="text-amber-400" role="img" aria-label="SOC-bot has not received messages">
                    <AlertTriangle size={13} />
                </span>
            )}
        </span>
    );
}
