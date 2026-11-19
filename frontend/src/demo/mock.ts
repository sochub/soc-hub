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
        const me = (responses['/users/me'] as { id?: number } | undefined)?.id;
        if (path in LISTS && Array.isArray(data)) return listPage(config, data, LISTS[path], me);
        return reply(config, 200, data);
    };
}

// Lists that page and filter on the server: mirror their filters, sort and X-Total-Count so the demo behaves the same.
type Row = Record<string, unknown>;
type P = Record<string, unknown>;
const SEV_RANK: Record<string, number> = { critical: 0, high: 1, medium: 2, low: 3, info: 4 };
const SLA_STATES: Record<string, string[]> = { breached: ['breached'], at_risk: ['at_risk'], pressure: ['at_risk', 'breached'] };
const anyOf = (v: unknown) => (v == null ? null : (Array.isArray(v) ? v : [v]).map(String));
const has = (q: unknown, ...fields: unknown[]) => fields.some((f) => String(f ?? '').toLowerCase().includes(String(q).toLowerCase()));
const t = (v: unknown) => Date.parse(String(v));

const LISTS: Record<string, (r: Row, p: P, me?: number) => boolean> = {
    '/alerts/': (a, p) => ['status', 'severity', 'source'].every((k) => p[k] == null || a[k] === p[k]),
    '/cases/': (c, p, me) => {
        const id = String(p.q ?? '').trim().replace(/^#/, '');
        return (!anyOf(p.status) || anyOf(p.status)!.includes(String(c.status)))
            && (!anyOf(p.severity) || anyOf(p.severity)!.includes(String(c.severity)))
            && (p.owner == null || (p.owner === 'me' ? c.owner_id === me : p.owner === 'unassigned' ? c.owner_id == null : String(c.owner_id) === String(p.owner)))
            && (p.tag == null || (c.tags as string[] | undefined ?? []).includes(String(p.tag)))
            && (!id || has(id, c.title, c.description) || String(c.id) === id)
            && (p.sla == null || (SLA_STATES[String(p.sla)] ?? []).includes(String(c.sla_overall_status)))
            && (p.outcome == null || (p.outcome === 'not_set' ? c.outcome == null : c.outcome === p.outcome))
            && (p.created_from == null || t(c.created_at) >= t(p.created_from))
            && (p.created_before == null || t(c.created_at) < t(p.created_before));
    },
    '/iocs/': (i, p) => (p.q == null || has(p.q, i.value)) && ['ioc_type', 'status', 'case_id'].every((k) => p[k] == null || String(i[k]) === String(p[k])),
    '/artifacts/': (a, p) => (p.q == null || has(p.q, a.value)) && (p.artifact_type == null || a.artifact_type === p.artifact_type),
};

function listPage(config: InternalAxiosRequestConfig, all: Row[], keep: (r: Row, p: P, me?: number) => boolean, me?: number): AxiosResponse {
    const p = (config.params ?? {}) as P;
    const rows = all.filter((r) => keep(r, p, me));
    const sort = String(p.sort ?? '-created_at');
    const field = sort.replace(/^-/, '');
    const key = (r: Row) => field === 'severity' ? SEV_RANK[String(r.severity)] ?? 9 : field === 'id' ? Number(r.id) : t(r[field]) || 0;
    // 'severity' = most severe first (rank ascending), dates and ids ascending; a leading '-' reverses.
    const dir = sort.startsWith('-') ? -1 : 1;
    rows.sort((x, y) => dir * (key(x) - key(y)) || Number(y.id) - Number(x.id));
    const skip = Number(p.skip ?? 0), limit = Number(p.limit ?? 100);
    const res = reply(config, 200, rows.slice(skip, skip + limit));
    res.headers = { 'x-total-count': String(rows.length) };
    return res;
}
