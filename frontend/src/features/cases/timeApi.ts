import { useEffect } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api } from '../../api/client';

export interface TimeEntry {
    id: number;
    user_id: number;
    started_at: string;
    ended_at: string;
    source: 'auto' | 'manual';
    note: string | null;
    seconds: number;
}
export interface CaseTimeSummary {
    entries: TimeEntry[];
    total_seconds: number;
    by_user: { user_id: number; email: string; seconds: number }[];
}

export function formatDuration(seconds: number): string {
    const m = Math.round(seconds / 60);
    if (m < 60) return `${m}m`;
    return `${Math.floor(m / 60)}h ${String(m % 60).padStart(2, '0')}m`;
}

/** Shared by the Time tab and the header pill. */
export function useCaseTime(caseId: number, enabled = true) {
    return useQuery({
        queryKey: ['case-time', caseId],
        queryFn: async () => (await api.get(`/cases/${caseId}/time`)).data as CaseTimeSummary,
        enabled,
    });
}

/** Beats once now and every 60s while the tab is visible; silent while hidden. */
export function useTimeHeartbeat(caseId: number | undefined, enabled: boolean) {
    useEffect(() => {
        if (!enabled || !caseId) return;
        const beat = () => {
            if (document.visibilityState === 'visible') api.post(`/cases/${caseId}/time/heartbeat`).catch(() => { /* best effort */ });
        };
        beat();
        const t = window.setInterval(beat, 60_000);
        document.addEventListener('visibilitychange', beat);
        return () => { window.clearInterval(t); document.removeEventListener('visibilitychange', beat); };
    }, [caseId, enabled]);
}
