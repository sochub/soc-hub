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

/** datetime-local value → ISO UTC, or null when the browser accepted something that isn't a real date (e.g. a 6-digit year). */
export function localToIso(local: string): string | null {
    const d = new Date(local);
    return Number.isNaN(d.getTime()) || d.getFullYear() > 9999 ? null : d.toISOString();
}

// Keeps the browser's date picker to 4-digit years.
export const DATE_INPUT_BOUNDS = { min: '1970-01-01T00:00', max: '9999-12-31T23:59' };

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

