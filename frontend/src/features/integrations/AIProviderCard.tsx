import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Bot, Copy } from 'lucide-react';
import { isAxiosError } from 'axios';
import { api } from '../../api/client';

type Provider = 'ollama' | 'openai' | 'openai_compatible' | 'anthropic' | 'gemini' | 'vertex' | 'bedrock';
type SecretField = 'api_key' | 'organization' | 'access_key_id' | 'secret_access_key' | 'session_token' | 'service_account_json';
type FormValue = string | boolean | null;

interface AIConfig {
    source: 'tenant' | 'deployment';
    override_allowed: boolean;
    deployment: { provider: string; model: string };
    deployment_principal_arn: string | null;
    provider: Provider; model: string; enabled: boolean;
    api_base: string | null; region: string | null; project: string | null; location: string | null;
    auth_mode: 'role' | 'keys' | null; role_arn: string | null; external_id: string | null;
    secrets_set: Record<SecretField, boolean>;
}

const LABEL: Record<Provider, string> = {
    ollama: 'Ollama (local)', openai: 'OpenAI', openai_compatible: 'OpenAI-compatible (vLLM, LM Studio, OpenRouter…)',
    anthropic: 'Anthropic', gemini: 'Google Gemini (AI Studio)', vertex: 'Google Vertex AI', bedrock: 'AWS Bedrock',
};
const FIELDS: Record<Provider, { plain: string[]; secrets: SecretField[] }> = {
    ollama: { plain: ['api_base'], secrets: [] },
    openai: { plain: [], secrets: ['api_key', 'organization'] },
    openai_compatible: { plain: ['api_base'], secrets: ['api_key'] },
    anthropic: { plain: [], secrets: ['api_key'] },
    gemini: { plain: [], secrets: ['api_key'] },
    vertex: { plain: ['project', 'location'], secrets: ['service_account_json'] },
    bedrock: { plain: ['region'], secrets: [] },
};
const FIELD_LABEL: Record<string, string> = {
    api_base: 'Base URL', project: 'GCP project', location: 'Location (e.g. us-central1)', region: 'AWS region',
    api_key: 'API key', organization: 'Organization (optional)', access_key_id: 'Access key ID',
    secret_access_key: 'Secret access key', session_token: 'Session token (optional)',
    service_account_json: 'Service account JSON',
};

const errText = (e: unknown, fb: string): string => {
    const d: unknown = isAxiosError(e) ? e.response?.data?.detail : undefined;
    if (typeof d === 'string') return d;
    // pydantic 422: detail is a list of {loc, msg, ...}
    if (Array.isArray(d) && typeof d[0]?.msg === 'string') return d[0].msg;
    return fb;
};

// GET /ai/config has no explicit "row exists" flag; a saved-but-disabled row reports source=deployment.
const hasSavedRow = (d: AIConfig) =>
    d.source === 'tenant' || !!d.external_id || Object.values(d.secrets_set).some(Boolean)
    || [d.api_base, d.region, d.project, d.location, d.role_arn, d.auth_mode].some((v) => v != null)
    || d.provider !== d.deployment.provider || d.model !== d.deployment.model;

