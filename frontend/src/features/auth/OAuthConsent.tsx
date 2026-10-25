import { useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { KeyRound, ShieldAlert } from 'lucide-react';
import { api, ssoLoginUrl } from '../../api/client';
import { Badge, Button, Label } from '../../components/ui';
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

function Shell({ children }: { children: React.ReactNode }) {
    return (
        <div className="min-h-screen flex items-center justify-center bg-canvas text-fg font-sans p-4">
            <div className="w-full max-w-md rounded-2xl border border-line bg-panel p-6 sm:p-8 space-y-6 shadow-lg">
                <div className="flex items-center gap-2.5">
                    <img src={`${import.meta.env.BASE_URL}logo.png`} alt="" className="w-8 h-8" />
                    <span className="font-display text-lg font-semibold tracking-tight">sochub</span>
                </div>
                {children}
            </div>
        </div>
    );
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
        return <Shell><p role="alert" className="text-sm text-severity-critical">This page can't be shown inside another site.</p></Shell>;
    }
    if (info.isPending) {
        return <Shell><p className="text-sm text-fg-2">Loading…</p></Shell>;
    }
    if (info.isError) {
        return (
            <Shell>
                <h1 className="text-xl font-semibold">This authorization request can't be completed</h1>
                <p role="alert" className="text-sm text-severity-critical">{errorText(info.error)}</p>
            </Shell>
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
        <Shell>
            <div className="space-y-3">
                {d.unverified && (
                    <Badge variant="high"><ShieldAlert size={12} /> Unverified app</Badge>
                )}
                <h1 className="text-xl font-semibold leading-snug">
                    <strong className="font-bold">{d.client_name}</strong> wants to access SOC Hub as you.
                </h1>
                <p className="text-sm text-fg-2">Redirects to <span className="font-mono text-fg">{d.redirect_host}</span></p>
            </div>

            {d.tenants.length > 0 ? (
                <div className="space-y-2">
                    <Label htmlFor="oauth-tenant">Organization</Label>
                    <select id="oauth-tenant" value={tenantId ?? ''} disabled={busy}
                        onChange={e => setPicked(Number(e.target.value))}
                        className="w-full rounded-md border border-line-strong bg-panel px-2 py-2 text-sm text-fg">
                        {tenantId === null && <option value="" disabled>Choose an organization…</option>}
                        {d.tenants.map(t => <option key={t.id} value={t.id}>{t.name}</option>)}
                    </select>
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
                <Button type="button" variant="outline" className="flex-1" disabled={busy} onClick={() => decide(false)}>
                    Deny
                </Button>
                <Button type="button" className="flex-1" disabled={busy || tenantId === null} onClick={() => decide(true)}>
                    Approve
                </Button>
            </div>
        </Shell>
    );
}
