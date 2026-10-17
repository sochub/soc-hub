import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { UserPlus, Send, Ban } from 'lucide-react';
import { api } from '../../api/client';
import { Badge, Button, Input, Label } from '../../components/ui';
import type { CaseGuest } from '../../types';

function errText(err: unknown, fallback: string): string {
    const d = (err as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
    return typeof d === 'string' ? d : fallback;
}

const dateOnly = (iso: string) => iso.slice(0, 10);
const plusDays = (n: number) => new Date(Date.now() + n * 86400000).toISOString().slice(0, 10);

export default function GuestsPanel({ caseId, guestSummary }: { caseId: number; guestSummary: string | null | undefined }) {
    const qc = useQueryClient();
    const key = ['case-guests', caseId];
    const [email, setEmail] = useState('');
    const [permission, setPermission] = useState<'viewer' | 'contributor'>('viewer');
    const [expires, setExpires] = useState(plusDays(30));
    const [summary, setSummary] = useState(guestSummary ?? '');
    const [error, setError] = useState<string | null>(null);

    const { data: guests = [], isLoading } = useQuery({
        queryKey: key,
        queryFn: async () => (await api.get(`/cases/${caseId}/guests`)).data as CaseGuest[],
    });
    const refresh = () => { setError(null); qc.invalidateQueries({ queryKey: key }); };
    const onError = (fallback: string) => (err: unknown) => setError(errText(err, fallback));

    const invite = useMutation({
        mutationFn: async () => api.post(`/cases/${caseId}/guests`, {
            email, permission, expires_at: `${expires}T23:59:59Z`,
        }),
        onSuccess: () => { setEmail(''); refresh(); },
        onError: onError('Could not invite this guest.'),
    });
    const patch = useMutation({
        mutationFn: async ({ id, body }: { id: number; body: Partial<Pick<CaseGuest, 'permission' | 'expires_at'>> }) =>
            api.patch(`/cases/${caseId}/guests/${id}`, body),
        onSuccess: refresh, onError: onError('Could not update this guest.'),
    });
    const revoke = useMutation({
        mutationFn: async (id: number) => api.delete(`/cases/${caseId}/guests/${id}`),
        onSuccess: refresh, onError: onError('Could not revoke access.'),
    });
    const resend = useMutation({
        mutationFn: async (id: number) => api.post(`/cases/${caseId}/guests/${id}/resend`),
        onSuccess: refresh, onError: onError('Could not resend the invitation.'),
    });
    const saveSummary = useMutation({
        mutationFn: async () => api.put(`/cases/${caseId}`, { guest_summary: summary }),
        onSuccess: () => { setError(null); qc.invalidateQueries({ queryKey: ['case', String(caseId)] }); },
        onError: onError('Could not save the guest summary.'),
    });

    return (
        <div className="space-y-6">
            {error && <p role="alert" className="text-sm text-severity-critical">{error}</p>}

            <section className="bg-panel border border-line p-4 space-y-3">
                <h3 className="text-sm font-semibold text-fg">Summary shown to guests</h3>
                <p className="text-xs text-fg-3">Guests never see the internal description. Write what they should know.</p>
                <textarea value={summary} onChange={(e) => setSummary(e.target.value)} maxLength={4000} rows={4}
                    aria-label="Guest summary"
                    className="w-full bg-raised border border-line rounded-md p-2 text-sm text-fg" />
                <Button size="sm" variant="secondary" onClick={() => saveSummary.mutate()} disabled={saveSummary.isPending}>
                    Save summary
                </Button>
            </section>

            <section className="bg-panel border border-line p-4">
                <form className="grid gap-3 sm:grid-cols-[1fr_auto_auto_auto] items-end"
                    onSubmit={(e) => { e.preventDefault(); invite.mutate(); }}>
                    <div><Label htmlFor="guest-email">Email</Label>
                        <Input id="guest-email" type="email" required value={email} onChange={(e) => setEmail(e.target.value)}
                            placeholder="name@department.example" /></div>
                    <div><Label htmlFor="guest-perm">Access</Label>
                        <select id="guest-perm" value={permission}
                            onChange={(e) => setPermission(e.target.value as 'viewer' | 'contributor')}
                            className="h-10 bg-raised border border-line rounded-lg px-2 text-sm text-fg">
                            <option value="viewer">Viewer</option>
                            <option value="contributor">Contributor</option>
                        </select></div>
                    <div><Label htmlFor="guest-exp">Expires</Label>
                        <Input id="guest-exp" type="date" value={expires} min={plusDays(1)} max={plusDays(365)}
                            onChange={(e) => setExpires(e.target.value)} /></div>
                    <Button type="submit" disabled={invite.isPending}><UserPlus size={14} /> Invite</Button>
                </form>
            </section>

            <section className="bg-panel border border-line">
                {isLoading ? <p className="p-4 font-mono text-xs text-muted">$ loading…</p> : guests.length === 0 ? (
                    <p className="p-4 text-sm text-fg-3">No guests yet. Invited guests see only this case's shared updates.</p>
                ) : (
                    <ul className="divide-y divide-line">
                        {guests.map((g) => (
                            <li key={g.id} className="flex flex-wrap items-center gap-3 p-3 text-sm">
                                <span className="font-medium text-fg">{g.email}</span>
                                <Badge variant={g.permission === 'contributor' ? 'brand' : 'default'}>{g.permission}</Badge>
                                {!g.active && <Badge variant="critical">{g.revoked_at ? 'revoked' : 'expired'}</Badge>}
                                <span className="text-xs text-fg-3">
                                    expires {dateOnly(g.expires_at)} · {g.last_seen_at ? `last seen ${dateOnly(g.last_seen_at)}` : 'never signed in'}
                                    {g.has_account && ' · has account'}
                                </span>
                                {g.active && (
                                    <span className="ml-auto flex items-center gap-2">
                                        <select aria-label={`Access for ${g.email}`} value={g.permission}
                                            onChange={(e) => patch.mutate({ id: g.id, body: { permission: e.target.value as CaseGuest['permission'] } })}
                                            className="h-8 bg-raised border border-line rounded-md px-2 text-xs text-fg">
                                            <option value="viewer">Viewer</option>
                                            <option value="contributor">Contributor</option>
                                        </select>
                                        <input type="date" key={g.expires_at} aria-label={`Expiry for ${g.email}`} defaultValue={dateOnly(g.expires_at)}
                                            min={plusDays(1)} max={plusDays(365)}
                                            onChange={(e) => e.target.value && patch.mutate({ id: g.id, body: { expires_at: `${e.target.value}T23:59:59Z` } })}
                                            className="h-8 bg-raised border border-line rounded-md px-2 text-xs text-fg" />
                                        <Button size="sm" variant="ghost" onClick={() => resend.mutate(g.id)}><Send size={13} /> Resend</Button>
                                        <Button size="sm" variant="danger" onClick={() => revoke.mutate(g.id)}><Ban size={13} /> Revoke</Button>
                                    </span>
                                )}
                            </li>
                        ))}
                    </ul>
                )}
            </section>
        </div>
    );
}
