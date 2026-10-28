import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ShieldCheck } from 'lucide-react';
import { isAxiosError } from 'axios';
import { api } from '../../api/client';

type Entry = { kind: string; pattern: string; note?: string | null };
type Policy = { enabled: boolean; daily_cap: number; max_targets_per_case: number; protected: Entry[] };

const errText = (e: unknown, fb: string): string => {
    const d: unknown = isAxiosError(e) ? e.response?.data?.detail : undefined;
    if (typeof d === 'string') return d;
    if (Array.isArray(d) && typeof d[0]?.msg === 'string') return d[0].msg;
    return fb;
};
const toLines = (p: Entry[]) => p.map((e) => `${e.kind} ${e.pattern}${e.note ? `  # ${e.note}` : ''}`).join('\n');
const fromLines = (s: string): Entry[] => s.split('\n').map((l) => l.trim()).filter(Boolean).map((l) => {
    const [main, ...note] = l.split('#');
    const [kind, ...rest] = main.trim().split(/\s+/);
    return { kind: kind?.toLowerCase() ?? '', pattern: rest.join(' '), note: note.join('#').trim() || null };
});
const input = 'w-full border border-line-strong px-2 py-1.5 text-sm';

/** Admin card: guardrails every containment_guard step checks before a workflow contains anything. */
export default function ContainmentCard({ tenantId, isAdmin }: { tenantId: number | null; isAdmin: boolean }) {
    const key = ['containment-policy', tenantId];
    const qc = useQueryClient();
    const { data } = useQuery({ queryKey: key, queryFn: async () => (await api.get('/containment/policy')).data as Policy });
    const [draft, setDraft] = useState<{ enabled: boolean; daily_cap: string; max_targets_per_case: string; protected: string } | null>(null);
    const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
    const save = useMutation({
        mutationFn: async () => (await api.put('/containment/policy', {
            enabled: form.enabled, daily_cap: Number(form.daily_cap), max_targets_per_case: Number(form.max_targets_per_case),
            protected: fromLines(form.protected),
        })).data as Policy,
        onSuccess: (r) => { qc.setQueryData(key, r); setDraft(null); setMsg({ ok: true, text: 'Saved' }); },
        onError: (e) => setMsg({ ok: false, text: errText(e, 'Save failed') }),
    });
    if (!data) return null;
    const form = draft ?? { enabled: data.enabled, daily_cap: String(data.daily_cap), max_targets_per_case: String(data.max_targets_per_case),
        protected: toLines(data.protected) };
    const set = (patch: Partial<typeof form>) => { setDraft({ ...form, ...patch }); setMsg(null); };
    return (
        <div className="bg-panel border border-line p-5 space-y-3">
            <div className="flex items-center gap-2">
                <ShieldCheck size={16} className="text-accent" />
                <h2 className="font-semibold text-fg">Containment guardrails</h2>
                <span className="ml-auto label-mono">{data.enabled ? `${data.daily_cap}/day · ${data.max_targets_per_case}/case` : 'off'}</span>
            </div>
            <p className="text-xs text-fg-3">
                Checked by every <strong>Containment guard</strong> step (all built-in response workflows have one) before it isolates a host,
                suspends a user or blocks an IP. A refused action stops the run and adds a note to the case.
            </p>
            <fieldset disabled={!isAdmin} className="grid grid-cols-1 sm:grid-cols-3 gap-3">
                <label className="text-xs text-fg-2 space-y-1 flex items-center gap-2 pt-5">
                    <input type="checkbox" checked={form.enabled} onChange={(e) => set({ enabled: e.target.checked })} /> Enforce guardrails
                </label>
                <label className="text-xs text-fg-2 space-y-1">Actions per 24 hours (whole organization)
                    <input type="number" min={0} value={form.daily_cap} onChange={(e) => set({ daily_cap: e.target.value })} className={input} />
                </label>
                <label className="text-xs text-fg-2 space-y-1">Contained targets per case
                    <input type="number" min={0} value={form.max_targets_per_case} onChange={(e) => set({ max_targets_per_case: e.target.value })} className={input} />
                </label>
                <label className="text-xs text-fg-2 space-y-1 sm:col-span-3">Never contain automatically (one per line: <code>host|user|ip|any pattern  # note</code>)
                    <textarea rows={5} value={form.protected} onChange={(e) => set({ protected: e.target.value })}
                        placeholder={'user ceo@acme.com  # VIP\nhost dc-*  # domain controllers\nip 10.0.0.0/24  # core network\nany *-prod-db'}
                        className={`${input} font-mono text-xs`} />
                    <span className="block text-muted">Host and user patterns are case-insensitive, <code>*</code> is a wildcard; ip takes an address or CIDR range.</span>
                </label>
            </fieldset>
            {msg && <p role={msg.ok ? 'status' : 'alert'} className={msg.ok ? 'text-xs text-emerald-300' : 'text-xs text-red-300'}>{msg.text}</p>}
            {isAdmin && <button onClick={() => save.mutate()} disabled={!draft || save.isPending} className="px-3 py-1.5 text-sm bg-accent text-on-accent hover:bg-accent-hover disabled:opacity-50">Save</button>}
        </div>
    );
}
