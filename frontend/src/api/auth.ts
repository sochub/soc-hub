import { api } from './client';

/** Switch the active tenant: the API re-issues the session cookie. Caller should
 *  invalidate React Query caches afterwards so all data refetches. */
export async function switchTenant(tenantId: number): Promise<void> {
    await api.post('/auth/switch-tenant', { tenant_id: tenantId });
}
