import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api } from '../../api/client';
import { ArrowDown, ArrowUp, Plus, Trash2 } from 'lucide-react';
import PageContainer from '../../components/layout/PageContainer';
import { Button, Input, Label } from '../../components/ui';
import type { EscalationTier, User } from '../../types';
import CheckList from './CheckList';
import { errMsg, useGroups, usePolicy, useSavePolicy } from './api';

const blank = (): EscalationTier => ({
    id: null, name: '', accept_timeout_minutes: null, notify_email: false, notify_slack: false,
    slack_channel: null, group_ids: [],
});

function PolicyForm() {
    const { data: policy, isLoading } = usePolicy();
    const { data: groups = [] } = useGroups();
    const save = useSavePolicy();
    const [draft, setTiers0] = useState<EscalationTier[] | null>(null);
    const tiers = draft ?? policy?.tiers ?? [];
    const setTiers = (fn: (ts: EscalationTier[]) => EscalationTier[]) => setTiers0(fn(tiers));
    const [error, setError] = useState<string | null>(null);
    const [saved, setSaved] = useState(false);

    const edit = (i: number, patch: Partial<EscalationTier>) => {
        setSaved(false);
        setTiers((ts) => ts.map((t, j) => (j === i ? { ...t, ...patch } : t)));
    };
    const move = (i: number, d: -1 | 1) => {
        setSaved(false);
        setTiers((ts) => {
            const n = [...ts]; [n[i], n[i + d]] = [n[i + d], n[i]]; return n;
        });
    };

    const submit = () => {
        setError(null); setSaved(false);
        const body = tiers.map((t) => ({
            id: t.id, name: t.name.trim(), accept_timeout_minutes: t.accept_timeout_minutes,
            notify_email: t.notify_email, notify_slack: t.notify_slack,
            slack_channel: t.slack_channel?.trim() || null, group_ids: t.group_ids,
        }));
        save.mutate(body, {
            onSuccess: () => { setTiers0(null); setSaved(true); },
            onError: (e) => setError(errMsg(e, 'Could not save the escalation policy.')),
        });
    };

    return (
        <PageContainer>
            <div className="flex items-center justify-between">
                <div>
                    <h1 className="text-2xl font-bold text-fg">Escalation policy</h1>
                    <p className="text-sm text-fg-3 mt-1">Tiers are tried in order. In-app notifications are always sent.</p>
                </div>
                <div className="flex items-center gap-2">
                    <Button variant="secondary" onClick={() => { setSaved(false); setTiers((t) => [...t, blank()]); }}><Plus size={16} />Add tier</Button>
                    <Button onClick={submit} disabled={save.isPending || isLoading}>{save.isPending ? 'Saving…' : 'Save policy'}</Button>
                </div>
            </div>
            {error && <p role="alert" className="text-sm text-severity-critical">{error}</p>}
            {saved && <p role="status" className="text-sm text-emerald-300">Policy saved.</p>}
            {isLoading ? <p className="text-sm text-muted">Loading…</p> : tiers.length === 0 ? (
                <p className="text-sm text-fg-3">No tiers yet. Add a tier to enable escalation.</p>
            ) : (
                <ol className="space-y-3">
                    {tiers.map((t, i) => (
                        <li key={t.id ?? `new-${i}`} className="bg-panel border border-line rounded-xl p-4 space-y-3">
                            <div className="flex items-center gap-2">
                                <span className="text-xs font-semibold text-fg-3 w-14 shrink-0">Tier {i + 1}</span>
                                <Input aria-label={`Tier ${i + 1} name`} placeholder="Name (e.g. Tier 2 responders)" value={t.name} maxLength={100}
                                    onChange={(e) => edit(i, { name: e.target.value })} />
                                <Button variant="ghost" size="sm" aria-label="Move up" disabled={i === 0} onClick={() => move(i, -1)}><ArrowUp size={14} /></Button>
                                <Button variant="ghost" size="sm" aria-label="Move down" disabled={i === tiers.length - 1} onClick={() => move(i, 1)}><ArrowDown size={14} /></Button>
                                <Button variant="ghost" size="sm" aria-label="Remove tier" onClick={() => { setSaved(false); setTiers((ts) => ts.filter((_, j) => j !== i)); }}><Trash2 size={14} /></Button>
                            </div>
                            <div className="grid gap-4 md:grid-cols-2">
                                <div className="space-y-1.5">
                                    <Label>Groups</Label>
                                    <CheckList options={groups.map((g) => ({ id: g.id, label: g.name }))} value={t.group_ids}
                                        onChange={(v) => edit(i, { group_ids: v })} empty="Create a group first (Users › Groups)." />
                                </div>
                                <div className="space-y-3">
                                    <div className="space-y-1.5">
                                        <Label htmlFor={`to-${i}`}>Auto-climb after (minutes)</Label>
                                        <Input id={`to-${i}`} type="number" min={1} max={10080} placeholder="Never"
                                            value={t.accept_timeout_minutes ?? ''}
                                            onChange={(e) => edit(i, { accept_timeout_minutes: e.target.value === '' ? null : Number(e.target.value) })} />
                                    </div>
                                    <div className="flex items-center gap-4 text-sm text-fg">
                                        <label className="flex items-center gap-2"><input type="checkbox" className="accent-accent" checked={t.notify_email}
                                            onChange={(e) => edit(i, { notify_email: e.target.checked })} />Email</label>
                                        <label className="flex items-center gap-2"><input type="checkbox" className="accent-accent" checked={t.notify_slack}
                                            onChange={(e) => edit(i, { notify_slack: e.target.checked })} />Slack</label>
                                    </div>
                                    <div className="space-y-1.5">
                                        <Label htmlFor={`ch-${i}`}>Slack channel (optional)</Label>
                                        <Input id={`ch-${i}`} placeholder="#soc-tier2" value={t.slack_channel ?? ''}
                                            disabled={!t.notify_slack} title={t.notify_slack ? undefined : 'Turn on Slack to post to a channel'}
                                            onChange={(e) => edit(i, { slack_channel: e.target.value || null })} />
                                    </div>
                                </div>
                            </div>
                        </li>
                    ))}
                </ol>
            )}
        </PageContainer>
    );
}

// Keyed on the active organization so a switch drops any unsaved draft.
export default function EscalationPolicy() {
    const { data: me } = useQuery({
        queryKey: ['currentUser'],
        queryFn: async () => (await api.get('/users/me')).data as User,
        staleTime: 300_000,
    });
    return <PolicyForm key={me?.active_tenant_id ?? 'none'} />;
}
