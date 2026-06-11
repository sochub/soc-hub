import axios from 'axios';

export const api = axios.create({
    baseURL: '/api/v1',
    headers: {
        'Content-Type': 'application/json',
    },
});

// Add interceptor to add token if available
api.interceptors.request.use((config) => {
    const token = localStorage.getItem('token');
    if (token) {
        config.headers.Authorization = `Bearer ${token}`;
    }
    return config;
});

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
        if (error.response?.status === 401) {
            localStorage.removeItem('token');
            window.dispatchEvent(new Event(AUTH_EXPIRED_EVENT));
        }
        return Promise.reject(error);
    }
);
