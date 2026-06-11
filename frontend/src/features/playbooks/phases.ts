import { IR_PHASES } from '../../types';

export const PHASE_LABEL: Record<string, string> = {
    identification: 'Identification',
    containment: 'Containment',
    eradication: 'Eradication',
    recovery: 'Recovery',
    lessons_learned: 'Lessons Learned',
};

// Accent hue per phase for the light console look.
export const PHASE_COLOR: Record<string, string> = {
    identification: '#2563eb', // blue
    containment: '#d97706',    // amber
    eradication: '#dc2626',    // red
    recovery: '#059669',       // green
    lessons_learned: '#7c3aed', // violet
};

export const PHASES = [...IR_PHASES];

/** Group a list of items (with a `phase` field) into ordered phase buckets. */
export function groupByPhase<T extends { phase: string }>(items: T[]): [string, T[]][] {
    return PHASES
        .map((p) => [p, items.filter((i) => i.phase === p)] as [string, T[]])
        .filter(([, list]) => list.length > 0);
}
