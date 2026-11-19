// The one map for semantic colours: severity, case status, verdicts and TLP. Badges, charts and graphs read from here.
export type Tone = 'critical' | 'high' | 'medium' | 'low' | 'info' | 'ok' | 'accent' | 'ink' | 'neutral';

/** Text colour (stamps ink in this colour). */
export const TONE_TEXT: Record<Tone, string> = {
    critical: 'text-severity-critical',
    high: 'text-severity-high',
    medium: 'text-severity-medium',
    low: 'text-severity-low',
    info: 'text-severity-info',
    ok: 'text-lime', // lime is the brand's signal: good outcomes own it
    accent: 'text-accent',
    ink: 'text-fg',
    neutral: 'text-fg-3',
};

/** CSS variable per tone, for charts, React Flow and SVG that can't take a class. */
export const TONE_VAR: Record<Tone, string> = {
    critical: 'var(--color-severity-critical)',
    high: 'var(--color-severity-high)',
    medium: 'var(--color-severity-medium)',
    low: 'var(--color-severity-low)',
    info: 'var(--color-severity-info)',
    ok: 'var(--color-lime)',
    accent: 'var(--color-accent)',
    ink: 'var(--color-fg-2)',
    neutral: 'var(--color-fg-3)',
};

export const SEVERITY_ORDER = ['critical', 'high', 'medium', 'low', 'info'] as const;

export function severityTone(s?: string | null): Tone {
    const v = (s ?? '').toLowerCase();
    return (SEVERITY_ORDER as readonly string[]).includes(v) ? (v as Tone) : 'info';
}

// Case status is a state, not a rank: it never borrows the severity ramp (which owns red/orange/amber/blue).
export function statusTone(s?: string | null): Tone {
    switch (s) {
        case 'new': return 'accent';
        case 'open': return 'ink';
        case 'in_progress': return 'ink';
        case 'pending': return 'neutral';
        case 'resolved': return 'ok';
        case 'closed': return 'neutral';
        default: return 'neutral';
    }
}

export function verdictTone(v?: string | null): Tone {
    switch ((v ?? '').toLowerCase()) {
        case 'malicious': return 'critical';
        case 'suspicious': return 'medium';
        case 'clean': case 'harmless': case 'benign': return 'ok';
        default: return 'neutral';
    }
}

export function tlpTone(t?: string | null): Tone {
    switch ((t ?? '').toLowerCase()) {
        case 'red': return 'critical';
        case 'amber': case 'amber+strict': return 'medium';
        case 'green': return 'ok';
        default: return 'neutral';
    }
}
