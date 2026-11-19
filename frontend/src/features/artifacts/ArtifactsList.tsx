import { useEffect, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient, keepPreviousData } from '@tanstack/react-query';
import { api } from '../../api/client';
import { Link } from 'react-router-dom';
import { FileText, Globe, Mail, Server, Monitor, User, ArrowRight, Calendar, Hash, Link2, Pencil, Trash2, Check, X } from 'lucide-react';
import { Badge, Button, Input, Pager, Select, Skeleton, ErrorState, EmptyState, useConfirm } from '../../components/ui';
import { getPage, showingText } from '../../api/paging';
import { useArtifactTypes } from './useArtifactTypes';

const ARTIFACT_ICONS: Record<string, any> = {
    hash: FileText,
    file_hash: FileText,
    ip: Server,
    domain: Globe,
    url: Globe,
    email: Mail,
    hostname: Monitor,
    username: User,
};

const PAGE_SIZE = 100;

export default function ArtifactsList() {
    const queryClient = useQueryClient();
    const { options: ARTIFACT_TYPE_OPTIONS } = useArtifactTypes();
    const [searchParams, setSearchParams] = useSearchParams();
    const [editingId, setEditingId] = useState<number | null>(null);
    const [editData, setEditData] = useState({ artifact_type: '', value: '', description: '' });

    const { confirm, dialog } = useConfirm();
    // Filters live in the URL; value search runs server-side after a pause in typing.
    const [page, setPage] = useState(0);
    const rawQ = searchParams.get('q') ?? '';
    const typeFilter = searchParams.get('type') ?? '';
    const setParam = (key: 'q' | 'type', value: string) => {
        const next = new URLSearchParams(searchParams);
        if (value) next.set(key, value); else next.delete(key);
        setSearchParams(next, { replace: true });
        setPage(0);
    };
    const [q, setQ] = useState(rawQ.trim());
    useEffect(() => {
        const t = setTimeout(() => setQ(rawQ.trim()), 300);
        return () => clearTimeout(t);
    }, [rawQ]);
    const params = { ...(q && { q }), ...(typeFilter && { artifact_type: typeFilter }), skip: page * PAGE_SIZE, limit: PAGE_SIZE };
    const { data: result, isLoading, isError, refetch } = useQuery({
        queryKey: ['artifacts', 'all', 'list', params],
        queryFn: () => getPage<Record<string, unknown>>('/artifacts/', params),
        placeholderData: keepPreviousData,
    });
    const artifacts = result?.items;
    const total = result?.total ?? 0;
    useEffect(() => {
        if (result && result.items.length === 0 && result.total > 0 && page > 0) setPage(Math.max(0, Math.ceil(result.total / PAGE_SIZE) - 1));
    }, [result, page]);

    const updateMutation = useMutation({
        mutationFn: async ({ id, data }: { id: number; data: { artifact_type?: string; value?: string; description?: string } }) => {
            const response = await api.put(`/artifacts/${id}`, data);
            return response.data;
        },
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['artifacts'] });
            setEditingId(null);
        }
    });

    const deleteMutation = useMutation({
        mutationFn: async (id: number) => {
            await api.delete(`/artifacts/${id}`);
        },
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['artifacts'] });
        }
    });

    const startEdit = (artifact: any) => {
        setEditingId(artifact.id);
        setEditData({
            artifact_type: artifact.artifact_type,
            value: artifact.value,
            description: artifact.description || '',
        });
    };

    const cancelEdit = () => {
        setEditingId(null);
    };

    const saveEdit = (id: number) => {
        updateMutation.mutate({ id, data: editData });
    };

    const all = (artifacts ?? []) as { artifact_type: string; value: string }[];
    const typeLabel = (t: string) => ARTIFACT_TYPE_OPTIONS.find(o => o.value === t)?.label ?? t;
    // The server filters by type, so offer every known type (plus a custom one already in the URL).
    const presentTypes = [...new Set([...ARTIFACT_TYPE_OPTIONS.map(o => o.value as string), ...(typeFilter ? [typeFilter] : [])])];
    const visible = all;
    const filtering = !!rawQ || !!typeFilter;

    return (
        <div className="p-4 space-y-4">
            <div className="flex flex-wrap items-center gap-2">
                <p className="mr-auto text-sm text-fg-3">
                    {isLoading ? '\u00a0' : <>{showingText(page, PAGE_SIZE, all.length, total, 'artifacts')}. Confirmed-malicious ones live under IOCs.</>}
                </p>
                <Select inputSize="sm" className="w-auto" aria-label="Type" value={typeFilter} onChange={e => setParam('type', e.target.value)}>
                    <option value="">All types</option>
                    {presentTypes.map(t => <option key={t} value={t}>{typeLabel(t)}</option>)}
                </Select>
                <Input type="search" inputSize="sm" className="w-56" aria-label="Filter by value" value={rawQ} placeholder="Filter by value…"
                    onChange={e => setParam('q', e.target.value)} />
            </div>

            {isError ? <ErrorState message="Could not load artifacts." onRetry={() => refetch()} /> : isLoading ? <Skeleton rows={8} /> : all.length === 0 && !filtering ? (
                <EmptyState title="No artifacts yet" body="Artifacts added to cases will appear here automatically." />
            ) : visible.length === 0 ? (
                <EmptyState title="No artifacts match these filters"
                    action={<Button size="sm" variant="secondary" onClick={() => setSearchParams({}, { replace: true })}>Clear filters</Button>} />
            ) : (
                <div className="-mx-4 overflow-x-auto border-y border-line">
                    <table className="w-full text-left text-sm border-collapse">
                        <thead>
                            <tr className="border-b border-line bg-surface label-caps">
                                <th className="px-3 py-2 w-32">Type</th>
                                <th className="px-3 py-2">Value</th>
                                <th className="px-3 py-2 w-64">Related cases</th>
                                <th className="px-3 py-2 w-40">Created</th>
                                <th className="px-3 py-2 w-24 text-right">Actions</th>
                            </tr>
                        </thead>
                        <tbody className="divide-y divide-line">
                                {visible.map((artifact: any) => {
                                    const isEditing = editingId === artifact.id;
                                    const Icon = ARTIFACT_ICONS[artifact.artifact_type] || FileText;
                                    const caseIds: number[] = artifact.case_ids || [];

                                    return (
                                        <tr
                                            key={artifact.id}
                                            className={`group transition-colors ${isEditing ? 'bg-raised' : 'hover:bg-raised'}`}
                                        >
                                            {isEditing ? (
                                                <>
                                                    <td className="px-3 py-2.5">
                                                        <Select
                                                            inputSize="sm"
                                                            aria-label="Type"
                                                            value={editData.artifact_type}
                                                            onChange={(e) => setEditData({ ...editData, artifact_type: e.target.value })}
                                                        >
                                                            {ARTIFACT_TYPE_OPTIONS.map(opt => (
                                                                <option key={opt.value} value={opt.value}>{opt.label}</option>
                                                            ))}
                                                        </Select>
                                                    </td>
                                                    <td className="px-3 py-2.5">
                                                        <div className="space-y-2">
                                                            <Input
                                                                inputSize="sm"
                                                                aria-label="Value"
                                                                value={editData.value}
                                                                onChange={(e) => setEditData({ ...editData, value: e.target.value })}
                                                                className="font-mono"
                                                            />
                                                            <Input
                                                                inputSize="sm"
                                                                aria-label="Description"
                                                                value={editData.description}
                                                                onChange={(e) => setEditData({ ...editData, description: e.target.value })}
                                                                placeholder="Description (optional)"
                                                            />
                                                        </div>
                                                    </td>
                                                    <td className="px-3 py-2.5">
                                                        <div className="flex flex-wrap gap-2">
                                                            {caseIds.map((cid: number) => (
                                                                <span key={cid} className="flex items-center gap-1 text-xs text-muted">
                                                                    <Hash size={10} />
                                                                    {String(cid).padStart(4, '0')}
                                                                </span>
                                                            ))}
                                                        </div>
                                                    </td>
                                                    <td className="px-3 py-2.5">
                                                        <div className="flex items-center gap-2 text-xs text-muted">
                                                            <Calendar size={12} />
                                                            {new Date(artifact.created_at).toLocaleDateString()}
                                                        </div>
                                                    </td>
                                                    <td className="px-3 py-2.5">
                                                        <div className="flex items-center justify-end gap-1">
                                                            <Button
                                                                variant="ghost" size="icon"
                                                                onClick={() => saveEdit(artifact.id)}
                                                                disabled={!editData.value.trim() || updateMutation.isPending}
                                                                aria-label="Save" title="Save"
                                                            >
                                                                <Check size={14} />
                                                            </Button>
                                                            <Button variant="ghost" size="icon" onClick={cancelEdit} aria-label="Cancel" title="Cancel">
                                                                <X size={14} />
                                                            </Button>
                                                        </div>
                                                    </td>
                                                </>
                                            ) : (
                                                <>
                                                    <td className="px-3 py-2.5">
                                                        <div className="flex items-center gap-2">
                                                            <div className="p-1.5 bg-raised rounded-sm text-accent">
                                                                <Icon size={14} />
                                                            </div>
                                                            <span className="text-xs font-medium text-fg-3">{artifact.artifact_type}</span>
                                                        </div>
                                                    </td>
                                                    <td className="px-3 py-2.5">
                                                        <div className="flex items-center gap-2">
                                                            <span className="font-mono text-sm text-fg">{artifact.value}</span>
                                                            {!artifact.isolated && caseIds.length > 1 && (
                                                                <Badge variant="brand">
                                                                    <Link2 size={10} aria-hidden />
                                                                    Shared
                                                                </Badge>
                                                            )}
                                                            {artifact.isolated && (
                                                                <Badge variant="medium">Isolated</Badge>
                                                            )}
                                                        </div>
                                                        {artifact.description && (
                                                            <p className="text-xs text-muted mt-1">{artifact.description}</p>
                                                        )}
                                                    </td>
                                                    <td className="px-3 py-2.5">
                                                        <div className="flex flex-wrap gap-2">
                                                            {caseIds.length > 0 ? caseIds.map((cid: number) => (
                                                                <Link
                                                                    key={cid}
                                                                    to={`/cases/${cid}`}
                                                                    className="flex items-center gap-1 text-sm text-fg-3 hover:text-fg transition-colors group/link"
                                                                >
                                                                    <Hash size={12} />
                                                                    <span className="num">{String(cid).padStart(4, '0')}</span>
                                                                    <ArrowRight size={12} className="opacity-0 group-hover/link:opacity-100 transition-opacity" />
                                                                </Link>
                                                            )) : (
                                                                <span className="text-xs text-muted">No cases</span>
                                                            )}
                                                        </div>
                                                    </td>
                                                    <td className="px-3 py-2.5">
                                                        <div className="flex items-center gap-2 text-xs text-muted">
                                                            <Calendar size={12} />
                                                            {new Date(artifact.created_at).toLocaleDateString()}
                                                        </div>
                                                    </td>
                                                    <td className="px-3 py-2.5">
                                                        <div className="flex items-center justify-end gap-1 opacity-0 group-hover:opacity-100 group-focus-within:opacity-100 [@media(hover:none)]:opacity-100 transition-opacity">
                                                            <Button variant="ghost" size="icon" onClick={() => startEdit(artifact)} aria-label="Edit artifact" title="Edit artifact">
                                                                <Pencil size={14} />
                                                            </Button>
                                                            <Button
                                                                variant="ghost" size="icon"
                                                                className="hover:text-severity-critical"
                                                                onClick={async () => {
                                                                    if (await confirm({
                                                                        title: 'Delete this artifact?',
                                                                        body: <>“<span className="font-mono">{artifact.value}</span>” will be removed from all linked cases.</>,
                                                                        danger: true, confirmLabel: 'Delete artifact',
                                                                    })) {
                                                                        deleteMutation.mutate(artifact.id);
                                                                    }
                                                                }}
                                                                aria-label="Delete artifact" title="Delete artifact"
                                                            >
                                                                <Trash2 size={14} />
                                                            </Button>
                                                        </div>
                                                    </td>
                                                </>
                                            )}
                                        </tr>
                                    );
                                })}
                        </tbody>
                    </table>
                </div>
            )}
            <Pager page={page} pageSize={PAGE_SIZE} total={total} onPage={setPage} />
            {dialog}
        </div>
    );
}
