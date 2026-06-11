import { useEffect, useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { KeyRound, Copy, Check, Save, Loader2 } from 'lucide-react';
import { api } from '../../api/client';
import { cn } from '../../lib/utils';
import type { SSOConfig, User } from '../../types';

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
                <code className="flex-1 num text-[11px] text-zinc-700 bg-zinc-50 border border-zinc-200 px-2 py-1.5 truncate">{value}</code>
                <button onClick={copy} className="p-1.5 border border-zinc-200 text-zinc-500 hover:text-zinc-900 hover:bg-zinc-50" aria-label={`Copy ${label}`}>
                    {copied ? <Check size={13} className="text-emerald-600" /> : <Copy size={13} />}
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
        enabled: false, idp_entity_id: '', idp_sso_url: '', idp_x509_cert: '',
        auto_provision: false, default_role: 'viewer' as 'analyst' | 'viewer',
    });
    const [savedFlash, setSavedFlash] = useState(false);

    useEffect(() => {
        if (cfg) {
            setForm({
                enabled: cfg.enabled,
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

    const field = (label: string, key: 'idp_entity_id' | 'idp_sso_url', placeholder: string) => (
        <div>
            <label className="label-mono block mb-1">{label}</label>
            <input value={form[key]} onChange={e => setForm(f => ({ ...f, [key]: e.target.value }))}
                placeholder={placeholder}
                className="w-full bg-white border border-zinc-300 px-2.5 py-2 text-sm num focus:outline-none focus:ring-1 focus:ring-accent-500" />
        </div>
    );

    return (
        <div className="p-4 sm:p-6 max-w-[1000px] mx-auto space-y-6">
            <div>
                <h1 className="text-xl font-semibold text-zinc-900 tracking-tight">Settings</h1>
                <p className="label-mono mt-1">tenant configuration</p>
            </div>

            {!isAdmin ? (
                <div className="bg-white border border-zinc-200 p-8 text-center text-sm text-zinc-500">
                    Tenant settings are managed by your tenant admin.
                </div>
            ) : (
                <section className="bg-white border border-zinc-200">
                    <div className="flex items-center justify-between px-4 h-11 border-b border-zinc-200">
                        <h2 className="flex items-center gap-2 text-sm font-semibold text-zinc-900">
                            <KeyRound size={15} className="text-accent-600" /> Single Sign-On (SAML)
                        </h2>
                        <label className="flex items-center gap-2 text-xs text-zinc-600 cursor-pointer">
                            <input type="checkbox" checked={form.enabled}
                                onChange={e => setForm(f => ({ ...f, enabled: e.target.checked }))}
                                className="border-zinc-300 text-accent-600 focus:ring-accent-500" />
                            enabled
                        </label>
                    </div>

                    {isLoading ? (
                        <p className="p-4 font-mono text-xs text-zinc-400">$ loading…</p>
                    ) : (
                        <div className="p-4 grid grid-cols-1 lg:grid-cols-2 gap-6">
                            {/* IdP side */}
                            <div className="space-y-4">
                                <p className="label-mono">// identity provider (from your idp)</p>
                                {field('idp entity id', 'idp_entity_id', 'https://idp.example.com/entityid')}
                                {field('idp sso url', 'idp_sso_url', 'https://idp.example.com/sso/saml')}
                                <div>
                                    <label className="label-mono block mb-1">idp x509 certificate</label>
                                    <textarea value={form.idp_x509_cert}
                                        onChange={e => setForm(f => ({ ...f, idp_x509_cert: e.target.value }))}
                                        rows={6} placeholder="-----BEGIN CERTIFICATE-----"
                                        className="w-full bg-white border border-zinc-300 px-2.5 py-2 text-[11px] num focus:outline-none focus:ring-1 focus:ring-accent-500" />
                                </div>
                                <div className="flex items-center gap-4 flex-wrap">
                                    <label className="flex items-center gap-2 text-xs text-zinc-600 cursor-pointer">
                                        <input type="checkbox" checked={form.auto_provision}
                                            onChange={e => setForm(f => ({ ...f, auto_provision: e.target.checked }))}
                                            className="border-zinc-300 text-accent-600 focus:ring-accent-500" />
                                        auto-provision new users
                                    </label>
                                    <label className="flex items-center gap-2 text-xs text-zinc-600">
                                        default role
                                        <select value={form.default_role}
                                            onChange={e => setForm(f => ({ ...f, default_role: e.target.value as 'analyst' | 'viewer' }))}
                                            disabled={!form.auto_provision}
                                            className="border border-zinc-300 px-1.5 py-1 text-xs font-mono disabled:opacity-50">
                                            <option value="viewer">viewer</option>
                                            <option value="analyst">analyst</option>
                                        </select>
                                    </label>
                                </div>
                                <div className="flex items-center gap-3">
                                    <button onClick={() => save.mutate()} disabled={save.isPending}
                                        className={cn('inline-flex items-center gap-1.5 h-9 px-4 text-sm font-medium text-white transition-colors disabled:opacity-50',
                                            savedFlash ? 'bg-emerald-600' : 'bg-accent-600 hover:bg-accent-700')}>
                                        {save.isPending ? <Loader2 size={14} className="animate-spin" /> : savedFlash ? <Check size={14} /> : <Save size={14} />}
                                        {savedFlash ? 'Saved' : 'Save SSO settings'}
                                    </button>
                                    {save.isError && (
                                        <span className="text-xs text-severity-critical">
                                            {(save.error as any)?.response?.data?.detail ?? 'Save failed'}
                                        </span>
                                    )}
                                </div>
                            </div>

                            {/* SP side */}
                            <div className="space-y-3 lg:border-l lg:border-zinc-200 lg:pl-6">
                                <p className="label-mono">// service provider (give these to your idp)</p>
                                {cfg && (
                                    <>
                                        <CopyField label="sp entity id / audience" value={cfg.sp_entity_id} />
                                        <CopyField label="acs url (single sign on url)" value={cfg.sp_acs_url} />
                                        <CopyField label="sp metadata url" value={cfg.sp_metadata_url} />
                                        <CopyField label="sso login url (for your users)" value={cfg.sp_login_url} />
                                    </>
                                )}
                                <p className="text-[11px] text-zinc-400 leading-relaxed pt-1">
                                    Users sign in via “Sign in with SSO” on the login page using your tenant slug.
                                    NameID must be the user's email address. Password login remains available.
                                </p>
                            </div>
                        </div>
                    )}
                </section>
            )}
        </div>
    );
}
