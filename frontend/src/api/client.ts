import axios from 'axios';
import { DEMO } from '../demo';

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
    active: () => { if (DEMO) return true; try { return localStorage.getItem(FLAG) === '1'; } catch { return false; } },
    mark: () => { try { localStorage.setItem(FLAG, '1'); } catch { /* private mode */ } },
    clear: () => { try { localStorage.removeItem(FLAG); } catch { /* private mode */ } },
};

// Event-based auth expiry so React Router can handle navigation
const AUTH_EXPIRED_EVENT = 'auth:expired';

export function onAuthExpired(callback: () => void) {
    window.addEventListener(AUTH_EXPIRED_EVENT, callback);
    return () => window.removeEventListener(AUTH_EXPIRED_EVENT, callback);
}

// One-shot message for the login page (e.g. why the session ended), survives the redirect only.
const LOGIN_NOTICE = 'login_notice';
export const loginNotice = {
    peek: (): string | null => { try { return sessionStorage.getItem(LOGIN_NOTICE); } catch { return null; } },
    clear: () => { try { sessionStorage.removeItem(LOGIN_NOTICE); } catch { /* private mode */ } },
};

export function ssoLoginUrl(slug: string) {
    return `/api/v1/auth/sso/${encodeURIComponent(slug)}/login`;
}

// Session-wide auth outcomes. A request carrying an explicit token (e.g. the MFA setup challenge at login)
// is not the browser session, so it never ends or redirects it.
api.interceptors.response.use(
    (response) => response,
    (error) => {
        const status = error.response?.status;
        if (!error.config?.headers?.Authorization) {
            const d = error.response?.data?.detail;
            if (status === 401) {
                session.clear();
                window.dispatchEvent(new Event(AUTH_EXPIRED_EVENT));
            } else if (status === 403 && d?.code === 'sso_required' && d.slug) {
                // The active organization signs in with SSO: go through its IdP.
                window.location.assign(ssoLoginUrl(d.slug));
            } else if (status === 403 && d?.code === 'password_required') {
                // An SSO session may only open its own organization; this one needs a password sign-in.
                try { sessionStorage.setItem(LOGIN_NOTICE, 'Sign in with your password to open this organization.'); } catch { /* private mode */ }
                session.clear();
                window.dispatchEvent(new Event(AUTH_EXPIRED_EVENT));
            }
        }
        return Promise.reject(error);
    }
);
