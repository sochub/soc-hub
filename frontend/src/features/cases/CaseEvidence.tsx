import { useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Copy, Download, Trash2, Upload } from 'lucide-react';
import { api } from '../../api/client';
import { cn } from '../../lib/utils';
import { Button, Badge, Stamp, Input, Skeleton, ErrorState, EmptyState } from '../../components/ui';
import { useCanRun } from '../enrichment/useCanRun';
import type { Attachment, AttachmentList, User } from '../../types';

function formatSize(n: number): string {
    if (n < 1024) return `${n} B`;
    const units = ['KB', 'MB', 'GB'];
    let v = n / 1024;
    let i = 0;
    while (v >= 1024 && i < units.length - 1) { v /= 1024; i++; }
    return `${v.toFixed(v < 10 ? 1 : 0)} ${units[i]}`;
}

function relTime(iso: string): string {
    const diff = Date.now() - new Date(iso).getTime();
    const m = Math.floor(diff / 60000);
    if (m < 1) return 'just now';
    if (m < 60) return `${m}m ago`;
    const h = Math.floor(m / 60);
    if (h < 24) return `${h}h ago`;
    return `${Math.floor(h / 24)}d ago`;
}

// Error bodies from blob requests are Blobs; JSON requests have parsed data.
async function errorDetail(err: unknown, maxMb?: number): Promise<string> {
    const resp = (err as { response?: { status?: number; data?: unknown } })?.response;
    let data = resp?.data;
    if (data instanceof Blob) {
        try { data = JSON.parse(await data.text()); } catch { data = undefined; }
    }
    const detail = (data as { detail?: unknown } | undefined)?.detail;
    if (typeof detail === 'string') return detail;
    // nginx answers oversized bodies with an HTML 413 (no JSON detail)
    if (resp?.status === 413 && maxMb) return `File exceeds ${maxMb} MB`;
    return 'Request failed';
}

