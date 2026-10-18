import axios from 'axios';

// The session token lives in an HttpOnly cookie the browser sends on its own; page scripts never see it.
// X-Requested-With is the CSRF header the API requires on cookie-authenticated writes.
export const api = axios.create({
    baseURL: '/api/v1',
    headers: {
        'Content-Type': 'application/json',
        'X-Requested-With': 'XMLHttpRequest',
    },
});

// "Signed in" hint for routing only; it holds no credential. The real check is the cookie, server side.
const FLAG = 'signed_in';
try { localStorage.removeItem('token'); } catch { /* sessions from before the cookie switch: sign in again */ }

export const session = {
    active: () => { try { return localStorage.getItem(FLAG) === '1'; } catch { return false; } },
    mark: () => { try { localStorage.setItem(FLAG, '1'); } catch { /* private mode */ } },
    clear: () => { try { localStorage.removeItem(FLAG); } catch { /* private mode */ } },
};

// Event-based auth expiry so React Router can handle navigation
const AUTH_EXPIRED_EVENT = 'auth:expired';

export function onAuthExpired(callback: () => void) {
    window.addEventListener(AUTH_EXPIRED_EVENT, callback);
    return () => window.removeEventListener(AUTH_EXPIRED_EVENT, callback);
}

// Add response interceptor to handle 401 errors
api.interceptors.response.use(
    (response) => response,
    (error) => {
        // A request carrying an explicit token (e.g. the MFA setup challenge at login) must not end the session.
        if (error.response?.status === 401 && !error.config?.headers?.Authorization) {
            session.clear();
            window.dispatchEvent(new Event(AUTH_EXPIRED_EVENT));
        }
        return Promise.reject(error);
    }
);
