import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Check, UserCheck } from 'lucide-react';
import { api } from '../../api/client';
import { Button, Skeleton, fieldClass } from '../../components/ui';
import { cn } from '../../lib/utils';
import type { GuestSettings } from '../../types';

export default function GuestSettingsSection() {
    const qc = useQueryClient();
    const [domainsDraft, setDomains] = useState<string | null>(null);
    const [methodDraft, setMethod] = useState<GuestSettings['login_method'] | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [saved, setSaved] = useState(false);
    const { data, isLoading } = useQuery({
        queryKey: ['tenant-guest-settings'],
        queryFn: async () => (await api.get('/tenants/current/guests')).data as GuestSettings,
    });
    const domains = domainsDraft ?? (data ? data.domain_allowlist.join('\n') : '');
    const loginMethod = methodDraft ?? data?.login_method ?? 'password';
    const save = useMutation({
        mutationFn: async () => (await api.put('/tenants/current/guests', {
            domain_allowlist: domains.split(/[\s,]+/).filter(Boolean), login_method: loginMethod,
        })).data as GuestSettings,
        onSuccess: (d) => {
            setError(null); setDomains(null); setMethod(null); qc.setQueryData(['tenant-guest-settings'], d);
            qc.invalidateQueries({ queryKey: ['sso-config'] }); // shows the guest method too
            setSaved(true); setTimeout(() => setSaved(false), 2500);
        },
        onError: (err) => {
            const d = (err as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
            setError(typeof d === 'string' ? d : 'Could not save guest settings.');
        },
    });
    return (
        <section className="rounded-md bg-panel border border-line">
            <div className="flex items-center px-4 h-11 border-b border-line">
                <h3 className="flex items-center gap-2 text-sm font-semibold text-fg">
                    <UserCheck size={15} className="text-accent" /> Case guests
                </h3>
            </div>
            {isLoading || !data ? <Skeleton rows={3} className="p-4" /> : (
                <div className="p-4 space-y-3">
                    <label htmlFor="guest-domains" className="block text-xs font-medium text-fg-2 mb-1.5">Allowed email domains (one per line)</label>
                    <textarea id="guest-domains" rows={4} value={domains} onChange={(e) => { setDomains(e.target.value); setSaved(false); }}
                        placeholder="company.com" className={cn(fieldClass(), 'h-auto py-2 font-mono')} />
                    <p className="text-xs text-fg-3">Leave empty to turn guest access off for this organization.</p>
                    <label htmlFor="guest-method" className="block text-xs font-medium text-fg-2 mb-1.5">Guest sign-in</label>
                    <select id="guest-method" value={loginMethod}
                        onChange={(e) => { setMethod(e.target.value as GuestSettings['login_method']); setSaved(false); }}
                        className={cn(fieldClass(), 'w-auto')}>
                        <option value="password">Password / email link</option>
                        <option value="saml" disabled={!data.saml_ready && data.login_method !== 'saml'}>SAML SSO</option>
                        <option value="oidc" disabled={!data.oidc_ready && data.login_method !== 'oidc'}>OIDC SSO</option>
                    </select>
                    {!data.sso_ready && <p className="text-xs text-fg-3">Configure and enable SSO first to require it for guests.</p>}
                    {error && <p role="alert" className="text-sm text-severity-critical">{error}</p>}
                    <div className="flex items-center gap-3">
                        <Button size="sm" onClick={() => save.mutate()} disabled={save.isPending}>Save</Button>
                        {saved && <span role="status" className="flex items-center gap-1 text-xs text-lime"><Check size={13} /> Saved</span>}
                    </div>
                </div>
            )}
        </section>
    );
}
