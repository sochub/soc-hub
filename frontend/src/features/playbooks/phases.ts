import { IR_PHASES } from '../../types';

export const PHASE_LABEL: Record<string, string> = {
    identification: 'Identification',
    containment: 'Containment',
    eradication: 'Eradication',
    recovery: 'Recovery',
    lessons_learned: 'Lessons Learned',
};

// Marker colour per phase (a small square next to the phase heading). Theme tokens, so both themes read.
export const PHASE_COLOR: Record<string, string> = {
    identification: 'var(--color-severity-low)',     // blue
    containment: 'var(--color-severity-medium)',     // amber
    eradication: 'var(--color-severity-critical)',   // red
    recovery: 'var(--color-lime)',                   // green
    lessons_learned: 'var(--color-magenta)',         // violet
};

export const PHASES = [...IR_PHASES];

/** Group a list of items (with a `phase` field) into ordered phase buckets. */
export function groupByPhase<T extends { phase: string }>(items: T[]): [string, T[]][] {
    return PHASES
        .map((p) => [p, items.filter((i) => i.phase === p)] as [string, T[]])
        .filter(([, list]) => list.length > 0);
}
