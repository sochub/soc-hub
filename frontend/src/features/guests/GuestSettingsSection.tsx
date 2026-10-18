import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { UserCheck } from 'lucide-react';
import { api } from '../../api/client';
import { Button } from '../../components/ui';
import type { GuestSettings } from '../../types';

export default function GuestSettingsSection() {
    const qc = useQueryClient();
    const [domainsDraft, setDomains] = useState<string | null>(null);
    const [ssoDraft, setRequireSso] = useState<boolean | null>(null);
    const [error, setError] = useState<string | null>(null);
    const { data, isLoading } = useQuery({
        queryKey: ['tenant-guest-settings'],
        queryFn: async () => (await api.get('/tenants/current/guests')).data as GuestSettings,
    });
    const domains = domainsDraft ?? (data ? data.domain_allowlist.join('\n') : '');
    const requireSso = ssoDraft ?? data?.require_sso ?? false;
    const save = useMutation({
        mutationFn: async () => (await api.put('/tenants/current/guests', {
            domain_allowlist: domains.split(/[\s,]+/).filter(Boolean), require_sso: requireSso,
        })).data as GuestSettings,
        onSuccess: (d) => { setError(null); setDomains(null); setRequireSso(null); qc.setQueryData(['tenant-guest-settings'], d); },
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
                    <textarea id="guest-domains" rows={4} value={domains} onChange={(e) => setDomains(e.target.value)}
                        placeholder="company.com" className="w-full bg-raised border border-line rounded-md p-2 text-sm text-fg font-mono" />
                    <p className="text-xs text-fg-3">Leave empty to turn guest access off for this organization.</p>
                    <label className="flex items-center gap-2 text-sm text-fg cursor-pointer">
                        <input type="checkbox" checked={requireSso} disabled={!data.sso_ready}
                            onChange={(e) => setRequireSso(e.target.checked)} className="border-line-strong text-accent focus:ring-accent" />
                        Require SSO for guests
                    </label>
                    {!data.sso_ready && <p className="text-xs text-fg-3">Configure and enable SSO first.</p>}
                    {error && <p role="alert" className="text-sm text-severity-critical">{error}</p>}
                    <Button size="sm" onClick={() => save.mutate()} disabled={save.isPending}>Save</Button>
                </div>
            )}
        </section>
    );
}
