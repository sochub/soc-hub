/** Server message for a failed request, always a string (safe to render as a React child):
 *  a string `detail`, a `{message}` detail, or the 422 validation entries joined. */
export function apiErrorMessage(e: unknown, fallback: string): string {
    const d = (e as { response?: { data?: { detail?: unknown } } } | null)?.response?.data?.detail;
    if (typeof d === 'string') return d;
    const m = (d as { message?: unknown } | null | undefined)?.message;
    if (typeof m === 'string') return m;
    if (Array.isArray(d)) {  // FastAPI validation errors
        const msgs = d
            .map((x) => (x as { msg?: unknown })?.msg)
            .filter((x): x is string => typeof x === 'string')
            .map((x) => x.replace(/^Value error, /, ''));
        if (msgs.length) return msgs.join('; ');
    }
    return fallback;
}

/** Client-side mirror of backend/app/core/passwords.py, for an early hint. The server stays authoritative. */
export const PASSWORD_HINT = 'At least 12 characters, with an uppercase letter, a lowercase letter and a digit.';

export function passwordProblem(pw: string): string | null {
    if (pw.length < 12) return 'Password must be at least 12 characters long.';
    if (pw.length > 128) return 'Password must be at most 128 characters long.';
    if (!/[A-Z]/.test(pw)) return 'Password must contain at least one uppercase letter.';
    if (!/[a-z]/.test(pw)) return 'Password must contain at least one lowercase letter.';
    if (!/[0-9]/.test(pw)) return 'Password must contain at least one digit.';
    return null;
}
