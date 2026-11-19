import { api } from './client';

/** One page of a list endpoint: the bare-list body plus the server's X-Total-Count. */
export interface Page<T> { items: T[]; total: number }

export async function getPage<T>(url: string, params: Record<string, unknown>): Promise<Page<T>> {
    // indexes: null sends arrays as repeated keys (status=new&status=open), which FastAPI reads as a list.
    const r = await api.get<T[]>(url, { params, paramsSerializer: { indexes: null } });
    return { items: r.data, total: Number(r.headers['x-total-count'] ?? r.data.length) };
}

/** Case statuses that count as open work (mirrors the backend CaseStatus enum). */
export const OPEN_CASE_STATUSES = ['new', 'open', 'in_progress', 'pending'];

/** "Showing X–Y of N" text for a zero-based page. */
export function showingText(page: number, pageSize: number, shown: number, total: number, noun: string) {
    if (total === 0) return `0 ${noun}`;
    const first = page * pageSize + 1;
    return `Showing ${first.toLocaleString()}–${(first + shown - 1).toLocaleString()} of ${total.toLocaleString()} ${noun}`;
}
