import { useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Plus, Download, Check, Trash2, Send, Search } from 'lucide-react';
import { api } from '../../api/client';
import { cn } from '../../lib/utils';
import type { PlaybookSummary, User } from '../../types';
import TemplateModal from './TemplateModal';
import PageContainer from '../../components/layout/PageContainer';
import { Button, Input, PageHeader, FolderTabs, Skeleton, EmptyState, ErrorState, Stamp, useConfirm } from '../../components/ui';

type Tab = 'mine' | 'drafts' | 'marketplace';
const TABS: Tab[] = ['mine', 'drafts', 'marketplace'];

function errorText(e: unknown, fallback: string): string {
    const d = (e as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
    return typeof d === 'string' ? d : fallback;
}

export default function Playbooks() {
    const qc = useQueryClient();
    const [params, setParams] = useSearchParams();
    const tabParam = params.get('tab') as Tab | null;
    const [selected, setSelected] = useState<Set<number>>(new Set());
    const [editId, setEditId] = useState<number | 'new' | null>(null);
    const [newMarketplace, setNewMarketplace] = useState(false);
    const [search, setSearch] = useState('');
    const [actionError, setActionError] = useState<string | null>(null);
    const { confirm, dialog } = useConfirm();

    const { data: me } = useQuery({
        queryKey: ['currentUser'],
        queryFn: async () => (await api.get('/users/me')).data as User,
        staleTime: 5 * 60 * 1000,
    });
    const canManage = me?.role === 'admin' || me?.is_super_admin;
    const isSuper = !!me?.is_super_admin;
    const tab: Tab = tabParam && TABS.includes(tabParam) && (tabParam !== 'drafts' || canManage) ? tabParam : 'mine';
    const setTab = (t: Tab) => {
        const next = new URLSearchParams(params);
        if (t === 'mine') next.delete('tab'); else next.set('tab', t);
        setParams(next, { replace: true });
    };

    const mineQ = useQuery({
        queryKey: ['playbooks', 'mine', 'with-drafts', !!canManage],
        queryFn: async () => (await api.get('/playbooks/', { params: { include_drafts: canManage ? true : undefined } })).data as PlaybookSummary[],
    });
    const mine = useMemo(() => mineQ.data ?? [], [mineQ.data]);
    const publishMutation = useMutation({
        mutationFn: async (id: number) => api.post(`/playbooks/${id}/publish`),
        onSuccess: () => { setActionError(null); qc.invalidateQueries({ queryKey: ['playbooks'] }); },
        onError: (e) => setActionError(errorText(e, 'Could not publish the playbook.')),
    });
    const marketQ = useQuery({
        queryKey: ['playbooks', 'marketplace'],
        queryFn: async () => (await api.get('/playbooks/marketplace')).data as PlaybookSummary[],
        enabled: tab === 'marketplace',
    });

    const importMutation = useMutation({
        mutationFn: async (ids: number[]) => (await api.post('/playbooks/import', { template_ids: ids })).data,
        onSuccess: () => {
            setSelected(new Set());
            setActionError(null);
            qc.invalidateQueries({ queryKey: ['playbooks'] });
        },
        onError: (e) => setActionError(errorText(e, 'Could not import the selected templates.')),
    });
    const deleteMutation = useMutation({
        mutationFn: async (id: number) => api.delete(`/playbooks/${id}`),
        onSuccess: () => { setActionError(null); qc.invalidateQueries({ queryKey: ['playbooks'] }); },
        onError: (e) => setActionError(errorText(e, 'Could not delete the playbook.')),
    });

    const toggle = (id: number) => {
        const next = new Set(selected);
        if (next.has(id)) next.delete(id); else next.add(id);
        setSelected(next);
    };

    const published = mine.filter((p) => p.status !== 'draft');
    const drafts = mine.filter((p) => p.status === 'draft');
    const activeQ = tab === 'marketplace' ? marketQ : mineQ;
    const fullList = tab === 'mine' ? published : tab === 'drafts' ? drafts : (marketQ.data ?? []);
    const q = search.trim().toLowerCase();
    const list = q ? fullList.filter((p) => p.name.toLowerCase().includes(q)) : fullList;
    const canEditIn = (t: PlaybookSummary) => tab === 'marketplace' ? isSuper : !!canManage && !t.is_system;
    const canDeleteIn = tab === 'marketplace' ? isSuper : !!canManage;

    const onDelete = async (t: PlaybookSummary) => {
        if (await confirm({ title: `Delete "${t.name}"?`, body: 'The playbook is removed for this organization. Cases that already used it keep their tasks.', danger: true, confirmLabel: 'Delete playbook' })) {
            deleteMutation.mutate(t.id);
        }
    };

    const actions = tab === 'marketplace' ? (
        <>
            {isSuper && (
                <Button variant="secondary" onClick={() => { setNewMarketplace(true); setEditId('new'); }}>
                    <Plus size={15} aria-hidden /> New template
                </Button>
            )}
            {canManage && (
                <Button onClick={() => importMutation.mutate([...selected])} disabled={selected.size === 0 || importMutation.isPending}>
                    <Download size={15} aria-hidden /> Import{selected.size > 0 && <span className="num">({selected.size})</span>}
                </Button>
            )}
        </>
    ) : canManage ? (
        <Button onClick={() => { setNewMarketplace(false); setEditId('new'); }}>
            <Plus size={15} aria-hidden /> New playbook
        </Button>
    ) : null;

    const tabItems = [
        { value: 'mine' as Tab, label: 'My playbooks', count: mineQ.data ? published.length : undefined },
        ...(canManage ? [{ value: 'drafts' as Tab, label: 'Drafts', count: drafts.length || undefined }] : []),
        { value: 'marketplace' as Tab, label: 'Marketplace' },
    ];

    return (
        <PageContainer className="space-y-0">
            <PageHeader title="Playbooks" description="Incident response templates: phase-grouped tasks you can apply to a case." actions={actions} />

            <FolderTabs items={tabItems} value={tab} onChange={setTab} label="Playbook lists" idPrefix="playbooks-tab">
                <div className="relative">
                    <Search size={14} aria-hidden className="absolute left-2.5 top-1/2 -translate-y-1/2 text-muted pointer-events-none" />
                    <Input inputSize="sm" type="search" value={search} onChange={(e) => setSearch(e.target.value)}
                        placeholder="Search by name" aria-label="Search playbooks by name" className="pl-8 w-48 sm:w-60" />
                </div>
            </FolderTabs>

            <div id="playbooks-tab-panel" role="tabpanel" aria-labelledby={`playbooks-tab-${tab}`} className="pt-4 space-y-4">
                {actionError && <ErrorState message={actionError} />}
                {activeQ.isLoading ? (
                    <Skeleton rows={4} />
                ) : activeQ.isError ? (
                    <ErrorState message="Could not load playbooks." onRetry={() => activeQ.refetch()} />
                ) : list.length === 0 ? (
                    <EmptyState
                        title={q ? 'No playbooks match that name.'
                            : tab === 'mine' ? 'No playbooks yet.'
                                : tab === 'drafts' ? 'No drafts.'
                                    : 'No marketplace templates available.'}
                        body={q ? undefined
                            : tab === 'mine' ? 'Import one from the Marketplace or create your own.'
                                : tab === 'drafts' ? 'Drafts proposed by integrations appear here until you publish them.' : undefined}
                        action={!q && tab === 'mine' ? <Button variant="secondary" onClick={() => setTab('marketplace')}>Browse marketplace</Button> : undefined}
                    />
                ) : (
                    <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
                        {list.map((t) => {
                            const isSel = selected.has(t.id);
                            const canEdit = canEditIn(t);
                            return (
                                <div key={t.id}
                                    className={cn('rounded-md bg-panel border p-4 flex flex-col transition-colors',
                                        isSel ? 'border-accent' : 'border-line hover:border-line-strong')}>
                                    <div className="flex items-start justify-between gap-2">
                                        <div className="min-w-0">
                                            <h3 className="text-sm font-semibold text-fg truncate">{t.name}</h3>
                                            <div className="mt-1 flex items-center gap-2">
                                                <span className="text-xs text-fg-3">{t.category}</span>
                                                {t.status === 'draft' && <Stamp tone="medium">Draft</Stamp>}
                                            </div>
                                        </div>
                                        {tab === 'marketplace' && (
                                            t.already_imported ? (
                                                <Stamp tone="ok" className="shrink-0 inline-flex items-center gap-1"><Check size={11} aria-hidden /> Imported</Stamp>
                                            ) : canManage ? (
                                                <input type="checkbox" checked={isSel} onChange={() => toggle(t.id)}
                                                    aria-label={`Select ${t.name}`} className="shrink-0 mt-0.5 h-4 w-4 accent-accent cursor-pointer" />
                                            ) : null
                                        )}
                                    </div>
                                    {t.description && <p className="text-xs text-fg-3 mt-2 line-clamp-2 flex-1">{t.description}</p>}
                                    <div className="mt-3 flex items-center justify-between">
                                        <span className="num text-xs text-fg-3">
                                            {t.task_count} task{t.task_count === 1 ? '' : 's'}
                                            {!t.is_system && <> · <span className={(t.workflow_count ?? 0) > 0 ? 'text-accent' : undefined}>
                                                {t.workflow_count ?? 0} workflow{t.workflow_count === 1 ? '' : 's'}</span></>}
                                        </span>
                                        <div className="flex items-center gap-1">
                                            {t.status === 'draft' && canManage && (
                                                <Button variant="ghost" size="sm" onClick={() => publishMutation.mutate(t.id)} disabled={publishMutation.isPending}>
                                                    <Send size={12} aria-hidden /> Publish
                                                </Button>
                                            )}
                                            <Button variant="ghost" size="sm" onClick={() => { setNewMarketplace(false); setEditId(t.id); }}>
                                                {canEdit ? 'Edit' : 'View'}
                                            </Button>
                                            {canDeleteIn && (
                                                <Button variant="ghost" size="sm" onClick={() => onDelete(t)}
                                                    aria-label={`Delete ${t.name}`} title="Delete" className="px-2 hover:text-severity-critical">
                                                    <Trash2 size={14} aria-hidden />
                                                </Button>
                                            )}
                                        </div>
                                    </div>
                                </div>
                            );
                        })}
                    </div>
                )}
            </div>

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
            {dialog}
        </PageContainer>
    );
}
