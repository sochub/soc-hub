// Spreadsheet formula-injection guard, mirroring backend `_csv_safe` (api/v1/audit_logs.py): a cell that starts
// with = + - @ TAB or CR would be evaluated as a formula by Excel/Sheets, so it is prefixed with a single quote.
export function csvSafe(v: unknown): string {
    const s = String(v ?? '');
    return /^[=+\-@\t\r]/.test(s) ? `'${s}` : s;
}

/** One RFC 4180 cell: formula-neutralised, always quoted, embedded quotes doubled. */
export const csvCell = (v: unknown): string => `"${csvSafe(v).replace(/"/g, '""')}"`;
