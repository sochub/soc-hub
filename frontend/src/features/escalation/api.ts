import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../../api/client';
import { apiErrorMessage } from '../../api/errors';
import type {
    EscalationGroup, EscalationGroupInput, EscalationHistoryRow, EscalationIntegrations, EscalationQueueRow, EscalationTier,
} from '../../types';

const base = '/escalations';

/** Server message for a failed request (shared helper; kept under this name for existing callers). */
export const errMsg = apiErrorMessage;

export const useGroups = () => useQuery({
    queryKey: ['escalation-groups'],
    queryFn: async () => (await api.get(`${base}/groups`)).data as EscalationGroup[],
});

export function useSaveGroup() {
    const qc = useQueryClient();
    return useMutation({
        mutationFn: async ({ id, body }: { id?: number; body: EscalationGroupInput }) =>
            (id ? await api.put(`${base}/groups/${id}`, body) : await api.post(`${base}/groups`, body)).data as EscalationGroup,
        onSuccess: () => qc.invalidateQueries({ queryKey: ['escalation-groups'] }),
    });
}

export function useDeleteGroup() {
    const qc = useQueryClient();
    return useMutation({
        mutationFn: async (id: number) => { await api.delete(`${base}/groups/${id}`); },
        onSuccess: () => qc.invalidateQueries({ queryKey: ['escalation-groups'] }),
    });
}

export const usePolicy = () => useQuery({
    queryKey: ['escalation-policy'],
    queryFn: async () => (await api.get(`${base}/policy`)).data as { tiers: EscalationTier[] },
});

export function useSavePolicy() {
    const qc = useQueryClient();
    return useMutation({
        mutationFn: async (tiers: EscalationTier[]) =>
            (await api.put(`${base}/policy`, { tiers })).data as { tiers: EscalationTier[] },
        onSuccess: (d) => {
            qc.setQueryData(['escalation-policy'], d);
            qc.invalidateQueries({ queryKey: ['escalation-groups'] });
        },
    });
}

export const useEscalationIntegrations = () => useQuery({
    queryKey: ['escalation-integrations'],
    queryFn: async () => (await api.get(`${base}/integrations`)).data as EscalationIntegrations,
});

export function useSaveEscalationIntegrations() {
    const qc = useQueryClient();
    return useMutation({
        mutationFn: async (body: { pagerduty_webhook_secret?: string; pagerduty_api_key?: string }) =>
            (await api.put(`${base}/integrations`, body)).data as EscalationIntegrations,
        onSuccess: (d) => qc.setQueryData(['escalation-integrations'], d),
    });
}

export function useOpsgenieToken() {
    const qc = useQueryClient();
    return useMutation({
        mutationFn: async () => (await api.post(`${base}/integrations/opsgenie-token`)).data as { token: string },
        onSuccess: () => qc.invalidateQueries({ queryKey: ['escalation-integrations'] }),
    });
}

export const queueKey = ['escalation-queue'];

export const useEscalationQueue = () => useQuery({
    queryKey: queueKey,
    queryFn: async () => (await api.get(`${base}/queue`)).data as EscalationQueueRow[],
});

export const useEscalationHistory = (caseId: number) => useQuery({
    queryKey: ['escalation-history', caseId],
    queryFn: async () => (await api.get(`${base}/cases/${caseId}/history`)).data as EscalationHistoryRow[],
});

/** Mutation for one case escalation action; refreshes the case, case lists, history and queue. */
export function useEscalationAction(caseId: number, action: 'escalate' | 'accept' | 'redirect' | 'return' | 'withdraw') {
    const qc = useQueryClient();
    return useMutation({
        mutationFn: async (body: Record<string, unknown> = {}) =>
            (await api.post(`${base}/cases/${caseId}/${action}`, body)).data as EscalationHistoryRow,
        onSettled: () => {
            qc.invalidateQueries({ queryKey: ['case', String(caseId)] });
            qc.invalidateQueries({ queryKey: ['cases'] });
            qc.invalidateQueries({ queryKey: ['escalation-history', caseId] });
            qc.invalidateQueries({ queryKey: queueKey });
            qc.invalidateQueries({ queryKey: ['audit-logs', 'case', String(caseId)] });
        },
    });
}

/** "12m" / "3h" / "2d" since an ISO timestamp (naive timestamps are UTC). */
export function sinceShort(iso: string): string {
    const v = /^\d{4}-\d{2}-\d{2}T[\d:.]+$/.test(iso) ? `${iso}Z` : iso;
    const m = Math.max(0, Math.floor((Date.now() - new Date(v).getTime()) / 60000));
    if (m < 60) return `${m}m`;
    if (m < 1440) return `${Math.floor(m / 60)}h`;
    return `${Math.floor(m / 1440)}d`;
}
