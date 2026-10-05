/** Format an ISO timestamp in the given IANA timezone (falls back to the browser default). */
export function formatDateTime(value: string | number | Date | null | undefined, tz?: string | null): string {
    if (value === null || value === undefined || value === '') return '';
    // Backend timestamps without an offset are UTC.
    const v = typeof value === 'string' && /^\d{4}-\d{2}-\d{2}T[\d:.]+$/.test(value) ? `${value}Z` : value;
    const d = new Date(v);
    if (Number.isNaN(d.getTime())) return '';
    const opts: Intl.DateTimeFormatOptions = { dateStyle: 'medium', timeStyle: 'short' };
    try {
        return new Intl.DateTimeFormat(undefined, tz ? { ...opts, timeZone: tz } : opts).format(d);
    } catch {
        return new Intl.DateTimeFormat(undefined, opts).format(d);
    }
}
