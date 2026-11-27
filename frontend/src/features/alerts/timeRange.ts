/** Received-time ranges for the Alerts list. A relative range ("24h", "today") lives in the URL as `range` and is
 *  turned into an instant each time the list is fetched, so a refresh moves the window; an absolute one is the
 *  `from` / `to` pair (datetime-local values, the browser's timezone). */
export const PRESETS: [string, string][] = [
    ['15m', 'Last 15 minutes'], ['1h', 'Last hour'], ['6h', 'Last 6 hours'], ['24h', 'Last 24 hours'],
    ['7d', 'Last 7 days'], ['today', 'Today'],
];
export const RANGE_RE = /^(today|[1-9]\d{0,3}[mhd])$/;
const UNIT_MS: Record<string, number> = { m: 60_000, h: 3_600_000, d: 86_400_000 };
const UNIT_NAME: Record<string, string> = { m: 'minute', h: 'hour', d: 'day' };

/** Start of a relative range as an ISO instant (its end is "now", so new alerts keep arriving in it). */
export function rangeStart(range: string, now = Date.now()): string | undefined {
    if (!RANGE_RE.test(range)) return undefined;
    if (range === 'today') { const d = new Date(now); d.setHours(0, 0, 0, 0); return d.toISOString(); }
    return new Date(now - Number(range.slice(0, -1)) * UNIT_MS[range.slice(-1)]).toISOString();
}

export function rangeLabel(range: string): string {
    const preset = PRESETS.find(([k]) => k === range);
    if (preset) return preset[1];
    const n = Number(range.slice(0, -1)), unit = UNIT_NAME[range.slice(-1)] ?? '';
    return `Last ${n} ${unit}${n === 1 ? '' : 's'}`;
}

const short = (local: string) => new Date(local).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
export const absoluteLabel = (from: string, to: string) => `${from ? short(from) : 'Any time'} – ${to ? short(to) : 'now'}`;

export const browserZone = () => { try { return Intl.DateTimeFormat().resolvedOptions().timeZone || 'local time'; } catch { return 'local time'; } };

// Recently used ranges, per browser: "24h" or "<from>|<to>". A convenience only; the list works without it.
const KEY = 'sochub.alerts.recentRanges';
export function recentRanges(): string[] {
    try { const v = JSON.parse(localStorage.getItem(KEY) ?? '[]'); return Array.isArray(v) ? v.filter((x) => typeof x === 'string').slice(0, 4) : []; } catch { return []; }
}
export function rememberRange(entry: string): void {
    try { localStorage.setItem(KEY, JSON.stringify([entry, ...recentRanges().filter((x) => x !== entry)].slice(0, 4))); } catch { /* private window */ }
}
