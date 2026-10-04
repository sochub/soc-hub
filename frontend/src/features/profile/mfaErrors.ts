/** Human-readable message for an MFA/password API error (never includes codes). */
export function mfaErrorMessage(err: unknown, fallback = 'Something went wrong. Try again.'): string {
    const e = err as { response?: { status?: number; data?: { detail?: unknown }; headers?: Record<string, string> } };
    const detail = e?.response?.data?.detail;
    if (e?.response?.status === 429) {
        const wait = e.response.headers?.['retry-after'];
        return `Too many attempts. Try again${wait ? ` in ${wait}s` : ' later'}.`;
    }
    return typeof detail === 'string' ? detail : fallback;
}
