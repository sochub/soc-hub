import { useEffect, useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { ShieldCheck, KeyRound, Copy, Check, Save, Loader2, Timer } from 'lucide-react';
import { api } from '../../api/client';
import { cn } from '../../lib/utils';
import type { LoginMethod, SLAPolicyItem, SSOConfig, User } from '../../types';
import PageContainer from '../../components/layout/PageContainer';
import Modal, { btnPrimary, btnSecondary } from '../../components/layout/Modal';
import { mfaErrorMessage } from '../profile/mfaErrors';
import GuestSettingsSection from '../guests/GuestSettingsSection';
import ArtifactTypesSection from './ArtifactTypesSection';
import ReportBrandingSection from './ReportBrandingSection';

const SEVERITY_LABEL: Record<string, string> = {
    critical: 'Critical', high: 'High', medium: 'Medium', low: 'Low', info: 'Info',
};

function SLAPoliciesSection() {
    const qc = useQueryClient();
    const [rows, setRows] = useState<SLAPolicyItem[]>([]);
    const [savedFlash, setSavedFlash] = useState(false);

    const { data: policies, isLoading } = useQuery({
        queryKey: ['sla-policies'],
        queryFn: async () => (await api.get('/tenants/sla-policies')).data as SLAPolicyItem[],
    });

    useEffect(() => {
        if (policies) setRows(policies);
    }, [policies]);

    const save = useMutation({
        mutationFn: async () => (await api.put('/tenants/sla-policies', { policies: rows })).data as SLAPolicyItem[],
        onSuccess: (data) => {
            setRows(data);
            qc.invalidateQueries({ queryKey: ['sla-policies'] });
            setSavedFlash(true);
            setTimeout(() => setSavedFlash(false), 2000);
        },
    });

    const updateRow = (severity: string, field: 'response_target_minutes' | 'resolution_target_minutes', raw: string) => {
        const value = raw.trim() === '' ? null : Math.max(0, parseInt(raw, 10) || 0);
        setRows((rs) => rs.map((r) => (r.severity === severity ? { ...r, [field]: value } : r)));
    };

    return (
        <section className="bg-panel border border-line">
            <div className="flex items-center justify-between px-4 h-11 border-b border-line">
                <h2 className="flex items-center gap-2 text-sm font-semibold text-fg">
                    <Timer size={15} className="text-accent" /> SLA Policies
                </h2>
            </div>

            {isLoading ? (
                <p className="p-4 font-mono text-xs text-muted">$ loading…</p>
            ) : (
                <div className="p-4 space-y-4">
                    <p className="text-xs text-muted">
                        Response/resolution targets per severity, in minutes. Leave blank to disable SLA tracking
                        for that severity.
                    </p>
                    <table className="w-full text-sm">
                        <thead>
                            <tr className="text-left label-mono text-muted">
                                <th className="pb-2 font-normal">Severity</th>
                                <th className="pb-2 font-normal">Response (min)</th>
                                <th className="pb-2 font-normal">Resolution (min)</th>
                            </tr>
                        </thead>
                        <tbody className="divide-y divide-line">
                            {rows.map((r) => (
                                <tr key={r.severity}>
                                    <td className="py-2 text-fg-2">{SEVERITY_LABEL[r.severity] ?? r.severity}</td>
                                    <td className="py-2 pr-4">
                                        <input
                                            type="number" min={0}
                                            value={r.response_target_minutes ?? ''}
                                            onChange={(e) => updateRow(r.severity, 'response_target_minutes', e.target.value)}
                                            placeholder="—"
                                            className="w-28 border border-line-strong px-2 py-1 text-sm num focus:outline-hidden focus:ring-1 focus:ring-accent"
                                        />
                                    </td>
                                    <td className="py-2">
                                        <input
                                            type="number" min={0}
                                            value={r.resolution_target_minutes ?? ''}
                                            onChange={(e) => updateRow(r.severity, 'resolution_target_minutes', e.target.value)}
                                            placeholder="—"
                                            className="w-28 border border-line-strong px-2 py-1 text-sm num focus:outline-hidden focus:ring-1 focus:ring-accent"
                                        />
                                    </td>
                                </tr>
                            ))}
                        </tbody>
                    </table>

                    <button onClick={() => save.mutate()} disabled={save.isPending}
                        className={cn('inline-flex items-center gap-1.5 h-9 px-4 text-sm font-medium text-white transition-colors disabled:opacity-50',
                            savedFlash ? 'bg-emerald-600' : 'bg-accent hover:bg-accent-hover')}>
                        {save.isPending ? <Loader2 size={14} className="animate-spin" /> : savedFlash ? <Check size={14} /> : <Save size={14} />}
                        {savedFlash ? 'Saved' : 'Save SLA policies'}
                    </button>
                </div>
            )}
        </section>
    );
}

function MfaRequirementSection() {
    const qc = useQueryClient();
    const [error, setError] = useState<string | null>(null);
    const [confirmOn, setConfirmOn] = useState(false);
    const { data, isLoading } = useQuery({
        queryKey: ['tenant-security'],
        queryFn: async () => (await api.get('/tenants/current/security')).data as { require_mfa: boolean; members_without_mfa: number },
    });
    const toggle = useMutation({
        mutationFn: async (require_mfa: boolean) => (await api.put('/tenants/current/security', { require_mfa })).data,
        onSuccess: (d) => { setError(null); setConfirmOn(false); qc.setQueryData(['tenant-security'], d); },
        onError: (err) => setError(mfaErrorMessage(err, 'Could not update the setting.')),
    });
    const n = data?.members_without_mfa ?? 0;
    return (
        <section className="bg-panel border border-line">
            <div className="flex items-center justify-between px-4 h-11 border-b border-line">
                <h2 className="flex items-center gap-2 text-sm font-semibold text-fg">
                    <ShieldCheck size={15} className="text-accent" /> Two-factor authentication
                </h2>
            </div>
            {isLoading || !data ? (
                <p className="p-4 font-mono text-xs text-muted">$ loading…</p>
            ) : (
                <div className="p-4 space-y-3">
                    <label className="flex items-center gap-2 text-sm text-fg cursor-pointer">
                        <input type="checkbox" checked={data.require_mfa} disabled={toggle.isPending}
                            onChange={(e) => (e.target.checked ? setConfirmOn(true) : toggle.mutate(false))}
                            className="border-line-strong text-accent focus:ring-accent" />
                        Require two-factor authentication
                    </label>
                    <p className="text-xs text-fg-3">
                        {n === 0
                            ? 'All members have two-factor authentication enabled.'
                            : `${n} member${n === 1 ? '' : 's'} ${n === 1 ? "doesn't" : "don't"} have MFA yet.`}
                        {data.require_mfa && n > 0 && ' They will be asked to set it up at their next sign-in.'}
                    </p>
                    <p className="text-xs text-fg-3">Members who sign in through single sign-on are not affected; their identity provider handles two-factor.</p>
                    {error && <p role="alert" className="text-xs text-red-300">{error}</p>}
                </div>
            )}
            <Modal open={confirmOn} onClose={() => setConfirmOn(false)} title="Require two-factor authentication?" size="md"
                footer={<>
                    <button type="button" className={btnSecondary} onClick={() => setConfirmOn(false)}>Cancel</button>
                    <button type="button" className={btnPrimary} disabled={toggle.isPending} onClick={() => toggle.mutate(true)}>
                        {toggle.isPending && <Loader2 size={14} className="animate-spin" />} Require two-factor
                    </button>
                </>}>
                <div className="space-y-2 text-sm text-fg-2">
                    <p>Every member of this organization will need two-factor authentication to sign in or switch into it.</p>
                    {n > 0 && <p>{n} member{n === 1 ? '' : 's'} without it will be asked to set it up at their next sign-in.</p>}
                    <p>Members who sign in through single sign-on are not affected; their identity provider handles two-factor.</p>
                </div>
            </Modal>
        </section>
    );
}

function detailMessage(d: unknown): string {
    if (typeof d === 'string') return d;
    if (Array.isArray(d)) {
        const m = d.map((x) => (x && typeof x === 'object' && typeof (x as { msg?: unknown }).msg === 'string' ? (x as { msg: string }).msg : '')).filter(Boolean);
        if (m.length) return m.join('; ');
    }
    return 'Save failed';
}

function CopyField({ label, value }: { label: string; value: string }) {
    const [copied, setCopied] = useState(false);
    const copy = async () => {
        await navigator.clipboard.writeText(value);
        setCopied(true);
        setTimeout(() => setCopied(false), 1500);
    };
    return (
        <div>
            <p className="label-mono mb-1">{label}</p>
            <div className="flex items-center gap-1.5">
                <code className="flex-1 num text-[11px] text-fg-2 bg-surface border border-line px-2 py-1.5 truncate">{value}</code>
                <button onClick={copy} className="p-1.5 border border-line text-fg-3 hover:text-fg hover:bg-surface" aria-label={`Copy ${label}`}>
                    {copied ? <Check size={13} className="text-emerald-400" /> : <Copy size={13} />}
                </button>
            </div>
        </div>
    );
}

export default function Settings() {
    const qc = useQueryClient();
    const { data: me } = useQuery({
        queryKey: ['currentUser'],
        queryFn: async () => (await api.get('/users/me')).data as User,
        staleTime: 5 * 60 * 1000,
    });
    const isAdmin = me?.role === 'admin' || me?.is_super_admin;

    const { data: cfg, isLoading } = useQuery({
        queryKey: ['sso-config'],
        queryFn: async () => (await api.get('/tenants/sso-config')).data as SSOConfig,
        enabled: !!isAdmin,
        retry: false,
    });

    const [form, setForm] = useState({
        member_login_method: 'password' as LoginMethod, guest_login_method: 'password' as LoginMethod,
        idp_entity_id: '', idp_sso_url: '', idp_x509_cert: '',
        oidc_issuer: '', oidc_client_id: '', oidc_client_secret: '', oidc_scopes: 'openid email profile',
        auto_provision: false, default_role: 'viewer' as 'analyst' | 'viewer',
    });
    const [savedFlash, setSavedFlash] = useState(false);

    useEffect(() => {
        if (cfg) {
            setForm({
                member_login_method: cfg.member_login_method,
                guest_login_method: cfg.guest_login_method,
                oidc_issuer: cfg.oidc_issuer ?? '',
                oidc_client_id: cfg.oidc_client_id ?? '',
                oidc_client_secret: '',
                oidc_scopes: cfg.oidc_scopes,
                idp_entity_id: cfg.idp_entity_id ?? '',
                idp_sso_url: cfg.idp_sso_url ?? '',
                idp_x509_cert: cfg.idp_x509_cert ?? '',
                auto_provision: cfg.auto_provision,
                default_role: cfg.default_role,
            });
        }
    }, [cfg]);

    const save = useMutation({
        mutationFn: async () => (await api.put('/tenants/sso-config', form)).data as SSOConfig,
        onSuccess: () => {
            qc.invalidateQueries({ queryKey: ['sso-config'] });
            setSavedFlash(true);
            setTimeout(() => setSavedFlash(false), 2000);
        },
    });

    const field = (label: string, key: 'idp_entity_id' | 'idp_sso_url' | 'oidc_issuer' | 'oidc_client_id' | 'oidc_client_secret' | 'oidc_scopes',
        placeholder: string, type = 'text') => (
        <div>
            <label className="label-mono block mb-1">{label}</label>
            <input type={type} autoComplete="off" value={form[key]} onChange={e => setForm(f => ({ ...f, [key]: e.target.value }))}
                placeholder={placeholder}
                className="w-full bg-panel border border-line-strong px-2.5 py-2 text-sm num focus:outline-hidden focus:ring-1 focus:ring-accent" />
        </div>
    );

    return (
        <PageContainer width="narrow">
            <div>
                <h1 className="text-xl font-semibold text-fg tracking-tight">Tenant settings</h1>
                <p className="label-mono mt-1">tenant configuration</p>
            </div>

            {!isAdmin ? (
                <div className="bg-panel border border-line p-8 text-center text-sm text-fg-3">
                    Tenant settings are managed by your tenant admin.
                </div>
            ) : (
                <section className="bg-panel border border-line">
                    <div className="flex items-center justify-between px-4 h-11 border-b border-line">
                        <h2 className="flex items-center gap-2 text-sm font-semibold text-fg">
                            <KeyRound size={15} className="text-accent" /> Single Sign-On
                        </h2>
                    </div>

                    {isLoading ? (
                        <p className="p-4 font-mono text-xs text-muted">$ loading…</p>
                    ) : (
                        <div className="p-4 grid grid-cols-1 lg:grid-cols-2 gap-6">
                            {/* IdP side */}
                            <div className="space-y-4">
                                <div className="flex gap-4 flex-wrap">
                                    {([['member sign-in', 'member_login_method'], ['guest sign-in', 'guest_login_method']] as const).map(([label, key]) => (
                                        <label key={key} className="text-xs text-fg-2">
                                            <span className="label-mono block mb-1">{label}</span>
                                            <select value={form[key]} aria-label={label === 'member sign-in' ? 'Member sign-in' : 'Guest sign-in'}
                                                onChange={e => setForm(f => ({ ...f, [key]: e.target.value as LoginMethod }))}
                                                className="border border-line-strong bg-panel px-1.5 py-1.5 text-sm">
                                                <option value="password">Password</option>
                                                <option value="saml">SAML</option>
                                                <option value="oidc">OIDC</option>
                                            </select>
                                        </label>
                                    ))}
                                </div>
                                <p className="label-mono">// saml</p>
                                <p className="label-mono">// identity provider (from your idp)</p>
                                {field('idp entity id', 'idp_entity_id', 'https://idp.example.com/entityid')}
                                {field('idp sso url', 'idp_sso_url', 'https://idp.example.com/sso/saml')}
                                <div>
                                    <label className="label-mono block mb-1">idp x509 certificate</label>
                                    <textarea value={form.idp_x509_cert}
                                        onChange={e => setForm(f => ({ ...f, idp_x509_cert: e.target.value }))}
                                        rows={6} placeholder="-----BEGIN CERTIFICATE-----"
                                        className="w-full bg-panel border border-line-strong px-2.5 py-2 text-[11px] num focus:outline-hidden focus:ring-1 focus:ring-accent" />
                                    <p className="text-[11px] text-fg-3 mt-1">The IdP must sign the assertion (in Keycloak, enable “Sign assertions”).</p>
                                </div>
                                <p className="label-mono pt-2">// oidc</p>
                                {field('issuer url', 'oidc_issuer', 'https://idp.example.com')}
                                {field('client id', 'oidc_client_id', '')}
                                {field('client secret', 'oidc_client_secret', cfg?.has_oidc_client_secret ? 'saved (leave blank to keep)' : '', 'password')}
                                {field('scopes', 'oidc_scopes', 'openid email profile')}
                                <div className="flex items-center gap-4 flex-wrap">
                                    <label className="flex items-center gap-2 text-xs text-fg-2 cursor-pointer">
                                        <input type="checkbox" checked={form.auto_provision}
                                            onChange={e => setForm(f => ({ ...f, auto_provision: e.target.checked }))}
                                            className="border-line-strong text-accent focus:ring-accent" />
                                        auto-provision new users
                                    </label>
                                    <label className="flex items-center gap-2 text-xs text-fg-2">
                                        default role
                                        <select value={form.default_role}
                                            onChange={e => setForm(f => ({ ...f, default_role: e.target.value as 'analyst' | 'viewer' }))}
                                            disabled={!form.auto_provision}
                                            className="border border-line-strong px-1.5 py-1 text-xs font-mono disabled:opacity-50">
                                            <option value="viewer">viewer</option>
                                            <option value="analyst">analyst</option>
                                        </select>
                                    </label>
                                </div>
                                <div className="flex items-center gap-3">
                                    <button onClick={() => save.mutate()} disabled={save.isPending}
                                        className={cn('inline-flex items-center gap-1.5 h-9 px-4 text-sm font-medium text-white transition-colors disabled:opacity-50',
                                            savedFlash ? 'bg-emerald-600' : 'bg-accent hover:bg-accent-hover')}>
                                        {save.isPending ? <Loader2 size={14} className="animate-spin" /> : savedFlash ? <Check size={14} /> : <Save size={14} />}
                                        {savedFlash ? 'Saved' : 'Save SSO settings'}
                                    </button>
                                    {save.isError && (
                                        <span className="text-xs text-severity-critical">
                                            {detailMessage((save.error as any)?.response?.data?.detail)}
                                        </span>
                                    )}
                                </div>
                            </div>

                            {/* SP side */}
                            <div className="space-y-3 lg:border-l lg:border-line lg:pl-6">
                                <p className="label-mono">// service provider (give these to your idp)</p>
                                {cfg && (
                                    <>
                                        <CopyField label="sp entity id / audience" value={cfg.sp_entity_id} />
                                        <CopyField label="acs url (single sign on url)" value={cfg.sp_acs_url} />
                                        <CopyField label="sp metadata url" value={cfg.sp_metadata_url} />
                                        <CopyField label="sso login url (for your users)" value={cfg.sp_login_url} />
                                        <CopyField label="oidc redirect uri (give this to your idp)" value={cfg.oidc_redirect_uri} />
                                    </>
                                )}
                                <p className="text-[11px] text-muted leading-relaxed pt-1">
                                    Users sign in via “Sign in with SSO” on the login page using your tenant slug.
                                    Send the email as the NameID (format emailAddress) or an <code className="num">email</code> attribute,
                                    and the full name as <code className="num">displayName</code>. Admins can always use the password form on the login page.
                                </p>
                            </div>
                        </div>
                    )}
                </section>
            )}

            {isAdmin && <MfaRequirementSection />}
            {isAdmin && <GuestSettingsSection />}
            {isAdmin && <SLAPoliciesSection />}
            {isAdmin && <ArtifactTypesSection />}
            {isAdmin && <ReportBrandingSection />}
        </PageContainer>
    );
}
