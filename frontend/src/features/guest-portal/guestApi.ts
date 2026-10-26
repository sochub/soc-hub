import axios from 'axios';
import { queryClient } from '../../api/queryClient';

// The guest session is an HttpOnly cookie (sochub_guest, sent only to /api/v1/guest) that page scripts never see.
// X-Requested-With is the CSRF header the API requires on cookie-authenticated writes.
// "Signed in" hint for routing only; it holds no credential. Separate from the analyst client's 'signed_in'.
const FLAG = 'guest_signed_in';
try { localStorage.removeItem('guest_token'); } catch { /* bearer token from before the cookie switch: sign in again */ }

// Guest query keys all start with 'guest'; drop them whenever the guest identity changes.
const dropGuestData = () => queryClient.removeQueries({ predicate: (q) => String(q.queryKey[0]).startsWith('guest') });

export const guestSession = {
    active: () => { try { return localStorage.getItem(FLAG) === '1'; } catch { return false; } },
    mark: () => { try { localStorage.setItem(FLAG, '1'); } catch { /* private mode */ } dropGuestData(); },
    clear: () => { try { localStorage.removeItem(FLAG); } catch { /* private mode */ } dropGuestData(); },
};

export const guestApi = axios.create({ baseURL: '/api/v1/guest', headers: { 'X-Requested-With': 'XMLHttpRequest' } });

/** Ends the browser guest session: the server drops the HttpOnly cookie. */
export async function guestSignOut() {
    try { await guestApi.post('/auth/logout'); } catch { /* the local hint is cleared regardless */ }
    guestSession.clear();
}

guestApi.interceptors.response.use((r) => r, (error) => {
    if (error.response?.status === 401 && !String(error.config?.url || '').startsWith('/auth/')) {
        guestSession.clear();
        if (!window.location.pathname.startsWith('/guest/login')) window.location.assign('/guest/login');
    }
    return Promise.reject(error);
});

export function guestError(err: unknown, fallback: string): string {
    const e = err as { response?: { status?: number; data?: { detail?: unknown } } };
    const d = e?.response?.data?.detail;
    if (e?.response?.status === 429) return typeof d === 'string' ? d : 'Too many attempts. Try again later.';
    if (typeof d === 'string') return d;
    const m = (d as { message?: unknown } | undefined)?.message;
    if (typeof m === 'string') return m;
    if (Array.isArray(d)) {  // FastAPI validation errors
        const msgs = d.map((x) => (x as { msg?: unknown })?.msg).filter((x): x is string => typeof x === 'string');
        if (msgs.length) return msgs.join('; ');
    }
    return fallback;
}
