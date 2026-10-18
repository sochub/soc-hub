import { useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Copy, Download, Paperclip, Trash2, Upload } from 'lucide-react';
import { api } from '../../api/client';
import { cn } from '../../lib/utils';
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

    const { data, isLoading } = useQuery({
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
                <h3 className="text-sm font-semibold text-fg-2">Evidence</h3>
                {isAdmin && (
                    <label className="text-xs text-fg-3 flex items-center gap-2 cursor-pointer">
                        <input type="checkbox" checked={showDeleted} onChange={(e) => setShowDeleted(e.target.checked)} />
                        Show deleted
                    </label>
                )}
            </div>

            {canWrite && (
                <div className="glass-panel p-4 rounded-lg bg-panel border border-line space-y-3">
                    <div
                        onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
                        onDragLeave={() => setDragOver(false)}
                        onDrop={(e) => { e.preventDefault(); setDragOver(false); startUpload(e.dataTransfer.files?.[0]); }}
                        onClick={() => { if (!upload.isPending) fileInput.current?.click(); }}
                        className={cn(
                            'border-2 border-dashed rounded-lg py-8 flex flex-col items-center justify-center gap-2 text-muted cursor-pointer transition-colors',
                            dragOver ? 'border-accent bg-accent/10' : 'border-line hover:border-line-strong'
                        )}
                    >
                        <Upload size={20} />
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
                            <span className="block text-xs text-muted">Stored and downloaded as a ZIP, password <span className="num">infected</span></span>
                        </span>
                    </label>
                    <input
                        type="text"
                        maxLength={1000}
                        value={description}
                        onChange={(e) => setDescription(e.target.value)}
                        placeholder="Description (optional)"
                        className="w-full text-sm border border-line rounded-sm px-3 py-1.5 bg-panel text-fg focus:outline-hidden focus:border-accent"
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
                <div role="alert" className="text-xs text-red-300 bg-red-500/10 border border-red-500/30 rounded-sm px-3 py-2">{error}</div>
            )}

            {isLoading ? (
                <div className="text-center py-8 text-muted">Loading evidence...</div>
            ) : items.length === 0 ? (
                <div className="glass-panel p-4 rounded-lg border-dashed border-2 border-line flex flex-col items-center justify-center text-muted py-12">
                    <Paperclip size={24} className="mb-2 opacity-50" />
                    <p className="text-sm">No evidence attached to this case.</p>
                </div>
            ) : (
                <div className="glass-panel rounded-lg bg-panel border border-line overflow-x-auto">
                    <table className="w-full text-sm">
                        <thead>
                            <tr className="text-left text-xs text-muted uppercase tracking-wider border-b border-line">
                                <th className="px-4 py-2 font-semibold">Name</th>
                                <th className="px-4 py-2 font-semibold">Size</th>
                                <th className="px-4 py-2 font-semibold">SHA-256</th>
                                <th className="px-4 py-2 font-semibold">Uploaded</th>
                                <th className="px-4 py-2 font-semibold text-right">Actions</th>
                            </tr>
                        </thead>
                        <tbody>
                            {items.map((a) => {
                                const deleted = !!a.deleted_at;
                                return (
                                    <tr key={a.id} className={cn('border-b border-line last:border-0', deleted && 'opacity-50')}>
                                        <td className="px-4 py-2 min-w-0">
                                            <div className="flex items-center gap-2">
                                                <span className={cn('text-fg break-all', deleted && 'line-through')}>{a.filename}</span>
                                                {a.is_malicious && (
                                                    <span className="text-[10px] bg-red-500/10 text-red-300 border border-red-500/30 px-1.5 py-0.5 rounded-sm uppercase font-bold">malicious</span>
                                                )}
                                            </div>
                                            {a.description && <p className="text-xs text-muted mt-0.5">{a.description}</p>}
                                        </td>
                                        <td className="px-4 py-2 num text-fg-2 whitespace-nowrap">{formatSize(a.size_bytes)}</td>
                                        <td className="px-4 py-2 whitespace-nowrap">
                                            <span className="num text-fg-2" title={a.sha256}>{a.sha256.slice(0, 12)}</span>
                                            <button onClick={() => copyHash(a.sha256)} className="ml-2 p-1 text-muted hover:text-fg-2 rounded-sm" title="Copy SHA-256" aria-label="Copy SHA-256">
                                                <Copy size={12} />
                                            </button>
                                        </td>
                                        <td className="px-4 py-2 text-xs text-fg-3">
                                            <div>{a.uploaded_by_guest_email ? (
                                                <span className="px-1.5 py-0.5 rounded text-[10px] font-medium bg-amber-500/15 text-amber-300"
                                                    title="Uploaded by an external guest — untrusted origin">Guest · {a.uploaded_by_guest_email} · untrusted origin</span>
                                            ) : (a.uploaded_by_email ?? 'unknown')}</div>
                                            <div className="text-muted">{relTime(a.created_at)}</div>
                                            {deleted && <div className="text-red-300">deleted by {a.deleted_by_email ?? 'unknown'}</div>}
                                        </td>
                                        <td className="px-4 py-2 text-right whitespace-nowrap">
                                            {!deleted && (
                                                confirmId === a.id ? (
                                                    <span className="inline-flex items-center gap-2 text-xs">
                                                        <span className="text-fg-2">Delete this file?</span>
                                                        <button onClick={() => remove.mutate(a.id)} disabled={remove.isPending} className="px-2 py-1 bg-red-600 text-white rounded-sm hover:bg-red-700 disabled:opacity-50">Delete</button>
                                                        <button onClick={() => setConfirmId(null)} className="px-2 py-1 bg-raised text-fg-2 rounded-sm hover:bg-raised-2">Cancel</button>
                                                    </span>
                                                ) : (
                                                    <span className="inline-flex items-center gap-2">
                                                        <button
                                                            onClick={() => download(a)}
                                                            disabled={downloading}
                                                            className="text-xs bg-raised hover:bg-raised-2 text-fg-2 px-3 py-1.5 rounded-sm transition-colors flex items-center gap-1.5 disabled:opacity-50"
                                                        >
                                                            <Download size={12} />
                                                            {a.is_malicious ? 'Download (ZIP, pw: infected)' : 'Download'}
                                                        </button>
                                                        {canWrite && (
                                                            <button onClick={() => setConfirmId(a.id)} className="p-1.5 text-muted hover:text-red-300 hover:bg-raised rounded-sm transition-colors" title="Delete" aria-label="Delete">
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
