import { useCallback, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api } from '../../api/client';

// Optional columns only: select, expand, title and actions are always shown.
export const ALERT_COLUMNS = [
    { key: 'severity', label: 'Severity' },
    { key: 'source', label: 'Source' },
    { key: 'status', label: 'Status' },
    { key: 'created_at', label: 'Received' },
] as const;
export type AlertColumnKey = typeof ALERT_COLUMNS[number]['key'];
/** Always-visible columns: select, expand, title, actions. */
export const FIXED_COLUMN_COUNT = 4;

const ALL: AlertColumnKey[] = ALERT_COLUMNS.map(c => c.key);

// Per-user, per-browser preference (the app has no server-side prefs store).
export function useAlertColumns() {
    const { data: me } = useQuery({
        queryKey: ['currentUser'],
        queryFn: async () => (await api.get('/users/me')).data as { id: number },
    });
    const storageKey = `alerts:columns:${me?.id ?? 'anon'}`;
    const read = (): AlertColumnKey[] => {
        try {
            const raw = localStorage.getItem(storageKey);
            if (raw) {
                const parsed = JSON.parse(raw);
                if (Array.isArray(parsed)) return ALL.filter(k => parsed.includes(k));
            }
        } catch { /* storage unavailable or corrupt */ }
        return ALL;
    };
    const [state, setState] = useState<{ key: string; cols: AlertColumnKey[] }>(() => ({ key: storageKey, cols: read() }));
    // Until the user id is known the key is 'anon'; re-read when it changes.
    const visible = state.key === storageKey ? state.cols : read();

    const save = useCallback((cols: AlertColumnKey[] | null) => {
        try {
            if (cols) localStorage.setItem(storageKey, JSON.stringify(cols));
            else localStorage.removeItem(storageKey);
        } catch { /* storage unavailable */ }
        setState({ key: storageKey, cols: cols ?? ALL });
    }, [storageKey]);

    return {
        isVisible: (k: AlertColumnKey) => visible.includes(k),
        visibleCount: visible.length,
        toggle: (k: AlertColumnKey) => save(ALL.filter(c => (c === k ? !visible.includes(c) : visible.includes(c)))),
        reset: () => save(null),
        isDefault: visible.length === ALL.length,
    };
}
