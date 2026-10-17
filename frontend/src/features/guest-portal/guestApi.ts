import axios from 'axios';
import { queryClient } from '../../api/queryClient';

// Never shares storage or interceptors with the analyst client (api/client.ts uses 'token').
const KEY = 'guest_token';

// Guest query keys all start with 'guest'; drop them whenever the guest identity changes.
const dropGuestData = () => queryClient.removeQueries({ predicate: (q) => String(q.queryKey[0]).startsWith('guest') });

export const guestToken = {
    get: () => { try { return localStorage.getItem(KEY); } catch { return null; } },
    set: (t: string) => { try { localStorage.setItem(KEY, t); } catch { /* private mode */ } dropGuestData(); },
    clear: () => { try { localStorage.removeItem(KEY); } catch { /* private mode */ } dropGuestData(); },
};

export const guestApi = axios.create({ baseURL: '/api/v1/guest' });

guestApi.interceptors.request.use((config) => {
    const t = guestToken.get();
    if (t && !config.headers.Authorization) config.headers.Authorization = `Bearer ${t}`;
    return config;
});

guestApi.interceptors.response.use((r) => r, (error) => {
    if (error.response?.status === 401 && !String(error.config?.url || '').startsWith('/auth/')) {
        guestToken.clear();
        if (!window.location.pathname.startsWith('/guest/login')) window.location.assign('/guest/login');
    }
    return Promise.reject(error);
});

export function guestError(err: unknown, fallback: string): string {
    const e = err as { response?: { status?: number; data?: { detail?: unknown } } };
    if (e?.response?.status === 429) return 'Too many attempts. Try again later.';
    const d = e?.response?.data?.detail;
    if (typeof d === 'string') return d;
    const m = (d as { message?: unknown } | undefined)?.message;
    if (typeof m === 'string') return m;
    if (Array.isArray(d)) {  // FastAPI validation errors
        const msgs = d.map((x) => (x as { msg?: unknown })?.msg).filter((x): x is string => typeof x === 'string');
        if (msgs.length) return msgs.join('; ');
    }
    return fallback;
}
