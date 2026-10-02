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
    const [busyId, setBusyId] = useState<number | null>(null);
    const fileInput = useRef<HTMLInputElement>(null);

    const { data, isLoading } = useQuery({
        queryKey: ['attachments', caseId, showDeleted],
        queryFn: async () => (await api.get(`/cases/${caseId}/attachments`, { params: { include_deleted: showDeleted } })).data as AttachmentList,
    });
    const maxMb = data?.max_upload_mb;

    const upload = useMutation({
        mutationFn: async (file: File) => {
            const fd = new FormData();
            fd.append('file', file);
            fd.append('is_malicious', String(malicious));
            fd.append('description', description);
            setProgress(0);
            await api.post(`/cases/${caseId}/attachments`, fd, {
                onUploadProgress: (e) => { if (e.total) setProgress(Math.round((e.loaded * 100) / e.total)); },
            });
        },
        onSuccess: () => {
            setError(null);
            setDescription('');
            setMalicious(false);
            queryClient.invalidateQueries({ queryKey: ['attachments', caseId] });
            queryClient.invalidateQueries({ queryKey: ['artifacts', String(caseId)] });
        },
        onError: async (e) => setError(await errorDetail(e, maxMb)),
        onSettled: () => setProgress(null),
    });

    const remove = useMutation({
        mutationFn: async (id: number) => { await api.delete(`/cases/${caseId}/attachments/${id}`); },
        onSuccess: () => {
            setConfirmId(null);
            queryClient.invalidateQueries({ queryKey: ['attachments', caseId] });
        },
        onError: async (e) => { setConfirmId(null); setError(await errorDetail(e)); },
    });

    const startUpload = (file: File | undefined) => {
        if (!file) return;
        if (maxMb && file.size > maxMb * 1024 * 1024) {
            setError(`File exceeds ${maxMb} MB`);
            return;
        }
        setError(null);
        upload.mutate(file);
    };

    const download = async (a: Attachment) => {
        setBusyId(a.id);
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
            URL.revokeObjectURL(url);
        } catch (e) {
            setError(await errorDetail(e));
        } finally {
            setBusyId(null);
        }
    };

    const copyHash = (hash: string) => {
        navigator.clipboard.writeText(hash).catch(() => setError('Could not copy to clipboard'));
    };

    const items = data?.items ?? [];

    return (
        <div className="space-y-4">
            <div className="flex justify-between items-center">
                <h3 className="text-sm font-semibold text-zinc-700">Evidence</h3>
                {isAdmin && (
                    <label className="text-xs text-zinc-500 flex items-center gap-2 cursor-pointer">
                        <input type="checkbox" checked={showDeleted} onChange={(e) => setShowDeleted(e.target.checked)} />
                        Show deleted
                    </label>
                )}
            </div>

            {canWrite && (
                <div className="glass-panel p-4 rounded-lg bg-white border border-zinc-200 space-y-3">
                    <div
                        onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
                        onDragLeave={() => setDragOver(false)}
                        onDrop={(e) => { e.preventDefault(); setDragOver(false); startUpload(e.dataTransfer.files?.[0]); }}
                        onClick={() => fileInput.current?.click()}
                        className={cn(
                            'border-2 border-dashed rounded-lg py-8 flex flex-col items-center justify-center gap-2 text-zinc-400 cursor-pointer transition-colors',
                            dragOver ? 'border-accent-600 bg-blue-50' : 'border-zinc-200 hover:border-zinc-300'
                        )}
                    >
                        <Upload size={20} />
                        <p className="text-sm">Drop a file here or click to choose{maxMb ? ` (max ${maxMb} MB)` : ''}</p>
                        <input
                            ref={fileInput}
                            type="file"
                            className="hidden"
                            aria-label="Choose evidence file"
                            onChange={(e) => { startUpload(e.target.files?.[0]); e.target.value = ''; }}
                        />
                    </div>
                    <label className="flex items-start gap-2 text-sm text-zinc-700 cursor-pointer">
                        <input type="checkbox" className="mt-1" checked={malicious} onChange={(e) => setMalicious(e.target.checked)} />
                        <span>
                            Malicious sample
                            <span className="block text-xs text-zinc-400">Stored and downloaded as a ZIP, password <span className="num">infected</span></span>
                        </span>
                    </label>
                    <input
                        type="text"
                        maxLength={1000}
                        value={description}
                        onChange={(e) => setDescription(e.target.value)}
                        placeholder="Description (optional)"
                        className="w-full text-sm border border-zinc-200 rounded px-3 py-1.5 bg-white text-zinc-800 focus:outline-none focus:border-accent-600"
                    />
                    {progress !== null && (
                        <div className="flex items-center gap-3">
                            <div className="flex-1 h-1.5 bg-zinc-100 rounded overflow-hidden">
                                <div className="h-full bg-accent-600 transition-all" style={{ width: `${progress}%` }} />
                            </div>
                            <span className="num text-xs text-zinc-500">{progress}%</span>
                        </div>
                    )}
                </div>
            )}

            {error && (
                <div role="alert" className="text-xs text-red-700 bg-red-50 border border-red-200 rounded px-3 py-2">{error}</div>
            )}

            {isLoading ? (
                <div className="text-center py-8 text-zinc-400">Loading evidence...</div>
            ) : items.length === 0 ? (
                <div className="glass-panel p-4 rounded-lg border-dashed border-2 border-zinc-200 flex flex-col items-center justify-center text-zinc-400 py-12">
                    <Paperclip size={24} className="mb-2 opacity-50" />
                    <p className="text-sm">No evidence attached to this case.</p>
                </div>
            ) : (
                <div className="glass-panel rounded-lg bg-white border border-zinc-200 overflow-x-auto">
                    <table className="w-full text-sm">
                        <thead>
                            <tr className="text-left text-xs text-zinc-400 uppercase tracking-wider border-b border-zinc-200">
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
                                    <tr key={a.id} className={cn('border-b border-zinc-100 last:border-0', deleted && 'opacity-50')}>
                                        <td className="px-4 py-2 min-w-0">
                                            <div className="flex items-center gap-2">
                                                <span className={cn('text-zinc-800 break-all', deleted && 'line-through')}>{a.filename}</span>
                                                {a.is_malicious && (
                                                    <span className="text-[10px] bg-red-50 text-red-700 border border-red-200 px-1.5 py-0.5 rounded uppercase font-bold">malicious</span>
                                                )}
                                            </div>
                                            {a.description && <p className="text-xs text-zinc-400 mt-0.5">{a.description}</p>}
                                        </td>
                                        <td className="px-4 py-2 num text-zinc-600 whitespace-nowrap">{formatSize(a.size_bytes)}</td>
                                        <td className="px-4 py-2 whitespace-nowrap">
                                            <span className="num text-zinc-600" title={a.sha256}>{a.sha256.slice(0, 12)}</span>
                                            <button onClick={() => copyHash(a.sha256)} className="ml-2 p-1 text-zinc-400 hover:text-zinc-700 rounded" title="Copy SHA-256" aria-label="Copy SHA-256">
                                                <Copy size={12} />
                                            </button>
                                        </td>
                                        <td className="px-4 py-2 text-xs text-zinc-500">
                                            <div>{a.uploaded_by_email ?? 'unknown'}</div>
                                            <div className="text-zinc-400">{relTime(a.created_at)}</div>
                                            {deleted && <div className="text-red-700">deleted by {a.deleted_by_email ?? 'unknown'}</div>}
                                        </td>
                                        <td className="px-4 py-2 text-right whitespace-nowrap">
                                            {!deleted && (
                                                confirmId === a.id ? (
                                                    <span className="inline-flex items-center gap-2 text-xs">
                                                        <span className="text-zinc-600">Delete this file?</span>
                                                        <button onClick={() => remove.mutate(a.id)} disabled={remove.isPending} className="px-2 py-1 bg-red-600 text-white rounded hover:bg-red-700 disabled:opacity-50">Delete</button>
                                                        <button onClick={() => setConfirmId(null)} className="px-2 py-1 bg-zinc-100 text-zinc-700 rounded hover:bg-zinc-200">Cancel</button>
                                                    </span>
                                                ) : (
                                                    <span className="inline-flex items-center gap-2">
                                                        <button
                                                            onClick={() => download(a)}
                                                            disabled={busyId === a.id}
                                                            className="text-xs bg-zinc-100 hover:bg-zinc-200 text-zinc-700 px-3 py-1.5 rounded transition-colors flex items-center gap-1.5 disabled:opacity-50"
                                                        >
                                                            <Download size={12} />
                                                            {a.is_malicious ? 'Download (ZIP, pw: infected)' : 'Download'}
                                                        </button>
                                                        {canWrite && (
                                                            <button onClick={() => setConfirmId(a.id)} className="p-1.5 text-zinc-400 hover:text-red-700 hover:bg-zinc-100 rounded transition-colors" title="Delete" aria-label="Delete">
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
