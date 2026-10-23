import { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { FileText, Loader2, Trash2, Upload } from 'lucide-react';
import { api } from '../../api/client';
import { btnPrimary, btnSecondary } from '../../components/layout/Modal';

interface Branding { org_name: string | null; accent: string | null; disclaimer: string | null; has_logo: boolean }

const URL = '/tenants/current/report-branding';
const DEFAULT_ACCENT = '#4f46e5';
const input = 'mt-1 w-full h-8 px-2 text-sm border border-line-strong bg-panel focus:outline-hidden focus:border-accent';

function errorText(e: unknown, fallback: string): string {
    const d = (e as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
    if (Array.isArray(d)) return d.map((x: { msg?: string }) => x?.msg ?? String(x)).join('; ');
    return d ? String(d) : fallback;
}

/** Organization name, accent color, disclaimer and logo used on exported incident reports. */
export default function ReportBrandingSection() {
    const qc = useQueryClient();
    const [form, setForm] = useState({ org_name: '', accent: DEFAULT_ACCENT, disclaimer: '' });
    const [error, setError] = useState<string | null>(null);
    const [saved, setSaved] = useState(false);
    const [logoUrl, setLogoUrl] = useState<string | null>(null);

    const { data } = useQuery({ queryKey: ['report-branding'], queryFn: async () => (await api.get(URL)).data as Branding });
    // Seed the form once the saved values arrive (adjust state during render, no effect).
    const [seeded, setSeeded] = useState<Branding | null>(null);
    if (data && data !== seeded) {
        setSeeded(data);
        setForm({ org_name: data.org_name ?? '', accent: data.accent ?? DEFAULT_ACCENT, disclaimer: data.disclaimer ?? '' });
    }

    // The logo endpoint needs the auth header, so load it as a blob instead of a plain <img src>.
    useEffect(() => {
        if (!data?.has_logo) return;
        let url: string | null = null;
        api.get(`${URL}/logo`, { responseType: 'blob' })
            .then((r) => { url = window.URL.createObjectURL(r.data); setLogoUrl(url); })
            .catch(() => setLogoUrl(null));
        return () => { if (url) window.URL.revokeObjectURL(url); setLogoUrl(null); };
    }, [data?.has_logo, seeded]);

    const done = (d: Branding) => { setError(null); qc.setQueryData(['report-branding'], d); };
    const save = useMutation({
        mutationFn: async () => (await api.put(URL, { org_name: form.org_name || null, accent: form.accent, disclaimer: form.disclaimer || null })).data as Branding,
        onSuccess: (d) => { done(d); setSaved(true); setTimeout(() => setSaved(false), 2000); },
        onError: (e) => setError(errorText(e, 'Could not save branding.')),
    });
    const upload = useMutation({
        mutationFn: async (f: File) => {
            const body = new FormData();
            body.append('file', f);
            return (await api.put(`${URL}/logo`, body)).data as Branding;
        },
        onSuccess: done, onError: (e) => setError(errorText(e, 'Could not upload the logo.')),
    });
    const removeLogo = useMutation({
        mutationFn: async () => (await api.delete(`${URL}/logo`)).data as Branding,
        onSuccess: done, onError: (e) => setError(errorText(e, 'Could not remove the logo.')),
    });

    return (
        <section className="bg-panel border border-line">
            <div className="flex items-center justify-between px-4 h-11 border-b border-line">
                <h2 className="flex items-center gap-2 text-sm font-semibold text-fg">
                    <FileText size={15} className="text-accent" /> Report branding
                </h2>
            </div>
            <div className="p-4 space-y-4">
                <p className="text-xs text-fg-3">Shown on the cover, page headers and disclaimer of exported incident reports.</p>
                <div className="grid gap-4 sm:grid-cols-[1fr_auto]">
                    <label className="block text-xs text-fg-2">Organization name
                        <input className={input} maxLength={120} value={form.org_name} placeholder="Defaults to the tenant name"
                            onChange={(e) => setForm({ ...form, org_name: e.target.value })} />
                    </label>
                    <label className="block text-xs text-fg-2">Accent color
                        <span className="mt-1 flex items-center gap-2">
                            <input type="color" value={form.accent} onChange={(e) => setForm({ ...form, accent: e.target.value })}
                                className="h-8 w-10 border border-line-strong bg-panel" aria-label="Accent color" />
                            <span className="font-mono text-xs text-fg-3">{form.accent}</span>
                        </span>
                    </label>
                </div>
                <label className="block text-xs text-fg-2">Disclaimer
                    <textarea className={`${input} h-24 py-1.5 resize-y`} maxLength={4000} value={form.disclaimer}
                        placeholder="Leave empty to use the default confidentiality disclaimer"
                        onChange={(e) => setForm({ ...form, disclaimer: e.target.value })} />
                </label>
                <div>
                    <span className="block text-xs text-fg-2">Logo <span className="text-muted">(PNG or JPEG, up to 256 KB)</span></span>
                    <div className="mt-1 flex items-center gap-3">
                        <div className="h-12 w-32 border border-line flex items-center justify-center bg-white">
                            {logoUrl ? <img src={logoUrl} alt="Report logo" className="max-h-10 max-w-28 object-contain" />
                                : <span className="text-[10px] text-zinc-500">No logo</span>}
                        </div>
                        <label className={`${btnSecondary} relative cursor-pointer`}>
                            {upload.isPending ? <Loader2 size={14} className="animate-spin" /> : <Upload size={14} />} Upload
                            <input type="file" accept="image/png,image/jpeg" className="sr-only"
                                onChange={(e) => { const f = e.target.files?.[0]; if (f) upload.mutate(f); e.target.value = ''; }} />
                        </label>
                        {data?.has_logo && (
                            <button type="button" className={btnSecondary} disabled={removeLogo.isPending} onClick={() => removeLogo.mutate()}>
                                <Trash2 size={14} /> Remove
                            </button>
                        )}
                    </div>
                </div>
                {error && <p role="alert" className="text-xs text-red-300">{error}</p>}
                <div className="flex items-center gap-3">
                    <button type="button" className={btnPrimary} disabled={save.isPending} onClick={() => save.mutate()}>
                        {save.isPending && <Loader2 size={14} className="animate-spin" />} Save branding
                    </button>
                    {saved && <span className="text-xs text-fg-3">Saved</span>}
                </div>
            </div>
        </section>
    );
}
