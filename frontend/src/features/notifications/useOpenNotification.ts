import { useNavigate } from 'react-router-dom';
import { useQueryClient } from '@tanstack/react-query';
import type { AppNotification } from '../../types';
import { markRead, notificationHref } from './api';

/** Opens a notification: mark read, refresh counts, go to the case (and timeline event). */
export function useOpenNotification(after?: () => void) {
    const qc = useQueryClient();
    const navigate = useNavigate();
    return async (n: AppNotification) => {
        try {
            if (!n.read_at) await markRead(n.id);
        } catch { /* navigation still proceeds */ }
        qc.invalidateQueries({ queryKey: ['notifications'] });
        after?.();
        navigate(notificationHref(n));
    };
}
