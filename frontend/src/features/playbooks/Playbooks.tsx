import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { BookText, Store, Plus, Download, Check, Trash2, ListChecks, FileEdit, Send } from 'lucide-react';
import { api } from '../../api/client';
import { cn } from '../../lib/utils';
import type { PlaybookSummary, User } from '../../types';
import TemplateModal from './TemplateModal';
import PageContainer from '../../components/layout/PageContainer';

type Tab = 'mine' | 'drafts' | 'marketplace';

export default function Playbooks() {
    const qc = useQueryClient();
    const [tab, setTab] = useState<Tab>('mine');
    const [selected, setSelected] = useState<Set<number>>(new Set());
    const [editId, setEditId] = useState<number | 'new' | null>(null);
    const [newMarketplace, setNewMarketplace] = useState(false);

    const { data: me } = useQuery({
        queryKey: ['currentUser'],
        queryFn: async () => (await api.get('/users/me')).data as User,
        staleTime: 5 * 60 * 1000,
    });
    const canManage = me?.role === 'admin' || me?.is_super_admin;
    const isSuper = !!me?.is_super_admin;

    const { data: mine = [], isLoading: mineLoading } = useQuery({
        queryKey: ['playbooks', 'mine', 'with-drafts', !!canManage],
        queryFn: async () => (await api.get('/playbooks/', { params: { include_drafts: canManage ? true : undefined } })).data as PlaybookSummary[],
    });
    const publishMutation = useMutation({
        mutationFn: async (id: number) => api.post(`/playbooks/${id}/publish`),
        onSuccess: () => qc.invalidateQueries({ queryKey: ['playbooks'] }),
    });
    const { data: market = [], isLoading: marketLoading } = useQuery({
        queryKey: ['playbooks', 'marketplace'],
        queryFn: async () => (await api.get('/playbooks/marketplace')).data as PlaybookSummary[],
        enabled: tab === 'marketplace',
    });

    const importMutation = useMutation({
        mutationFn: async (ids: number[]) => (await api.post('/playbooks/import', { template_ids: ids })).data,
        onSuccess: () => {
            setSelected(new Set());
            qc.invalidateQueries({ queryKey: ['playbooks'] });
        },
    });
    const deleteMutation = useMutation({
        mutationFn: async (id: number) => api.delete(`/playbooks/${id}`),
        onSuccess: () => qc.invalidateQueries({ queryKey: ['playbooks'] }),
    });

    const toggle = (id: number) => {
        const next = new Set(selected);
        next.has(id) ? next.delete(id) : next.add(id);
        setSelected(next);
    };

    const published = mine.filter((p) => p.status !== 'draft');
    const drafts = mine.filter((p) => p.status === 'draft');
    const list = tab === 'mine' ? published : tab === 'drafts' ? drafts : market;
    const loading = tab === 'marketplace' ? marketLoading : mineLoading;

    return (
        <PageContainer className="space-y-0">
            <div className="flex items-end justify-between mb-5 flex-wrap gap-3">
                <div>
                    <h1 className="text-xl font-semibold tracking-tight text-fg">Playbooks</h1>
                    <p className="label-mono mt-1">incident response templates</p>
                </div>
                {(tab === 'mine' || tab === 'drafts') && canManage && (
                    <button onClick={() => { setNewMarketplace(false); setEditId('new'); }}
                        className="inline-flex items-center gap-1.5 h-9 px-3.5 bg-accent text-on-accent text-sm font-medium hover:bg-accent-hover transition-colors">
                        <Plus size={15} /> New Playbook
                    </button>
                )}
                {tab === 'marketplace' && (
                    <div className="flex items-center gap-2">
                        {isSuper && (
                            <button onClick={() => { setNewMarketplace(true); setEditId('new'); }}
                                className="inline-flex items-center gap-1.5 h-9 px-3.5 border border-line-strong text-fg-2 text-sm font-medium hover:bg-raised transition-colors">
                                <Plus size={15} /> New template
                            </button>
                        )}
                        {canManage && (
                            <button onClick={() => importMutation.mutate([...selected])}
                                disabled={selected.size === 0 || importMutation.isPending}
                                className="inline-flex items-center gap-1.5 h-9 px-3.5 bg-accent text-on-accent text-sm font-medium hover:bg-accent-hover transition-colors disabled:opacity-50">
                                <Download size={15} /> Import {selected.size > 0 ? `(${selected.size})` : ''}
                            </button>
                        )}
                    </div>
                )}
            </div>

            {/* Tabs */}
            <div className="flex border-b border-line mb-4">
                {([
                    ['mine', 'My Playbooks', BookText],
                    ...(canManage ? [['drafts', `Drafts${drafts.length ? ` (${drafts.length})` : ''}`, FileEdit] as const] : []),
                    ['marketplace', 'Marketplace', Store],
                ] as const).map(([key, label, Icon]) => (
                    <button key={key} onClick={() => setTab(key)}
                        className={cn('relative flex items-center gap-2 px-4 py-2.5 text-sm font-medium transition-colors',
                            tab === key ? 'text-accent' : 'text-fg-3 hover:text-fg')}>
                        <Icon size={15} /> {label}
                        {tab === key && <span className="absolute -bottom-px left-0 right-0 h-0.5 bg-accent" />}
                    </button>
                ))}
            </div>

            {loading ? (
                <p className="font-mono text-xs text-muted">$ loading…</p>
            ) : list.length === 0 ? (
                <div className="border border-dashed border-line-strong bg-panel p-10 text-center">
                    <ListChecks size={28} className="mx-auto text-muted mb-3" />
                    <p className="text-sm text-fg-2">
                        {tab === 'mine'
                            ? 'No playbooks yet. Import from the Marketplace or create one.'
                            : tab === 'drafts'
                                ? 'No drafts. Drafts proposed by integrations appear here until you publish them.'
                                : 'No marketplace templates available.'}
                    </p>
                    {tab === 'mine' && (
                        <button onClick={() => setTab('marketplace')}
                            className="mt-3 text-sm text-accent hover:text-accent font-medium">Browse marketplace →</button>
                    )}
                </div>
            ) : (
                <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
                    {list.map((t) => {
                        const isSel = selected.has(t.id);
                        return (
                            <div key={t.id}
                                className={cn('group bg-panel border p-4 flex flex-col transition-all',
                                    isSel ? 'border-accent shadow-console-hover' : 'border-line hover:border-accent/40')}>
                                <div className="flex items-start justify-between gap-2">
                                    <div className="min-w-0">
                                        <h3 className="text-sm font-semibold text-fg truncate">{t.name}</h3>
                                        <span className="font-mono text-[10px] uppercase tracking-wide text-muted">{t.category}</span>
                                        {t.status === 'draft' && (
                                            <span className="ml-2 font-mono text-[10px] uppercase text-amber-300 bg-amber-500/10 border border-amber-500/30 px-1.5 py-0.5">Draft</span>
                                        )}
                                    </div>
                                    {tab === 'marketplace' && (
                                        t.already_imported ? (
                                            <span className="shrink-0 inline-flex items-center gap-1 font-mono text-[10px] uppercase text-emerald-300 bg-emerald-500/10 border border-emerald-500/30 px-1.5 py-0.5">
                                                <Check size={11} /> imported
                                            </span>
                                        ) : canManage ? (
                                            <button onClick={() => toggle(t.id)}
                                                className={cn('shrink-0 w-5 h-5 border flex items-center justify-center',
                                                    isSel ? 'bg-accent border-accent text-on-accent' : 'border-line-strong hover:border-accent/40')}>
                                                {isSel && <Check size={13} />}
                                            </button>
                                        ) : null
                                    )}
                                </div>
                                {t.description && <p className="text-xs text-fg-3 mt-2 line-clamp-2 flex-1">{t.description}</p>}
                                <div className="mt-3 flex items-center justify-between">
                                    <span className="num text-[11px] text-fg-3">{t.task_count} tasks</span>
                                    <div className="flex items-center gap-2">
                                        {t.status === 'draft' && canManage && (
                                            <button onClick={() => publishMutation.mutate(t.id)} disabled={publishMutation.isPending}
                                                className="inline-flex items-center gap-1 text-xs text-accent hover:text-accent font-medium disabled:opacity-50">
                                                <Send size={12} /> Publish
                                            </button>
                                        )}
                                        <button onClick={() => { setNewMarketplace(false); setEditId(t.id); }}
                                            className="text-xs text-accent hover:text-accent font-medium">
                                            {(((tab === 'mine' || tab === 'drafts') && canManage && !t.is_system) || (tab === 'marketplace' && isSuper)) ? 'Edit' : 'View'}
                                        </button>
                                        {(((tab === 'mine' || tab === 'drafts') && canManage) || (tab === 'marketplace' && isSuper)) && (
                                            <button onClick={() => { if (confirm(`Delete "${t.name}"?`)) deleteMutation.mutate(t.id); }}
                                                className="text-muted hover:text-severity-critical" title="Delete">
                                                <Trash2 size={14} />
                                            </button>
                                        )}
                                    </div>
                                </div>
                            </div>
                        );
                    })}
                </div>
            )}

            {editId !== null && (
                <TemplateModal
                    templateId={editId}
                    canEdit={!!canManage}
                    marketplace={editId === 'new' ? newMarketplace : undefined}
                    isSuperAdmin={isSuper}
                    onClose={() => setEditId(null)}
                    onSaved={() => { setEditId(null); qc.invalidateQueries({ queryKey: ['playbooks'] }); }}
                />
            )}
        </PageContainer>
    );
}
