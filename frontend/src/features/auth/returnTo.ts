// Post-login return-to: only the OAuth consent screen and an invitation link are valid destinations, so a
// stored value can never send the browser anywhere else (no open redirect via sessionStorage).
const KEY = 'return_to';

export function safeReturnTo(v: string | null): string {
    if (typeof v === 'string' && v.startsWith('/oauth/consent?') && !v.includes('//') && !v.includes('\\')) {
        return v;
    }
    if (typeof v === 'string' && /^\/invite\/[A-Za-z0-9_-]{1,128}$/.test(v)) return v;
    return '/';
}

export function saveReturnTo(pathAndSearch: string) {
    try { sessionStorage.setItem(KEY, pathAndSearch); } catch { /* private mode */ }
}

/** Where to go after a successful sign-in; the stored value is consumed. */
export function takeReturnTo(): string {
    let v: string | null = null;
    try {
        v = sessionStorage.getItem(KEY);
        sessionStorage.removeItem(KEY);
    } catch { /* private mode */ }
    return safeReturnTo(v);
}
