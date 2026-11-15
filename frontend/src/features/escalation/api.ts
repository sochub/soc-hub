import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { isAxiosError } from 'axios';
import { api } from '../../api/client';
import type {
    EscalationGroup, EscalationGroupInput, EscalationIntegrations, EscalationTier,
} from '../../types';

const base = '/escalations';

/** Server message for a failed request: string detail, or the first 422 validation entry. */
export function errMsg(e: unknown, fallback: string): string {
    if (!isAxiosError(e)) return fallback;
    const d = e.response?.data?.detail;
    if (typeof d === 'string') return d;
    if (Array.isArray(d) && d.length) {
        return d.map((x) => (typeof x?.msg === 'string' ? x.msg.replace(/^Value error, /, '') : '')).filter(Boolean).join('; ') || fallback;
    }
    return fallback;
}

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
