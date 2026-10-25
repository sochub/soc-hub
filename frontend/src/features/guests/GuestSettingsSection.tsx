import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Check, UserCheck } from 'lucide-react';
import { api } from '../../api/client';
import { Button } from '../../components/ui';
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
            setSaved(true); setTimeout(() => setSaved(false), 2500);
        },
        onError: (err) => {
            const d = (err as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
            setError(typeof d === 'string' ? d : 'Could not save guest settings.');
        },
    });
    return (
        <section className="bg-panel border border-line">
            <div className="flex items-center px-4 h-11 border-b border-line">
                <h2 className="flex items-center gap-2 text-sm font-semibold text-fg">
                    <UserCheck size={15} className="text-accent" /> Case guests
                </h2>
            </div>
            {isLoading || !data ? <p className="p-4 font-mono text-xs text-muted">$ loading…</p> : (
                <div className="p-4 space-y-3">
                    <label htmlFor="guest-domains" className="block text-sm text-fg">Allowed email domains (one per line)</label>
                    <textarea id="guest-domains" rows={4} value={domains} onChange={(e) => { setDomains(e.target.value); setSaved(false); }}
                        placeholder="company.com" className="w-full bg-raised border border-line rounded-md p-2 text-sm text-fg font-mono" />
                    <p className="text-xs text-fg-3">Leave empty to turn guest access off for this organization.</p>
                    <label htmlFor="guest-method" className="block text-sm text-fg">Guest login method</label>
                    <select id="guest-method" value={loginMethod}
                        onChange={(e) => { setMethod(e.target.value as GuestSettings['login_method']); setSaved(false); }}
                        className="bg-raised border border-line rounded-md p-2 text-sm text-fg">
                        <option value="password">Password / email link</option>
                        <option value="saml" disabled={!data.saml_ready && data.login_method !== 'saml'}>SAML SSO</option>
                        <option value="oidc" disabled={!data.oidc_ready && data.login_method !== 'oidc'}>OIDC SSO</option>
                    </select>
                    {!data.sso_ready && <p className="text-xs text-fg-3">Configure and enable SSO first to require it for guests.</p>}
                    {error && <p role="alert" className="text-sm text-severity-critical">{error}</p>}
                    <div className="flex items-center gap-3">
                        <Button size="sm" onClick={() => save.mutate()} disabled={save.isPending}>Save</Button>
                        {saved && <span role="status" className="flex items-center gap-1 text-xs text-emerald-400"><Check size={13} /> Saved</span>}
                    </div>
                </div>
            )}
        </section>
    );
}
