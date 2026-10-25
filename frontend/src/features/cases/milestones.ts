import { useMutation, useQueryClient } from '@tanstack/react-query';

import { api } from '../../api/client';
import type { MilestoneKey, ReportMilestones } from '../../types';

export const MILESTONES: { key: MilestoneKey; label: string }[] = [
    { key: 'first_seen', label: 'First seen' },
    { key: 'detected', label: 'Detected' },
    { key: 'contained', label: 'Contained' },
    { key: 'recovered', label: 'Recovered' },
];

/** UTC ISO → datetime-local value (browser local wall time, minute precision). */
export function isoToLocal(iso: string | null): string {
    if (!iso) return '';
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return '';
    return new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
}

export const fmtLocal = (iso: string | null) => (iso ? new Date(iso).toLocaleString() : 'unknown');

export function apiDetail(e: unknown): string {
    const d = (e as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
    if (typeof d === 'string') return d;
    if (Array.isArray(d)) return d.map((x) => (x as { msg?: string })?.msg).filter(Boolean).join('; ') || 'Save failed';
    return 'Save failed';
}

/** Saves a partial milestone patch (`{first_seen_at: iso|null, ...}`) and refreshes the case and report views. */
export function useSaveMilestones(caseId: number) {
    const qc = useQueryClient();
    return useMutation({
        mutationFn: async (patch: Partial<Record<`${MilestoneKey}_at`, string | null>>) =>
            (await api.put(`/cases/${caseId}/milestones`, patch)).data as ReportMilestones,
        onSuccess: () => {
            qc.invalidateQueries({ queryKey: ['case'] });
            qc.invalidateQueries({ queryKey: ['case-report'] });
        },
    });
}

