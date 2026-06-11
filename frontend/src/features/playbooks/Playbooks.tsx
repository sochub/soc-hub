import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { BookText, Store, Plus, Download, Check, Trash2, ListChecks } from 'lucide-react';
import { api } from '../../api/client';
import { cn } from '../../lib/utils';
import type { PlaybookSummary, User } from '../../types';
import TemplateModal from './TemplateModal';

type Tab = 'mine' | 'marketplace';

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
        queryKey: ['playbooks', 'mine'],
        queryFn: async () => (await api.get('/playbooks/')).data as PlaybookSummary[],
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

    const list = tab === 'mine' ? mine : market;
    const loading = tab === 'mine' ? mineLoading : marketLoading;

    return (
        <div className="p-4 sm:p-6 max-w-[1400px] mx-auto">
            <div className="flex items-end justify-between mb-5 flex-wrap gap-3">
                <div>
                    <h1 className="text-xl font-semibold tracking-tight text-zinc-900">Playbooks</h1>
                    <p className="label-mono mt-1">incident response templates</p>
                </div>
                {tab === 'mine' && canManage && (
                    <button onClick={() => { setNewMarketplace(false); setEditId('new'); }}
                        className="inline-flex items-center gap-1.5 h-9 px-3.5 bg-accent-600 text-white text-sm font-medium hover:bg-accent-700 transition-colors">
                        <Plus size={15} /> New Playbook
                    </button>
                )}
                {tab === 'marketplace' && (
                    <div className="flex items-center gap-2">
                        {isSuper && (
                            <button onClick={() => { setNewMarketplace(true); setEditId('new'); }}
                                className="inline-flex items-center gap-1.5 h-9 px-3.5 border border-zinc-300 text-zinc-700 text-sm font-medium hover:bg-zinc-100 transition-colors">
                                <Plus size={15} /> New template
                            </button>
                        )}
                        {canManage && (
                            <button onClick={() => importMutation.mutate([...selected])}
                                disabled={selected.size === 0 || importMutation.isPending}
                                className="inline-flex items-center gap-1.5 h-9 px-3.5 bg-accent-600 text-white text-sm font-medium hover:bg-accent-700 transition-colors disabled:opacity-50">
                                <Download size={15} /> Import {selected.size > 0 ? `(${selected.size})` : ''}
                            </button>
                        )}
                    </div>
                )}
            </div>

            {/* Tabs */}
            <div className="flex border-b border-zinc-200 mb-4">
                {([['mine', 'My Playbooks', BookText], ['marketplace', 'Marketplace', Store]] as const).map(([key, label, Icon]) => (
                    <button key={key} onClick={() => setTab(key)}
                        className={cn('relative flex items-center gap-2 px-4 py-2.5 text-sm font-medium transition-colors',
                            tab === key ? 'text-accent-700' : 'text-zinc-500 hover:text-zinc-800')}>
                        <Icon size={15} /> {label}
                        {tab === key && <span className="absolute bottom-[-1px] left-0 right-0 h-0.5 bg-accent-600" />}
                    </button>
                ))}
            </div>

            {loading ? (
                <p className="font-mono text-xs text-zinc-400">$ loading…</p>
            ) : list.length === 0 ? (
                <div className="border border-dashed border-zinc-300 bg-white p-10 text-center">
                    <ListChecks size={28} className="mx-auto text-zinc-300 mb-3" />
                    <p className="text-sm text-zinc-600">
                        {tab === 'mine'
                            ? 'No playbooks yet. Import from the Marketplace or create one.'
                            : 'No marketplace templates available.'}
                    </p>
                    {tab === 'mine' && (
                        <button onClick={() => setTab('marketplace')}
                            className="mt-3 text-sm text-accent-700 hover:text-accent-800 font-medium">Browse marketplace →</button>
                    )}
                </div>
            ) : (
                <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
                    {list.map((t) => {
                        const isSel = selected.has(t.id);
                        return (
                            <div key={t.id}
                                className={cn('group bg-white border p-4 flex flex-col transition-all',
                                    isSel ? 'border-accent-500 shadow-console-hover' : 'border-zinc-200 hover:border-accent-300')}>
                                <div className="flex items-start justify-between gap-2">
                                    <div className="min-w-0">
                                        <h3 className="text-sm font-semibold text-zinc-900 truncate">{t.name}</h3>
                                        <span className="font-mono text-[10px] uppercase tracking-wide text-zinc-400">{t.category}</span>
                                    </div>
                                    {tab === 'marketplace' && (
                                        t.already_imported ? (
                                            <span className="shrink-0 inline-flex items-center gap-1 font-mono text-[10px] uppercase text-emerald-700 bg-emerald-50 border border-emerald-200 px-1.5 py-0.5">
                                                <Check size={11} /> imported
                                            </span>
                                        ) : canManage ? (
                                            <button onClick={() => toggle(t.id)}
                                                className={cn('shrink-0 w-5 h-5 border flex items-center justify-center',
                                                    isSel ? 'bg-accent-600 border-accent-600 text-white' : 'border-zinc-300 hover:border-accent-400')}>
                                                {isSel && <Check size={13} />}
                                            </button>
                                        ) : null
                                    )}
                                </div>
                                {t.description && <p className="text-xs text-zinc-500 mt-2 line-clamp-2 flex-1">{t.description}</p>}
                                <div className="mt-3 flex items-center justify-between">
                                    <span className="num text-[11px] text-zinc-500">{t.task_count} tasks</span>
                                    <div className="flex items-center gap-2">
                                        <button onClick={() => { setNewMarketplace(false); setEditId(t.id); }}
                                            className="text-xs text-accent-700 hover:text-accent-800 font-medium">
                                            {((tab === 'mine' && canManage && !t.is_system) || (tab === 'marketplace' && isSuper)) ? 'Edit' : 'View'}
                                        </button>
                                        {((tab === 'mine' && canManage) || (tab === 'marketplace' && isSuper)) && (
                                            <button onClick={() => { if (confirm(`Delete "${t.name}"?`)) deleteMutation.mutate(t.id); }}
                                                className="text-zinc-300 hover:text-severity-critical" title="Delete">
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
        </div>
    );
}
