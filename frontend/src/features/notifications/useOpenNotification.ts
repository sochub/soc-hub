import { useNavigate } from 'react-router-dom';
import { useQueryClient } from '@tanstack/react-query';
import type { AppNotification } from '../../types';
import { markRead, notificationHref } from './api';

/** Opens a notification: navigate immediately, mark read in the background, then refresh counts. */
export function useOpenNotification(after?: () => void) {
    const qc = useQueryClient();
    const navigate = useNavigate();
    return (n: AppNotification) => {
        after?.();
        navigate(notificationHref(n));
        if (!n.read_at) {
            markRead(n.id).catch(() => undefined)
                .finally(() => qc.invalidateQueries({ queryKey: ['notifications'] }));
        }
    };
}
