import { api } from './client';

/** Switch the active tenant: re-issues the JWT and stores it. Caller should
 *  invalidate React Query caches afterwards so all data refetches. */
export async function switchTenant(tenantId: number): Promise<void> {
    const res = await api.post('/auth/switch-tenant', { tenant_id: tenantId });
    localStorage.setItem('token', res.data.access_token);
}
