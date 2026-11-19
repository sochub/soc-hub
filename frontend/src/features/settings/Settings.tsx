import { useEffect, useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { ShieldCheck, KeyRound, Copy, Check, Save, Loader2, Timer, Clock } from 'lucide-react';
import { Navigate } from 'react-router-dom';
import { api } from '../../api/client';
import type { TenantOAuthSession, LoginMethod, SLAPolicyItem, SSOConfig, User } from '../../types';
import Modal from '../../components/layout/Modal';
import { Button, Input, Label, Select, Skeleton, Textarea } from '../../components/ui';
import SettingsPageHeader from './SettingsPageHeader';
import { formatDateTime } from '../../utils/datetime';
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
        <section id="sla" className="scroll-mt-4 rounded-md bg-panel border border-line">
            <div className="flex items-center justify-between px-4 h-11 border-b border-line">
                <h3 className="flex items-center gap-2 text-sm font-semibold text-fg">
                    <Timer size={15} className="text-accent" /> SLA policies
                </h3>
            </div>

            {isLoading ? (
                <Skeleton rows={3} className="p-4" />
            ) : (
                <div className="p-4 space-y-4">
                    <p className="text-xs text-muted">
                        Response/resolution targets per severity, in minutes. Leave blank to disable SLA tracking
                        for that severity.
                    </p>
                    <table className="w-full text-sm">
                        <thead>
                            <tr className="text-left label-caps">
                                <th className="pb-2">Severity</th>
                                <th className="pb-2">Response (min)</th>
                                <th className="pb-2">Resolution (min)</th>
                            </tr>
                        </thead>
                        <tbody className="divide-y divide-line">
                            {rows.map((r) => (
                                <tr key={r.severity}>
                                    <td className="py-2 text-fg-2">{SEVERITY_LABEL[r.severity] ?? r.severity}</td>
                                    <td className="py-2 pr-4">
                                        <Input
                                            type="number" min={0}
                                            value={r.response_target_minutes ?? ''} aria-label={`${SEVERITY_LABEL[r.severity] ?? r.severity} response target (minutes)`}
                                            onChange={(e) => updateRow(r.severity, 'response_target_minutes', e.target.value)}
                                            placeholder="—"
                                            inputSize="sm" className="w-28 num"
                                        />
                                    </td>
                                    <td className="py-2">
                                        <Input
                                            type="number" min={0}
                                            value={r.resolution_target_minutes ?? ''} aria-label={`${SEVERITY_LABEL[r.severity] ?? r.severity} resolution target (minutes)`}
                                            onChange={(e) => updateRow(r.severity, 'resolution_target_minutes', e.target.value)}
                                            placeholder="—"
                                            inputSize="sm" className="w-28 num"
                                        />
                                    </td>
                                </tr>
                            ))}
                        </tbody>
                    </table>

                    <Button onClick={() => save.mutate()} disabled={save.isPending}>
                        {save.isPending ? <Loader2 size={14} className="animate-spin" /> : savedFlash ? <Check size={14} /> : <Save size={14} />}
                        {savedFlash ? 'Saved' : 'Save SLA policies'}
                    </Button>
                </div>
            )}
        </section>
    );
}

const IDLE_CHOICES: [string, number | null][] = [['Default (30 days)', null], ['2 hours', 120], ['4 hours', 240], ['8 hours', 480], ['24 hours', 1440], ['7 days', 10080]];
const MAX_CHOICES: [string, number | null][] = [['No limit (default)', null], ['8 hours', 480], ['12 hours', 720], ['24 hours', 1440], ['7 days', 10080], ['30 days', 43200]];
type SessionPolicy = { session_idle_minutes: number | null; session_max_minutes: number | null };

