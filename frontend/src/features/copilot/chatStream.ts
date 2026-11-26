import { api } from '../../api/client';
import { DEMO } from '../../demo';

export interface ChatBody {
    case_id: number | null;
    session_id?: number;
    message: string;
}

/** Stream `error` codes as the HTTP status POST /copilot/chat answers for the same failure. */
const STATUS: Record<string, number> = { unavailable: 503, timeout: 504, provider: 502, too_long: 422 };

/** Shaped like an axios error, so one error path serves both transports. */
const httpError = (status: number, detail?: unknown) => ({ response: { status, data: { detail } } });

// Set once the server turns out to have no stream endpoint (an older backend): later sends go straight to POST /chat.
let streamMissing = false;

/** True for a request the user stopped (fetch AbortError or axios cancel), which is not a failure. */
export const wasStopped = (e: unknown) =>
    ['AbortError', 'CanceledError'].includes((e as { name?: string } | null)?.name ?? '');

/**
 * Send a chat message and resolve with the stored assistant message. The reply is streamed when possible
 * (`onDelta` gets each piece of text as the model writes it); otherwise this is the plain POST /copilot/chat.
 */
export async function sendChat<T>(body: ChatBody, signal: AbortSignal, onDelta: (text: string) => void): Promise<T> {
    const plain = async () => (await api.post('/copilot/chat', body, { signal })).data as T;
    if (DEMO || streamMissing || typeof ReadableStream === 'undefined' || typeof TextDecoder === 'undefined') return plain();

    // Same origin, cookie session and CSRF header as the axios client; fetch because axios cannot read a body as it arrives.
    const res = await fetch(`${api.defaults.baseURL}/copilot/chat/stream`, {
        method: 'POST',
        credentials: 'same-origin',
        signal,
        headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream', 'X-Requested-With': 'XMLHttpRequest' },
        body: JSON.stringify(body),
    });
    // Expired access cookie: the axios path refreshes the session and retries the request once.
    if (res.status === 401) return plain();
    if (!res.ok || !res.body) {
        const detail = (await res.json().catch(() => null))?.detail;
        // The route itself is missing (as opposed to "Session not found"): fall back, now and from here on.
        if (res.status === 404 && (detail === undefined || detail === 'Not Found')) {
            streamMissing = true;
            return plain();
        }
        throw httpError(res.status, detail);
    }

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        for (let end = buffer.indexOf('\n\n'); end >= 0; end = buffer.indexOf('\n\n')) {
            const raw = buffer.slice(0, end);
            buffer = buffer.slice(end + 2);
            const event = /^event: (.*)$/m.exec(raw)?.[1];
            const data = JSON.parse(/^data: (.*)$/m.exec(raw)?.[1] ?? 'null');
            if (event === 'delta') onDelta(data.text);
            else if (event === 'message') return data as T;
            else if (event === 'error') throw httpError(STATUS[data.code] ?? 500, data.detail);
        }
    }
    throw httpError(504);  // the stream ended without its closing event (proxy timeout, server restart)
}
