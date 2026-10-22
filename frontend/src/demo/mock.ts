import { AxiosError, type AxiosInstance, type AxiosResponse, type InternalAxiosRequestConfig } from 'axios';
import { DEMO_READ_ONLY } from '.';

type Fixtures = { captured_at: string; responses: Record<string, unknown> };
const ISO = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})?$/;

// Fixtures are recorded once; shift every timestamp so "3 days ago" stays 3 days ago whenever the demo is opened.
function shift(value: unknown, ms: number): unknown {
    if (typeof value === 'string' && ISO.test(value)) return new Date(Date.parse(value) + ms).toISOString();
    if (Array.isArray(value)) return value.map((v) => shift(v, ms));
    if (value && typeof value === 'object') {
        return Object.fromEntries(Object.entries(value).map(([k, v]) => [k, shift(v, ms)]));
    }
    return value;
}

function reply(config: InternalAxiosRequestConfig, status: number, data: unknown): AxiosResponse {
    return { data, status, statusText: String(status), headers: {}, config, request: {} };
}

function fail(config: InternalAxiosRequestConfig, status: number, detail: string): Promise<never> {
    const response = reply(config, status, { detail });
    return Promise.reject(new AxiosError(detail, 'ERR_BAD_REQUEST', config, null, response));
}

// Many forms ignore failed saves, so the demo says it itself, once per attempt, whatever the form does.
function notice(): void {
    const id = 'demo-notice';
    document.getElementById(id)?.remove();
    const el = Object.assign(document.createElement('div'), { id, role: 'status', textContent: DEMO_READ_ONLY });
    el.className = 'fixed bottom-24 left-1/2 -translate-x-1/2 z-[100] max-w-md px-4 py-3 rounded-lg text-sm '
        + 'bg-panel border border-accent/40 text-fg shadow-2xl';
    document.body.append(el);
    setTimeout(() => el.remove(), 4000);
}

/** Answer every API call from the recorded fixtures; refuse writes with a friendly read-only message. */
export async function installDemo(api: AxiosInstance): Promise<void> {
    const raw: Fixtures = await (await fetch(`${import.meta.env.BASE_URL}demo-fixtures.json`)).json();
    const responses = shift(raw.responses, Date.now() - Date.parse(raw.captured_at)) as Record<string, unknown>;

    api.defaults.adapter = async (config) => {
        const path = (config.url ?? '').split('?')[0];
        const method = (config.method ?? 'get').toLowerCase();
        if (method !== 'get') {
            if (path === '/auth/logout') return reply(config, 200, {});
            notice();
            return fail(config, 403, DEMO_READ_ONLY);
        }
        const key = [path, path.endsWith('/') ? path.slice(0, -1) : `${path}/`].find((k) => k in responses);
        if (key === undefined) return fail(config, 404, 'Not available in the demo.');
        const data = responses[key];
        if (config.responseType === 'blob') {
            const svg = path.endsWith('.svg');
            return reply(config, 200, new Blob([typeof data === 'string' ? data : JSON.stringify(data)],
                { type: svg ? 'image/svg+xml' : 'application/json' }));
        }
        return reply(config, 200, data);
    };
}