function SessionPolicySection() {
    const qc = useQueryClient();
    const { data, isLoading } = useQuery({
        queryKey: ['tenant-security'],
        queryFn: async () => (await api.get('/tenants/current/security')).data as SessionPolicy,
    });
    const save = useMutation({
        mutationFn: async (patch: Partial<SessionPolicy>) => (await api.put('/tenants/current/security', patch)).data,
        onSuccess: (d) => qc.setQueryData(['tenant-security'], d),
    });
    const select = (label: string, key: keyof SessionPolicy, choices: [string, number | null][]) => (
        <div>
            <Label htmlFor={`session-${key}`}>{label}</Label>
            <Select id={`session-${key}`} value={data?.[key] ?? ''} disabled={save.isPending}
                onChange={(e) => save.mutate({ [key]: e.target.value ? Number(e.target.value) : null })}
                className="w-48">
                {choices.map(([l, v]) => <option key={l} value={v ?? ''}>{l}</option>)}
            </Select>
        </div>
    );
    return (
        <section id="sessions" className="scroll-mt-4 rounded-md bg-panel border border-line">
            <div className="flex items-center justify-between px-4 h-11 border-b border-line">
                <h3 className="flex items-center gap-2 text-sm font-semibold text-fg">
                    <Clock size={15} className="text-accent" /> Session length
                </h3>
            </div>
            {isLoading || !data ? (
                <Skeleton rows={3} className="p-4" />
            ) : (
                <div className="p-4 space-y-3">
                    <div className="flex flex-wrap gap-4">
                        {select('Sign out after inactivity', 'session_idle_minutes', IDLE_CHOICES)}
                        {select('Require sign-in again after', 'session_max_minutes', MAX_CHOICES)}
                    </div>
                    <p className="text-xs text-fg-3">
                        Applies to browser sign-ins of every member. Someone who belongs to several organizations gets the strictest setting.
                        Limits are checked when the session renews, so a sign-out can come up to an hour late. API tokens and MCP clients are not affected.
                    </p>
                    {save.isError && <p role="alert" className="text-xs text-severity-critical">{mfaErrorMessage(save.error, 'Could not update the setting.')}</p>}
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
        <section id="mfa" className="scroll-mt-4 rounded-md bg-panel border border-line">
            <div className="flex items-center justify-between px-4 h-11 border-b border-line">
                <h3 className="flex items-center gap-2 text-sm font-semibold text-fg">
                    <ShieldCheck size={15} className="text-accent" /> Two-factor authentication
                </h3>
            </div>
            {isLoading || !data ? (
                <Skeleton rows={3} className="p-4" />
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
                    {error && <p role="alert" className="text-xs text-severity-critical">{error}</p>}
                </div>
            )}
            <Modal open={confirmOn} onClose={() => setConfirmOn(false)} title="Require two-factor authentication?" size="md"
                footer={<>
                    <Button variant="secondary" onClick={() => setConfirmOn(false)}>Cancel</Button>
                    <Button disabled={toggle.isPending} onClick={() => toggle.mutate(true)}>
                        {toggle.isPending && <Loader2 size={14} className="animate-spin" />} Require two-factor
                    </Button>
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

function McpSection() {
    const qc = useQueryClient();
    const [error, setError] = useState<string | null>(null);
    const { data, isLoading } = useQuery({
        queryKey: ['tenant-security'],
        queryFn: async () => (await api.get('/tenants/current/security')).data as { require_mfa: boolean; members_without_mfa: number; mcp_enabled: boolean },
    });
    const toggle = useMutation({
        mutationFn: async (mcp_enabled: boolean) => (await api.put('/tenants/current/security', { mcp_enabled })).data,
        onSuccess: (d) => { setError(null); qc.setQueryData(['tenant-security'], d); },
        onError: (err) => setError(mfaErrorMessage(err, 'Could not update the setting.')),
    });
    const { data: apps } = useQuery({
        queryKey: ['tenant-oauth-sessions'],
        queryFn: async () => (await api.get('/tenants/current/oauth-sessions')).data as TenantOAuthSession[],
    });
    const revoke = useMutation({
        mutationFn: async (id: string) => { await api.delete(`/tenants/current/oauth-sessions/${id}`); },
        onSuccess: () => { setError(null); void qc.invalidateQueries({ queryKey: ['tenant-oauth-sessions'] }); },
        onError: (err) => setError(mfaErrorMessage(err, 'Could not revoke the session.')),
    });
    return (
        <section id="mcp" className="scroll-mt-4 rounded-md bg-panel border border-line">
            <div className="flex items-center justify-between px-4 h-11 border-b border-line">
                <h3 className="flex items-center gap-2 text-sm font-semibold text-fg">
                    <KeyRound size={15} className="text-accent" /> AI clients (MCP)
                </h3>
            </div>
            {isLoading || !data ? (
                <Skeleton rows={3} className="p-4" />
            ) : (
                <div className="p-4 space-y-3">
                    <label className="flex items-center gap-2 text-sm text-fg cursor-pointer">
                        <input type="checkbox" checked={data.mcp_enabled} disabled={toggle.isPending}
                            onChange={(e) => toggle.mutate(e.target.checked)}
                            className="border-line-strong text-accent focus:ring-accent" />
                        Allow AI clients (MCP)
                    </label>
                    <p className="text-xs text-fg-3">Lets members connect AI clients that act with their own permissions in this organization.</p>
                    {error && <p role="alert" className="text-xs text-severity-critical">{error}</p>}
                    <h4 className="text-xs font-semibold text-fg-2 pt-2">Connected apps</h4>
                    {!apps || apps.length === 0 ? <p className="text-xs text-fg-3">No connected apps.</p> : (
                        <div className="overflow-x-auto"><table className="w-full text-sm">
                            <thead>
                                <tr className="text-left label-caps border-b border-line">
                                    <th className="py-2">App</th><th className="py-2">Member</th>
                                    <th className="py-2">Last used</th><th><span className="sr-only">Actions</span></th>
                                </tr>
                            </thead>
                            <tbody>
                                {apps.map((a) => (
                                    <tr key={a.id} className="border-b border-line">
                                        <td className="py-2 text-fg">{a.client_name ?? 'Unknown app'}</td>
                                        <td className="py-2 text-fg-2">{a.user_email}</td>
                                        <td className="py-2 text-fg-3">{formatDateTime(a.last_used_at)}</td>
                                        <td className="py-2 text-right">
                                            <Button variant="secondary" size="sm" disabled={revoke.isPending}
                                                onClick={() => revoke.mutate(a.id)}>Revoke</Button>
                                        </td>
                                    </tr>
                                ))}
                            </tbody>
                        </table></div>
                    )}
                </div>
            )}
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
            <p className="label-caps mb-1">{label}</p>
            <div className="flex items-center gap-1.5">
                <code className="flex-1 font-mono text-xs text-fg-2 bg-surface border border-line rounded-md px-2 py-1.5 truncate">{value}</code>
                <Button variant="outline" size="icon" onClick={copy} className="h-8 w-8" aria-label={`Copy ${label}`}>
                    {copied ? <Check size={13} className="text-lime" /> : <Copy size={13} />}
                </Button>
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
        member_login_method: 'password' as LoginMethod,
        idp_entity_id: '', idp_sso_url: '', idp_x509_cert: '',
        oidc_issuer: '', oidc_client_id: '', oidc_client_secret: '', oidc_scopes: 'openid email profile',
        auto_provision: false, default_role: 'viewer' as 'analyst' | 'viewer',
        allowed_email_domains: '', // comma-separated in the form, a list on the wire
        saml_allow_idp_initiated: false,
        groups_claim: 'groups',
    });
    const [savedFlash, setSavedFlash] = useState(false);

    useEffect(() => {
        if (cfg) {
            setForm({
                member_login_method: cfg.member_login_method,
                oidc_issuer: cfg.oidc_issuer ?? '',
                oidc_client_id: cfg.oidc_client_id ?? '',
                oidc_client_secret: '',
                oidc_scopes: cfg.oidc_scopes,
                idp_entity_id: cfg.idp_entity_id ?? '',
                idp_sso_url: cfg.idp_sso_url ?? '',
                idp_x509_cert: cfg.idp_x509_cert ?? '',
                auto_provision: cfg.auto_provision,
                allowed_email_domains: (cfg.allowed_email_domains ?? []).join(', '),
                default_role: cfg.default_role,
                saml_allow_idp_initiated: cfg.saml_allow_idp_initiated,
                groups_claim: cfg.groups_claim ?? 'groups',
            });
        }
    }, [cfg]);

    const save = useMutation({
        mutationFn: async () => (await api.put('/tenants/sso-config', {
            ...form,
            groups_claim: form.groups_claim.trim() || 'groups',
            allowed_email_domains: form.allowed_email_domains.split(/[\s,]+/).map(d => d.trim()).filter(Boolean),
        })).data as SSOConfig,
        onSuccess: () => {
            qc.invalidateQueries({ queryKey: ['sso-config'] });
            qc.invalidateQueries({ queryKey: ['tenant-guest-settings'] }); // guest SSO readiness depends on this config
            setSavedFlash(true);
            setTimeout(() => setSavedFlash(false), 2000);
        },
    });

    const field = (label: string, key: 'idp_entity_id' | 'idp_sso_url' | 'oidc_issuer' | 'oidc_client_id' | 'oidc_client_secret' | 'oidc_scopes' | 'groups_claim',
        placeholder: string, type = 'text') => (
        <div>
            <Label htmlFor={`sso-${key}`}>{label}</Label>
            <Input id={`sso-${key}`} type={type} autoComplete="off" value={form[key]} onChange={e => setForm(f => ({ ...f, [key]: e.target.value }))}
                placeholder={placeholder} className="font-mono" />
        </div>
    );

    if (me && !isAdmin) return <Navigate to="/settings/profile" replace />;

    const toc: [string, string][] = [
        ['sso', 'Single sign-on'], ['mfa', 'Two-factor'], ['sessions', 'Session length'], ['mcp', 'AI clients'],
        ['guests', 'Case guests'], ['sla', 'SLA policies'], ['artifact-types', 'Artifact types'], ['report-branding', 'Report branding'],
    ];

    return (
        <div className="space-y-6">
            <SettingsPageHeader title="General" description="Sign-in, security and defaults for this organization." />
            <nav aria-label="On this page" className="flex flex-wrap gap-x-4 gap-y-1 text-sm border-b border-line pb-3">
                {toc.map(([id, label]) => (
                    <a key={id} href={`#${id}`} className="text-fg-3 hover:text-fg underline-offset-4 hover:underline">{label}</a>
                ))}
            </nav>

            {!me ? <Skeleton rows={6} /> : (
                <section id="sso" className="scroll-mt-4 rounded-md bg-panel border border-line">
                    <div className="flex items-center justify-between px-4 h-11 border-b border-line">
                        <h3 className="flex items-center gap-2 text-sm font-semibold text-fg">
                            <KeyRound size={15} className="text-accent" /> Single sign-on
                        </h3>
                    </div>

                    {isLoading ? (
                        <Skeleton rows={3} className="p-4" />
                    ) : (
                        <div className="p-4 grid grid-cols-1 lg:grid-cols-2 gap-6">
                            {/* IdP side */}
                            <div className="space-y-4">
                                <div>
                                    <Label htmlFor="sso-member-login">Member sign-in</Label>
                                    <Select id="sso-member-login" value={form.member_login_method} className="w-48"
                                        onChange={e => setForm(f => ({ ...f, member_login_method: e.target.value as LoginMethod }))}>
                                        <option value="password">Password</option>
                                        <option value="saml">SAML</option>
                                        <option value="oidc">OIDC</option>
                                    </Select>
                                    <p className="text-xs text-fg-3 mt-1">Guest sign-in is set in the Case guests section below.</p>
                                </div>
                                {field('Groups claim or attribute', 'groups_claim', 'groups')}
                                <p className="text-xs text-fg-3 -mt-2">
                                    Name of the SAML attribute or OIDC claim carrying the user's groups, used to sync escalation
                                    groups at sign-in. If the IdP omits it, memberships are left unchanged. Okta: add <code>groups</code> to the OIDC
                                    scopes above and set the app's Groups claim (filter e.g. "Matches regex .*"). Entra: add the groups
                                    claim under Token configuration. SAML: use the attribute name your IdP sends.
                                </p>
                                <h4 className="text-sm font-semibold text-fg pt-2">SAML</h4>
                                <p className="label-caps">Identity provider (from your IdP)</p>
                                {field('IdP entity ID', 'idp_entity_id', 'https://idp.example.com/entityid')}
                                {field('IdP SSO URL', 'idp_sso_url', 'https://idp.example.com/sso/saml')}
                                <div>
                                    <Label htmlFor="sso-idp-cert">IdP X.509 certificate</Label>
                                    <Textarea id="sso-idp-cert" value={form.idp_x509_cert}
                                        onChange={e => setForm(f => ({ ...f, idp_x509_cert: e.target.value }))}
                                        rows={6} placeholder="-----BEGIN CERTIFICATE-----"
                                        className="font-mono text-xs" />
                                    <p className="text-xs text-fg-3 mt-1">The IdP must sign the assertion (in Keycloak, enable “Sign assertions”).</p>
                                </div>
                                <div>
                                    <label className="flex items-center gap-2 text-xs text-fg-2 cursor-pointer">
                                        <input type="checkbox" checked={form.saml_allow_idp_initiated}
                                            onChange={e => setForm(f => ({ ...f, saml_allow_idp_initiated: e.target.checked }))}
                                            className="border-line-strong text-accent focus:ring-accent" />
                                        Allow IdP-initiated sign-in
                                    </label>
                                    <p className="text-xs text-fg-3 mt-1">
                                        Off by default: only sign-ins started from this site are accepted. Turning it on lets users
                                        start from the IdP's app dashboard, but also exposes the organization to login CSRF: anyone with an
                                        account at the same IdP can silently sign a colleague's browser into their own account.
                                    </p>
                                </div>
                                <h4 className="text-sm font-semibold text-fg pt-2">OIDC</h4>
                                {field('Issuer URL', 'oidc_issuer', 'https://idp.example.com')}
                                {field('Client ID', 'oidc_client_id', '')}
                                {field('Client secret', 'oidc_client_secret', cfg?.has_oidc_client_secret ? 'saved (leave blank to keep)' : '', 'password')}
                                {field('Scopes', 'oidc_scopes', 'openid email profile')}
                                <div className="flex items-center gap-4 flex-wrap">
                                    <label className="flex items-center gap-2 text-xs text-fg-2 cursor-pointer">
                                        <input type="checkbox" checked={form.auto_provision}
                                            onChange={e => setForm(f => ({ ...f, auto_provision: e.target.checked }))}
                                            className="border-line-strong text-accent focus:ring-accent" />
                                        Auto-provision new users
                                    </label>
                                    <label className="flex items-center gap-2 text-xs text-fg-2">
                                        Default role
                                        <Select inputSize="sm" className="w-28" value={form.default_role}
                                            onChange={e => setForm(f => ({ ...f, default_role: e.target.value as 'analyst' | 'viewer' }))}
                                            disabled={!form.auto_provision}>
                                            <option value="viewer">Viewer</option>
                                            <option value="analyst">Analyst</option>
                                        </Select>
                                    </label>
                                </div>
                                <div>
                                    <Label htmlFor="sso-domains">Auto-provision email domains</Label>
                                    <Input id="sso-domains" type="text" autoComplete="off" value={form.allowed_email_domains}
                                        onChange={e => setForm(f => ({ ...f, allowed_email_domains: e.target.value }))}
                                        placeholder="example.com, example.org" className="font-mono" />
                                    <p className="text-xs text-fg-3 mt-1">
                                        Required with auto-provisioning: only new users with an email at one of these domains get an account on first SSO sign-in.
                                    </p>
                                </div>
                                <div className="flex items-center gap-3">
                                    <Button onClick={() => save.mutate()} disabled={save.isPending}>
                                        {save.isPending ? <Loader2 size={14} className="animate-spin" /> : savedFlash ? <Check size={14} /> : <Save size={14} />}
                                        {savedFlash ? 'Saved' : 'Save SSO settings'}
                                    </Button>
                                    {save.isError && (
                                        <span role="alert" className="text-xs text-severity-critical">
                                            {detailMessage((save.error as any)?.response?.data?.detail)}
                                        </span>
                                    )}
                                </div>
                            </div>

                            {/* SP side */}
                            <div className="space-y-3 lg:border-l lg:border-line lg:pl-6">
                                <p className="label-caps">Service provider (give these to your IdP)</p>
                                {cfg && (
                                    <>
                                        <CopyField label="SP entity ID / audience" value={cfg.sp_entity_id} />
                                        <CopyField label="ACS URL (single sign-on URL)" value={cfg.sp_acs_url} />
                                        <CopyField label="SP metadata URL" value={cfg.sp_metadata_url} />
                                        <CopyField label="SSO login URL (for your users)" value={cfg.sp_login_url} />
                                        <CopyField label="OIDC redirect URI (give this to your IdP)" value={cfg.oidc_redirect_uri} />
                                    </>
                                )}
                                <p className="text-xs text-fg-3 leading-relaxed pt-1">
                                    Users sign in via “Sign in with SSO” on the login page using your organization slug.
                                    Send the email as the NameID (format emailAddress) or an <code className="num">email</code> attribute,
                                    and the full name as <code className="num">displayName</code>. Admins can always use the password form on the login page.
                                </p>
                            </div>
                        </div>
                    )}
                </section>
            )}

            {isAdmin && <MfaRequirementSection />}
            {isAdmin && <SessionPolicySection />}
            {isAdmin && <McpSection />}
            {isAdmin && <div id="guests" className="scroll-mt-4"><GuestSettingsSection /></div>}
            {isAdmin && <SLAPoliciesSection />}
            {isAdmin && <div id="artifact-types" className="scroll-mt-4"><ArtifactTypesSection /></div>}
            {isAdmin && <div id="report-branding" className="scroll-mt-4"><ReportBrandingSection /></div>}
        </div>
    );
}
