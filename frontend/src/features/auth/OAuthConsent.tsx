import { useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { KeyRound, ShieldAlert } from 'lucide-react';
import { api, ssoLoginUrl } from '../../api/client';
import { Badge, Button, Label, Select, Skeleton } from '../../components/ui';
import AuthLayout from './AuthLayout';
import { saveReturnTo } from './returnTo';

const PARAMS = ['response_type', 'client_id', 'redirect_uri', 'code_challenge', 'code_challenge_method',
    'state', 'scope', 'resource'] as const;

interface ConsentInfo {
    client_name: string;
    redirect_host: string;
    unverified: boolean;
    resource: string;
    tenants: { id: number; name: string; role: string }[];
    sso_tenants: { id: number; name: string; slug: string }[];
}

function errorText(err: unknown): string {
    const d = (err as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
    if (typeof d === 'string') return d;
    if (d && typeof d === 'object' && 'error_description' in d) return String((d as { error_description: unknown }).error_description);
    return 'Something went wrong. Please try again.';
}

export default function OAuthConsent() {
    const [search] = useSearchParams();
    const params = useMemo(() => {
        const out: Record<string, string> = {};
        for (const k of PARAMS) {
            const v = search.get(k);
            if (v !== null) out[k] = v;
        }
        return out;
    }, [search]);
    const framed = window.top !== window.self;

    const info = useQuery({
        queryKey: ['oauth-consent', params],
        queryFn: async () => (await api.get<ConsentInfo>('/oauth/authorize/info', { params })).data,
        retry: false,
        enabled: !framed,
        staleTime: Infinity,
    });
    const [picked, setPicked] = useState<number | null>(null);
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState('');

    // Consent must never be clickable through someone else's page.
    if (framed) {
        return <AuthLayout title="Authorize app" wide><p role="alert" className="text-sm text-severity-critical">This page can't be shown inside another site.</p></AuthLayout>;
    }
    if (info.isPending) {
        return <AuthLayout title="Authorize app" wide><Skeleton rows={3} /></AuthLayout>;
    }
    if (info.isError) {
        return (
            <AuthLayout title="This authorization request can't be completed" wide>
                <p role="alert" className="text-sm text-severity-critical">{errorText(info.error)}</p>
            </AuthLayout>
        );
    }

    const d = info.data;
    // Preselect only when there is no choice to make; otherwise the user must pick deliberately.
    const tenantId = picked ?? (d.tenants.length === 1 ? d.tenants[0].id : null);
    const tenant = d.tenants.find(t => t.id === tenantId);

    const decide = async (approve: boolean) => {
        setBusy(true);
        setError('');
        try {
            const r = await api.post<{ redirect: string }>('/oauth/authorize/decision',
                { ...params, tenant_id: tenantId, approve });
            // Only ever the URL the server built from the client's registered redirect URI.
            if (typeof r.data?.redirect === 'string') {
                window.location.assign(r.data.redirect);
                return;
            }
            setError('Something went wrong. Please try again.');
        } catch (err) {
            setError(errorText(err));
        }
        setBusy(false);
    };

    const ssoSignIn = (slug: string) => {
        saveReturnTo(window.location.pathname + window.location.search);
        window.location.assign(ssoLoginUrl(slug));
    };

    return (
        <AuthLayout wide
            title={<><strong className="font-bold">{d.client_name}</strong> wants to access SOC Hub as you.</>}
            description={<p>Redirects to <span className="font-mono text-fg">{d.redirect_host}</span></p>}>
            {d.unverified && (
                <Badge variant="high"><ShieldAlert size={12} /> Unverified app</Badge>
            )}

            {d.tenants.length > 0 ? (
                <div className="space-y-2">
                    <Label htmlFor="oauth-tenant">Organization</Label>
                    <Select id="oauth-tenant" value={tenantId ?? ''} disabled={busy}
                        onChange={e => setPicked(Number(e.target.value))}>
                        {tenantId === null && <option value="" disabled>Choose an organization…</option>}
                        {d.tenants.map(t => <option key={t.id} value={t.id}>{t.name}</option>)}
                    </Select>
                    {tenant && (
                        <p className="text-sm text-fg-2">
                            It will be able to do what your <strong className="text-fg">{tenant.role}</strong> role
                            allows in this organization, except administration.
                        </p>
                    )}
                </div>
            ) : (
                <p className="text-sm text-fg-2">
                    None of your organizations can be used with this app right now. An organization admin must
                    allow it first{d.sso_tenants.length > 0 ? ', or sign in with your organization’s SSO below' : ''}.
                </p>
            )}

            {d.sso_tenants.length > 0 && (
                <div className="space-y-2">
                    <p className="text-xs text-fg-3">These organizations need their own sign-in first:</p>
                    {d.sso_tenants.map(t => (
                        <Button key={t.id} type="button" variant="outline" className="w-full" disabled={busy}
                            onClick={() => ssoSignIn(t.slug)}>
                            <KeyRound size={14} /> Sign in with {t.name}&rsquo;s SSO
                        </Button>
                    ))}
                </div>
            )}

            {error && <p role="alert" className="text-sm text-severity-critical">{error}</p>}

            <div className="flex gap-3">
                <Button type="button" variant="outline" size="lg" className="flex-1" disabled={busy} onClick={() => decide(false)}>
                    Deny
                </Button>
                <Button type="button" size="lg" className="flex-1" disabled={busy || tenantId === null} onClick={() => decide(true)}>
                    Approve
                </Button>
            </div>
        </AuthLayout>
    );
}