export default function AIProviderCard() {
    const qc = useQueryClient();
    const { data } = useQuery({ queryKey: ['ai-config'], queryFn: async () => (await api.get('/ai/config')).data as AIConfig });
    const [draft, setDraft] = useState<Record<string, FormValue> | null>(null);
    const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);

    const seed = (d: AIConfig): Record<string, FormValue> => ({
        provider: d.provider, model: d.model, enabled: true,
        api_base: d.api_base, region: d.region, project: d.project, location: d.location,
        auth_mode: d.auth_mode ?? 'role', role_arn: d.role_arn,
    });
    const form: Record<string, FormValue> = draft ?? (data ? seed(data) : {});
    const set = (k: string, v: FormValue) => { setDraft({ ...form, [k]: v }); setMsg(null); };
    const provider = (form.provider as Provider) || 'ollama';
    const spec = FIELDS[provider];
    const bedrockSecrets: SecretField[] = form.auth_mode === 'keys' ? ['access_key_id', 'secret_access_key', 'session_token'] : [];
    const secretFields = provider === 'bedrock' ? bedrockSecrets : spec.secrets;

    const body = () => {
        const b: Record<string, unknown> = { provider, model: form.model, enabled: form.enabled ?? true };
        for (const f of spec.plain) b[f] = form[f] || null;
        if (provider === 'bedrock') { b.auth_mode = form.auth_mode; b.role_arn = form.auth_mode === 'keys' ? null : form.role_arn || null; }
        // Blank secrets are omitted so the backend keeps the stored value.
        for (const f of secretFields) if (form[`secret_${f}`]) b[f] = form[`secret_${f}`];
        return b;
    };
    const save = useMutation({
        mutationFn: async () => (await api.put('/ai/config', body())).data as AIConfig,
        onSuccess: (r) => {
            qc.setQueryData(['ai-config'], r); setDraft(null); qc.invalidateQueries({ queryKey: ['ai-info'] });
            setMsg({ ok: true, text: r.enabled ? 'Saved' : 'Saved (disabled until the role ARN is set)' });
        },
        onError: (e) => {
            if (isAxiosError(e) && e.response?.status === 409) qc.invalidateQueries({ queryKey: ['ai-config'] });
            setMsg({ ok: false, text: errText(e, 'Save failed') });
        },
    });
    const test = useMutation({
        // No draft: test the saved row (backend reports when there is none). Draft: test the unsaved form.
        mutationFn: async () => (await api.post('/ai/config/test', draft ? body() : undefined)).data as { ok: boolean; message: string },
        onSuccess: (r) => setMsg({ ok: r.ok, text: r.message }),
        onError: (e) => setMsg({ ok: false, text: errText(e, 'Test failed') }),
    });
    const reset = useMutation({
        mutationFn: async () => api.delete('/ai/config'),
        onSuccess: () => {
            setDraft(null); qc.invalidateQueries({ queryKey: ['ai-config'] }); qc.invalidateQueries({ queryKey: ['ai-info'] });
            setMsg({ ok: true, text: 'Reverted to the deployment default' });
        },
        onError: (e) => setMsg({ ok: false, text: errText(e, 'Reset failed') }),
    });

    if (!data) return null;
    const saved = hasSavedRow(data);
    // Stored secrets only carry over when the provider is unchanged.
    const secretIsSet = (f: SecretField) => saved && provider === data.provider && data.secrets_set[f];
    const input = 'w-full border border-zinc-300 px-2 py-1.5 text-sm';
    const readOnly = !data.override_allowed;
    const status = data.source === 'tenant' ? `this tenant · ${data.provider}`
        : saved && !readOnly ? `saved · disabled · deployment default ${data.deployment.provider}`
        : `deployment default · ${data.deployment.provider}`;
    const trust = data.external_id ? JSON.stringify({
        Version: '2012-10-17',
        Statement: [{
            Effect: 'Allow', Principal: { AWS: data.deployment_principal_arn ?? '<SOC Hub server role ARN>' },
            Action: 'sts:AssumeRole', Condition: { StringEquals: { 'sts:ExternalId': data.external_id } },
        }],
    }, null, 2) : null;
    const copyTrust = async () => {
        if (!trust) return;
        try { await navigator.clipboard.writeText(trust); setMsg({ ok: true, text: 'Trust policy copied' }); }
        catch { /* clipboard unavailable (non-secure context) */ }
    };

    return (
        <div className="bg-white border border-zinc-200 p-5 space-y-3">
            <div className="flex items-center gap-2">
                <Bot size={16} className="text-accent-600" />
                <h2 className="font-semibold text-zinc-800">AI provider</h2>
                <span className="ml-auto label-mono">{status}</span>
            </div>
            {readOnly ? (
                <div className="space-y-1">
                    <p className="text-sm text-zinc-600">Managed by the deployment: <span className="font-mono">{data.deployment.provider} / {data.deployment.model}</span></p>
                    <p className="text-xs text-zinc-500">Per-tenant AI provider overrides are turned off on this deployment.</p>
                </div>
            ) : (
                <>
                    <p className="text-xs text-zinc-500">Case data sent to the AI goes to this provider. Leave unset to use the deployment default ({data.deployment.provider} / {data.deployment.model}).</p>
                    <label className="block"><span className="label-mono">provider</span>
                        <select className={input} value={provider} onChange={(e) => { setDraft(e.target.value === data.provider ? seed(data) : { provider: e.target.value, model: '', enabled: true, auth_mode: 'role' }); setMsg(null); }}>
                            {(Object.keys(LABEL) as Provider[]).map((p) => <option key={p} value={p}>{LABEL[p]}</option>)}
                        </select></label>
                    <label className="block"><span className="label-mono">model</span>
                        <input className={`${input} font-mono`} value={(form.model as string) ?? ''} onChange={(e) => set('model', e.target.value)}
                            placeholder={provider === 'bedrock' ? 'anthropic.claude-3-5-sonnet-20241022-v2:0' : 'model id'} /></label>
                    {spec.plain.map((f) => (
                        <label key={f} className="block"><span className="label-mono">{FIELD_LABEL[f]}</span>
                            <input className={`${input} font-mono`} value={(form[f] as string) ?? ''} onChange={(e) => set(f, e.target.value)} /></label>
                    ))}
                    {provider === 'bedrock' && (
                        <fieldset className="space-y-2">
                            <legend className="label-mono">AWS credentials</legend>
                            <label className="flex items-center gap-2 text-sm"><input type="radio" checked={form.auth_mode !== 'keys'} onChange={() => set('auth_mode', 'role')} />Assume a role in your AWS account (recommended)</label>
                            <label className="flex items-center gap-2 text-sm"><input type="radio" checked={form.auth_mode === 'keys'} onChange={() => set('auth_mode', 'keys')} />Access keys</label>
                            {form.auth_mode !== 'keys' && (
                                <>
                                    <label className="block"><span className="label-mono">role ARN</span>
                                        <input className={`${input} font-mono`} value={(form.role_arn as string) ?? ''} onChange={(e) => set('role_arn', e.target.value)} placeholder="arn:aws:iam::123456789012:role/sochub-bedrock" /></label>
                                    {trust ? (
                                        <div>
                                            <div className="flex items-center justify-between">
                                                <span className="label-mono">trust policy for that role · external id <span className="num">{data.external_id}</span></span>
                                                <button type="button" className="text-xs text-accent-600 flex items-center gap-1" onClick={copyTrust}><Copy size={12} />Copy</button>
                                            </div>
                                            <pre className="text-[11px] font-mono bg-zinc-50 border border-zinc-200 p-2 overflow-x-auto">{trust}</pre>
                                            {!data.deployment_principal_arn && <p className="text-xs text-zinc-500 mt-1">Replace the placeholder principal with the ARN of the role the SOC Hub server runs as.</p>}
                                        </div>
                                    ) : <p className="text-xs text-zinc-500">Save with role mode to generate the External ID.</p>}
                                </>
                            )}
                        </fieldset>
                    )}
                    {secretFields.map((f) => (
                        <label key={f} className="block"><span className="label-mono">{FIELD_LABEL[f]}{secretIsSet(f) && <span className="ml-2 text-emerald-700">· set</span>}</span>
                            {f === 'service_account_json'
                                ? <textarea rows={4} className={`${input} font-mono text-xs`} value={(form[`secret_${f}`] as string) ?? ''} onChange={(e) => set(`secret_${f}`, e.target.value)} placeholder={secretIsSet(f) ? '•••• (saved — leave blank to keep)' : '{ "type": "service_account", … }'} />
                                : <input type="password" autoComplete="off" className={`${input} font-mono`} value={(form[`secret_${f}`] as string) ?? ''} onChange={(e) => set(`secret_${f}`, e.target.value)} placeholder={secretIsSet(f) ? '•••• (saved — leave blank to keep)' : ''} />}
                        </label>
                    ))}
                    <div className="flex items-center gap-2 flex-wrap">
                        <button onClick={() => save.mutate()} disabled={save.isPending || !form.model} className="h-8 px-3 bg-accent-600 text-white text-sm disabled:opacity-50">Save</button>
                        <button onClick={() => test.mutate()} disabled={test.isPending || (!!draft && !form.model)} className="h-8 px-3 border border-zinc-300 text-sm disabled:opacity-50">{test.isPending ? 'Testing…' : 'Test'}</button>
                        {saved && <button onClick={() => { if (confirm('Remove this tenant\'s AI provider and use the deployment default?')) reset.mutate(); }} disabled={reset.isPending} className="h-8 px-3 border border-zinc-300 text-sm disabled:opacity-50">Reset to deployment default</button>}
                        {msg && <span role="status" className={msg.ok ? 'text-xs text-emerald-700' : 'text-xs text-red-700'}>{msg.text}</span>}
                    </div>
                </>
            )}
        </div>
    );
}