export default function CaseEvidence({ caseId }: { caseId: number }) {
    const queryClient = useQueryClient();
    const canWrite = useCanRun();
    const { data: me } = useQuery({ queryKey: ['currentUser'], queryFn: async () => (await api.get('/users/me')).data as User });
    const isAdmin = !!me && (me.role === 'admin' || !!me.is_super_admin);

    const [showDeleted, setShowDeleted] = useState(false);
    const [malicious, setMalicious] = useState(false);
    const [description, setDescription] = useState('');
    const [dragOver, setDragOver] = useState(false);
    const [progress, setProgress] = useState<number | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [confirmId, setConfirmId] = useState<number | null>(null);
    const [downloading, setDownloading] = useState(false);
    const fileInput = useRef<HTMLInputElement>(null);

    const { data, isLoading, isError, refetch } = useQuery({
        queryKey: ['attachments', caseId, showDeleted],
        queryFn: async () => (await api.get(`/cases/${caseId}/attachments`, { params: { include_deleted: showDeleted } })).data as AttachmentList,
    });
    const maxMb = data?.max_upload_mb;
    // CaseDetail keys its case and audit queries by the route param (a string); refresh the timeline and audit tab.
    const invalidateCase = () => {
        queryClient.invalidateQueries({ queryKey: ['case', String(caseId)] });
        queryClient.invalidateQueries({ queryKey: ['audit-logs', 'case', String(caseId)] });
    };

    const upload = useMutation({
        mutationFn: async (file: File) => {
            const fd = new FormData();
            fd.append('file', file);
            fd.append('is_malicious', String(malicious));
            if (description.trim()) fd.append('description', description);
            setProgress(0);
            await api.post(`/cases/${caseId}/attachments`, fd, {
                headers: { 'Content-Type': 'multipart/form-data' },
                onUploadProgress: (e) => { if (e.total) setProgress(Math.round((e.loaded * 100) / e.total)); },
            });
        },
        onSuccess: () => {
            setError(null);
            setDescription('');
            setMalicious(false);
            queryClient.invalidateQueries({ queryKey: ['attachments', caseId] });
            queryClient.invalidateQueries({ queryKey: ['artifacts', String(caseId)] });
            queryClient.invalidateQueries({ queryKey: ['artifacts', 'all'] });
            invalidateCase();
        },
        onError: async (e) => setError(await errorDetail(e, maxMb)),
        onSettled: () => setProgress(null),
    });

    const remove = useMutation({
        mutationFn: async (id: number) => { await api.delete(`/cases/${caseId}/attachments/${id}`); },
        onSuccess: () => {
            setConfirmId(null);
            queryClient.invalidateQueries({ queryKey: ['attachments', caseId] });
            invalidateCase();
        },
        onError: async (e) => { setConfirmId(null); setError(await errorDetail(e)); },
    });

    const startUpload = (file: File | undefined) => {
        if (!file || upload.isPending) return;
        if (maxMb && file.size > maxMb * 1024 * 1024) {
            setError(`File exceeds ${maxMb} MB`);
            return;
        }
        setError(null);
        upload.mutate(file);
    };

    const download = async (a: Attachment) => {
        if (downloading) return;
        setDownloading(true);
        setError(null);
        try {
            const resp = await api.get(`/cases/${caseId}/attachments/${a.id}/download`, { responseType: 'blob' });
            const cd: string = resp.headers['content-disposition'] || '';
            const m = /filename\*=UTF-8''([^;]+)/i.exec(cd);
            let name = a.is_malicious ? `${a.filename}.zip` : a.filename;
            if (m) { try { name = decodeURIComponent(m[1]); } catch { /* keep fallback */ } }
            const url = URL.createObjectURL(resp.data as Blob);
            const link = document.createElement('a');
            link.href = url;
            link.download = name;
            document.body.appendChild(link);
            link.click();
            link.remove();
            setTimeout(() => URL.revokeObjectURL(url), 1000);
        } catch (e) {
            setError(await errorDetail(e));
        } finally {
            setDownloading(false);
        }
    };

    const copyHash = (hash: string) => {
        navigator.clipboard.writeText(hash).catch(() => setError('Could not copy to clipboard'));
    };

    const items = data?.items ?? [];

    return (
        <div className="space-y-4">
            <div className="flex justify-between items-center">
                <h2 className="text-sm font-semibold text-fg">Evidence</h2>
                {isAdmin && (
                    <label className="text-xs text-fg-3 flex items-center gap-2 cursor-pointer">
                        <input type="checkbox" checked={showDeleted} onChange={(e) => setShowDeleted(e.target.checked)} />
                        Show deleted
                    </label>
                )}
            </div>

            {canWrite && (
                <div className="p-4 rounded-md bg-panel border border-line space-y-3">
                    <div
                        onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
                        onDragLeave={() => setDragOver(false)}
                        onDrop={(e) => { e.preventDefault(); setDragOver(false); startUpload(e.dataTransfer.files?.[0]); }}
                        onClick={() => { if (!upload.isPending) fileInput.current?.click(); }}
                        className={cn(
                            'border-2 border-dashed rounded-md py-8 flex flex-col items-center justify-center gap-2 text-fg-3 cursor-pointer transition-colors',
                            dragOver ? 'border-accent bg-accent/10' : 'border-line-strong hover:bg-raised'
                        )}
                    >
                        <Upload size={20} aria-hidden />
                        <p className="text-sm">Drop a file here or click to choose{maxMb ? ` (max ${maxMb} MB)` : ''}</p>
                        <input
                            ref={fileInput}
                            type="file"
                            className="hidden"
                            aria-label="Choose evidence file"
                            onClick={(e) => e.stopPropagation()}
                            onChange={(e) => { startUpload(e.target.files?.[0]); e.target.value = ''; }}
                        />
                    </div>
                    <label className="flex items-start gap-2 text-sm text-fg-2 cursor-pointer">
                        <input type="checkbox" className="mt-1" checked={malicious} onChange={(e) => setMalicious(e.target.checked)} />
                        <span>
                            Malicious sample
                            <span className="block text-xs text-fg-3">Stored and downloaded as a ZIP, password <span className="num">infected</span></span>
                        </span>
                    </label>
                    <Input
                        type="text"
                        maxLength={1000}
                        value={description}
                        onChange={(e) => setDescription(e.target.value)}
                        placeholder="Description (optional)"
                        aria-label="Description (optional)"
                    />
                    {progress !== null && (
                        <div className="flex items-center gap-3">
                            <div className="flex-1 h-1.5 bg-raised rounded-sm overflow-hidden">
                                <div className="h-full bg-accent transition-all" style={{ width: `${progress}%` }} />
                            </div>
                            <span className="num text-xs text-fg-3">{progress}%</span>
                        </div>
                    )}
                </div>
            )}

            {error && (
                <div role="alert" className="text-xs text-severity-critical bg-severity-critical/5 border border-severity-critical/30 rounded-md px-3 py-2">{error}</div>
            )}

            {isLoading ? (
                <Skeleton rows={3} />
            ) : isError ? (
                <ErrorState message="Could not load the evidence." onRetry={() => refetch()} />
            ) : items.length === 0 ? (
                <EmptyState title="No evidence attached to this case" className="rounded-md border border-dashed border-line-strong" />
            ) : (
                <div className="rounded-md bg-panel border border-line overflow-x-auto">
                    <table className="w-full text-sm">
                        <thead>
                            <tr className="text-left label-caps border-b border-line">
                                <th className="px-3 py-2">Name</th>
                                <th className="px-3 py-2">Size</th>
                                <th className="px-3 py-2">SHA-256</th>
                                <th className="px-3 py-2">Uploaded</th>
                                <th className="px-3 py-2 text-right">Actions</th>
                            </tr>
                        </thead>
                        <tbody>
                            {items.map((a) => {
                                const deleted = !!a.deleted_at;
                                return (
                                    <tr key={a.id} className={cn('border-b border-line last:border-0 hover:bg-raised', deleted && 'opacity-50')}>
                                        <td className="px-3 py-2.5 min-w-0">
                                            <div className="flex items-center gap-2">
                                                <span className={cn('text-fg break-all', deleted && 'line-through')}>{a.filename}</span>
                                                {a.is_malicious && (
                                                    <Stamp tone="critical">Malicious</Stamp>
                                                )}
                                            </div>
                                            {a.description && <p className="text-xs text-fg-3 mt-0.5">{a.description}</p>}
                                        </td>
                                        <td className="px-3 py-2.5 num text-fg-2 whitespace-nowrap">{formatSize(a.size_bytes)}</td>
                                        <td className="px-3 py-2.5 whitespace-nowrap">
                                            <span className="font-mono text-xs text-fg-2" title={a.sha256}>{a.sha256.slice(0, 12)}</span>
                                            <button onClick={() => copyHash(a.sha256)} className="ml-2 p-1 text-fg-3 hover:text-fg hover:bg-raised rounded-sm" title="Copy SHA-256" aria-label="Copy SHA-256">
                                                <Copy size={12} />
                                            </button>
                                        </td>
                                        <td className="px-3 py-2.5 text-xs text-fg-3">
                                            <div>{a.uploaded_by_guest_email ? (
                                                <Badge variant="medium"
                                                    title="Uploaded by an external guest — untrusted origin">Guest · {a.uploaded_by_guest_email} · untrusted origin</Badge>
                                            ) : (a.uploaded_by_email ?? 'unknown')}</div>
                                            <div>{relTime(a.created_at)}</div>
                                            {deleted && <div className="text-severity-critical">Deleted by {a.deleted_by_email ?? 'unknown'}</div>}
                                        </td>
                                        <td className="px-3 py-2.5 text-right whitespace-nowrap">
                                            {!deleted && (
                                                confirmId === a.id ? (
                                                    <span className="inline-flex items-center gap-2 text-xs">
                                                        <span className="text-fg-2">Delete this file?</span>
                                                        <Button size="sm" variant="danger" className="h-7 px-2" onClick={() => remove.mutate(a.id)} disabled={remove.isPending}>Delete</Button>
                                                        <Button size="sm" variant="secondary" className="h-7 px-2" onClick={() => setConfirmId(null)}>Cancel</Button>
                                                    </span>
                                                ) : (
                                                    <span className="inline-flex items-center gap-2">
                                                        <Button size="sm" variant="secondary" onClick={() => download(a)} disabled={downloading}>
                                                            <Download size={12} aria-hidden />
                                                            {a.is_malicious ? 'Download (ZIP, pw: infected)' : 'Download'}
                                                        </Button>
                                                        {canWrite && (
                                                            <button onClick={() => setConfirmId(a.id)} className="p-1.5 text-fg-3 hover:text-severity-critical hover:bg-raised rounded-sm transition-colors" title="Delete" aria-label={`Delete ${a.filename}`}>
                                                                <Trash2 size={14} />
                                                            </button>
                                                        )}
                                                    </span>
                                                )
                                            )}
                                        </td>
                                    </tr>
                                );
                            })}
                        </tbody>
                    </table>
                </div>
            )}
        </div>
    );
}
