import { useEffect, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { AlertTriangle, FileDown, FileJson, Save, Sparkles } from 'lucide-react';
import { api } from '../../api/client';
import { cn } from '../../lib/utils';
import MilestonesEditor from './MilestonesEditor';
import { usePasteImage } from './usePasteImage';
import { useCanRun } from '../enrichment/useCanRun';
import { Button, Stamp, Textarea, Skeleton, ErrorState, TONE_TEXT, tlpTone } from '../../components/ui';
import type { CaseReportView, ReportDraft, TLPLevel } from '../../types';

const TEXT_MAX = 20000;
const LOCK_RENEW_MS = 45_000;
const CONFLICT_COPY = 'This report changed while you were editing — copy your changes, then reload.';
type LockRun = { cancelled: boolean; held: boolean };
type LockResp = { locked_by_email: string | null; updated_at: string | null };
const isVersionConflict = (e: unknown) => {
    const r = (e as { response?: { status?: number; data?: { detail?: unknown } } })?.response;
    return r?.status === 409 && r.data?.detail === 'version_conflict';
};
const lockHolder = (e: unknown): string | null => {
    const r = (e as { response?: { status?: number; data?: LockResp } })?.response;
    return r?.status === 409 ? (r.data?.locked_by_email ?? 'another user') : null;
};
const TLP_ORDER: TLPLevel[] = ['white', 'green', 'amber', 'red'];
const TLP_LABEL: Record<TLPLevel, string> = { white: 'CLEAR', green: 'GREEN', amber: 'AMBER', red: 'RED' };

const TEXT_FIELDS = [
    { key: 'executive_summary', label: 'Executive summary' },
    { key: 'impact', label: 'Impact' },
    { key: 'lessons_learned', label: 'Lessons learned' },
] as const;
type TextKey = typeof TEXT_FIELDS[number]['key'];

const DURATIONS = [
    { key: 'ttd_seconds', label: 'Time to detect', span: 'first seen → detected' },
    { key: 'ttc_seconds', label: 'Time to contain', span: 'detected → contained' },
    { key: 'ttr_seconds', label: 'Time to recover', span: 'contained → recovered' },
] as const;

type Form = Record<TextKey, string> & { tlp: TLPLevel };

const tlpIndex = (t: TLPLevel) => TLP_ORDER.indexOf(t);
const clampTlp = (t: TLPLevel, min: TLPLevel): TLPLevel => (tlpIndex(t) < tlpIndex(min) ? min : t);

function fmtDuration(s: number | null): string {
    if (s === null || s < 0) return 'unknown';
    const m = Math.floor(s / 60);
    const d = Math.floor(m / 1440), h = Math.floor((m % 1440) / 60), mm = m % 60;
    if (d) return `${d}d ${h}h`;
    if (h) return `${h}h ${mm}m`;
    return `${mm}m`;
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

// Error bodies from blob requests are Blobs; FastAPI validation errors carry a list of {msg}.
async function errorDetail(err: unknown): Promise<string> {
    const resp = (err as { response?: { status?: number; data?: unknown } })?.response;
    let data = resp?.data;
    if (data instanceof Blob) {
        try { data = JSON.parse(await data.text()); } catch { data = undefined; }
    }
    const detail = (data as { detail?: unknown } | undefined)?.detail;
    if (typeof detail === 'string') return detail;
    if (Array.isArray(detail)) {
        const msgs = detail.map((d) => (d as { msg?: unknown })?.msg).filter((m): m is string => typeof m === 'string');
        if (msgs.length) return msgs.join('; ');
    }
    if (resp?.status === 504) return 'Report rendering timed out';
    return 'Request failed';
}

function formFrom(v: CaseReportView): Form {
    const r = v.report;
    return {
        executive_summary: r.executive_summary ?? '',
        impact: r.impact ?? '',
        lessons_learned: r.lessons_learned ?? '',
        tlp: clampTlp(r.tlp, v.min_tlp),
    };
}

function sameForm(a: Form, b: Form): boolean {
    return (Object.keys(a) as (keyof Form)[]).every((k) => a[k] === b[k]);
}

/** The TLP marking: a stamp when read-only, a stamp-styled native select when it can change. */
function TlpSelect({ value, onChange, min, disabled, label }:
    { value: TLPLevel; onChange: (t: TLPLevel) => void; min: TLPLevel; disabled?: boolean; label: string }) {
    if (disabled) return <Stamp tone={tlpTone(value)} aria-label={`${label}: TLP:${TLP_LABEL[value]}`}>TLP:{TLP_LABEL[value]}</Stamp>;
    return (
        <select
            aria-label={label}
            value={value}
            onChange={(e) => onChange(e.target.value as TLPLevel)}
            className={cn('stamp h-8 cursor-pointer bg-panel hover:bg-raised', TONE_TEXT[tlpTone(value)])}
        >
            {TLP_ORDER.map((t) => {
                const below = tlpIndex(t) < tlpIndex(min);
                return (
                    <option key={t} value={t} disabled={below}
                        title={below ? `Case IOCs require ≥ ${TLP_LABEL[min]}` : undefined}>
                        TLP:{TLP_LABEL[t]}
                    </option>
                );
            })}
        </select>
    );
}

/** Chart preview: fetched with auth as a blob and shown via an object URL (never injected inline). */
function ChartPreview({ caseId, name, version, alt }: { caseId: number; name: 'lifecycle' | 'timeline'; version: string; alt: string }) {
    const [state, setState] = useState<{ key: string; url: string | null; failed: boolean } | null>(null);
    const key = `${caseId}:${name}:${version}`;
    useEffect(() => {
        let url: string | null = null;
        let cancelled = false;
        api.get(`/cases/${caseId}/report/charts/${name}.svg`, { responseType: 'blob' })
            .then((resp) => {
                if (cancelled) return;
                url = URL.createObjectURL(resp.data as Blob);
                setState({ key, url, failed: false });
            })
            .catch(() => { if (!cancelled) setState({ key, url: null, failed: true }); });
        return () => {
            cancelled = true;
            if (url) URL.revokeObjectURL(url);
        };
    }, [caseId, name, key]);

    const current = state?.key === key ? state : null;
    return (
        <div className="rounded-md border border-line bg-panel p-3 overflow-hidden">
            {current?.url ? (
                <img src={current.url} alt={alt} style={{ maxWidth: '100%', height: 'auto' }} className="block mx-auto" />
            ) : (
                current?.failed ? <div className="py-10 text-center text-xs text-fg-3">Chart unavailable</div> : <Skeleton rows={1} className="py-6" />
            )}
        </div>
    );
}

/** Narrative textarea that accepts pasted / dropped images (uploaded inline, inserted as markdown). */
function ReportTextarea({ caseId, id, rows, readOnly, value, onChange, placeholder }: {
    caseId: number; id: string; rows: number; readOnly: boolean; value: string; onChange: (v: string) => void; placeholder: string;
}) {
    const ref = useRef<HTMLTextAreaElement>(null);
    const paste = usePasteImage(caseId, () => ref.current,
        (f, t) => value.includes(f) && (onChange(value.replace(f, () => t)), true));
    return (
        <>
            <Textarea
                ref={ref}
                id={id}
                rows={rows}
                maxLength={TEXT_MAX}
                readOnly={readOnly}
                value={value}
                onChange={(e) => onChange(e.target.value)}
                onPaste={readOnly ? undefined : paste.onPaste}
                onDrop={readOnly ? undefined : paste.onDrop}
                placeholder={placeholder}
                className={cn('resize-y', readOnly && 'bg-surface text-fg-2')}
            />
            {paste.error && <p role="alert" className="mt-1 text-xs text-severity-critical">{paste.error}</p>}
        </>
    );
}

export default function CaseReport({ caseId, onDirtyChange }: { caseId: number; onDirtyChange?: (dirty: boolean) => void }) {
    const queryClient = useQueryClient();
    const canWrite = useCanRun();
    const queryKey = ['case-report', caseId];

    const { data, isLoading, isError } = useQuery({
        queryKey,
        queryFn: async () => (await api.get(`/cases/${caseId}/report`)).data as CaseReportView,
    });

    const [edited, setEdited] = useState<Form | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [notice, setNotice] = useState<string | null>(null);
    const [confirmDraft, setConfirmDraft] = useState(false);
    const [exportTlp, setExportTlp] = useState<TLPLevel | null>(null);
    const [full, setFull] = useState(false);
    const [exporting, setExporting] = useState<'pdf' | 'json' | null>(null);
    const exportingRef = useRef(false);
    // Edit lock: 'pending' until the claim resolves, 'held' when ours, otherwise the email of the current holder.
    const [lock, setLock] = useState<'pending' | 'held' | { by: string }>('pending');
    const [takingOver, setTakingOver] = useState(false);
    const [conflict, setConflict] = useState(false);
    const versionRef = useRef<string | null>(null);
    const runRef = useRef<LockRun | null>(null);
    const holding = lock === 'held';

    const baseline = data ? formFrom(data) : null;
    const form = edited ?? baseline;
    const dirty = !!(edited && baseline && !sameForm(edited, baseline));

    useEffect(() => { onDirtyChange?.(dirty); }, [dirty, onDirtyChange]);
    useEffect(() => () => onDirtyChange?.(false), [onDirtyChange]);
    useEffect(() => {
        if (!dirty) return;
        const onBeforeUnload = (e: BeforeUnloadEvent) => { e.preventDefault(); e.returnValue = ''; };
        window.addEventListener('beforeunload', onBeforeUnload);
        return () => window.removeEventListener('beforeunload', onBeforeUnload);
    }, [dirty]);

    // Each lock-effect run owns a token; a cancelled run never touches state or the shared lock after a newer run exists.
    const claim = async (takeover: boolean, run: LockRun | null) => {
        if (!run) return false;
        try {
            const r = (await api.post(`/cases/${caseId}/report/lock`, { takeover })).data as LockResp;
            run.held = true;
            if (!run.cancelled) { versionRef.current = r.updated_at; setLock('held'); }
            return true;
        } catch (e) {
            run.held = false;
            if (run.cancelled) return false;
            const by = lockHolder(e);
            if (by !== null) setLock({ by });
            else setError(await errorDetail(e));
            return false;
        }
    };

    // Release only when no newer run took over (StrictMode remount) and this run holds the lock.
    const release = (run: LockRun) => {
        if (runRef.current !== null || !run.held) return;
        run.held = false;
        api.delete(`/cases/${caseId}/report/lock`).catch(() => undefined);
    };

    useEffect(() => {
        if (!canWrite) return;
        const run: LockRun = { cancelled: false, held: false };
        runRef.current = run;
        // eslint-disable-next-line react-hooks/set-state-in-effect -- async claim; state is set after the request resolves
        claim(false, run).then((ok) => {
            if (!ok) return;
            if (run.cancelled) { setTimeout(() => release(run), 0); return; }
            queryClient.invalidateQueries({ queryKey });
        });
        const timer = window.setInterval(() => { if (run.held && !run.cancelled) void claim(false, run); }, LOCK_RENEW_MS);
        return () => {
            run.cancelled = true;
            runRef.current = null;
            window.clearInterval(timer);
            // Deferred so a StrictMode remount (which sets runRef synchronously) cancels the release.
            setTimeout(() => release(run), 0);
        };
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [caseId, canWrite]);

    const takeOver = async () => {
        setTakingOver(true);
        if (await claim(true, runRef.current)) {
            setEdited(null);
            setConflict(false);
            setError(null);
            await queryClient.invalidateQueries({ queryKey });
        }
        setTakingOver(false);
    };

    const update = (patch: Partial<Form>) => {
        if (!form) return;
        setEdited({ ...form, ...patch });
        setNotice(null);
    };

    const save = useMutation({
        mutationFn: async () => {
            if (!form || !data) throw new Error('not loaded');
            const body: Record<string, unknown> = { tlp: form.tlp, base_updated_at: versionRef.current };
            for (const { key } of TEXT_FIELDS) body[key] = form[key] === '' ? null : form[key];
            return (await api.put(`/cases/${caseId}/report`, body)).data as CaseReportView;
        },
        onSuccess: (view) => {
            versionRef.current = view.report.updated_at;
            setConflict(false);
            queryClient.setQueryData(queryKey, view);
            setEdited(null);
            setError(null);
            setNotice('Report saved');
            queryClient.invalidateQueries({ queryKey: ['audit-logs', 'case', String(caseId)] });
        },
        onError: async (e) => {
            setNotice(null);
            if (isVersionConflict(e)) { setConflict(true); setError(null); return; }
            setError(await errorDetail(e));
        },
    });

    const draft = useMutation({
        mutationFn: async () => (await api.post(`/cases/${caseId}/report/draft`)).data as ReportDraft,
        onSuccess: (d) => {
            setConfirmDraft(false);
            setError(null);
            update({ executive_summary: d.executive_summary, impact: d.impact, lessons_learned: d.lessons_learned });
            setNotice('AI draft inserted. Review it, then save.');
        },
        onError: async (e) => { setConfirmDraft(false); setNotice(null); setError(await errorDetail(e)); },
    });

    const requestDraft = () => {
        if (!form || draft.isPending) return;
        if (TEXT_FIELDS.some(({ key }) => form[key].trim() !== '')) setConfirmDraft(true);
        else draft.mutate();
    };

    const runExport = async (format: 'pdf' | 'json') => {
        if (exportingRef.current || !data) return;
        exportingRef.current = true;
        setExporting(format);
        setError(null);
        try {
            const tlp = clampTlp(exportTlp ?? data.report.tlp, data.min_tlp);
            const resp = await api.get(`/cases/${caseId}/report/export`, {
                params: { format, full, tlp },
                responseType: 'blob',
            });
            const cd: string = resp.headers['content-disposition'] || '';
            const m = /filename="([^"]+)"/i.exec(cd);
            const name = m ? m[1] : `case-${caseId}-report.${format}`;
            const url = URL.createObjectURL(resp.data as Blob);
            const link = document.createElement('a');
            link.href = url;
            link.download = name;
            document.body.appendChild(link);
            link.click();
            link.remove();
            setTimeout(() => URL.revokeObjectURL(url), 1000);
            queryClient.invalidateQueries({ queryKey: ['audit-logs', 'case', String(caseId)] });
        } catch (e) {
            setError(await errorDetail(e));
        } finally {
            exportingRef.current = false;
            setExporting(null);
        }
    };

    if (isLoading) return <Skeleton rows={6} />;
    if (isError || !data || !form) {
        return <ErrorState message="Could not load the report." onRetry={() => queryClient.invalidateQueries({ queryKey })} />;
    }

    const ms = data.milestones;
    const readOnly = !canWrite || !holding;
    const chartVersion = data.report.updated_at ?? 'none';
    // Never below the floor, also when min_tlp rises after a refetch.
    const exportValue = clampTlp(exportTlp ?? data.report.tlp, data.min_tlp);
    const outOfOrder = ms.out_of_order ?? [];

    return (
        <div className="space-y-4">
            {/* Header */}
            <div className="p-4 rounded-md bg-panel border border-line flex flex-wrap items-center gap-3">
                <h2 className="text-sm font-semibold text-fg mr-auto">Incident report</h2>
                <label className="flex items-center gap-2 text-xs text-fg-3">
                    <span className="label-caps">TLP</span>
                    <TlpSelect label="Report TLP" value={form.tlp} min={data.min_tlp} disabled={readOnly}
                        onChange={(t) => update({ tlp: t })} />
                </label>
                {!readOnly && data.ai_available && (
                    confirmDraft ? (
                        <span className="inline-flex items-center gap-2 text-xs">
                            <span className="text-fg-2">Replace current text with an AI draft?</span>
                            <Button size="sm" onClick={() => draft.mutate()} disabled={draft.isPending}>
                                {draft.isPending ? 'Drafting…' : 'Replace'}
                            </Button>
                            <Button size="sm" variant="secondary" onClick={() => setConfirmDraft(false)} disabled={draft.isPending}>Cancel</Button>
                        </span>
                    ) : (
                        <Button size="sm" variant="secondary" onClick={requestDraft} disabled={draft.isPending}>
                            <Sparkles size={14} aria-hidden />{draft.isPending ? 'Drafting…' : 'Draft with AI'}
                        </Button>
                    )
                )}
                <div className="w-full text-xs text-fg-3">
                    {data.report.updated_at
                        ? <>Last saved by {data.report.updated_by_email ?? 'unknown'} · {relTime(data.report.updated_at)}</>
                        : 'Not saved yet'}
                    {dirty && <span className="ml-2 font-medium text-severity-medium">· Unsaved changes</span>}
                </div>
            </div>

            {canWrite && typeof lock === 'object' && (
                <div role="status" className="flex flex-wrap items-center gap-3 text-xs text-fg bg-severity-medium/10 border border-severity-medium/30 rounded-md px-3 py-2">
                    <AlertTriangle size={14} className="shrink-0 text-severity-medium" aria-hidden />
                    <span className="mr-auto">Being edited by {lock.by}</span>
                    <Button size="sm" variant="secondary" onClick={takeOver} disabled={takingOver}>
                        {takingOver ? 'Taking over…' : 'Take over'}
                    </Button>
                </div>
            )}
            {conflict && (
                <div role="alert" className="text-xs text-severity-critical bg-severity-critical/5 border border-severity-critical/30 rounded-md px-3 py-2">{CONFLICT_COPY}</div>
            )}
            {error && (
                <div role="alert" className="text-xs text-severity-critical bg-severity-critical/5 border border-severity-critical/30 rounded-md px-3 py-2">{error}</div>
            )}
            {notice && !error && (
                <div role="status" className="text-xs text-accent bg-accent/10 border border-accent/30 rounded-md px-3 py-2">{notice}</div>
            )}

            {/* Narrative */}
            <div className="p-4 rounded-md bg-panel border border-line space-y-4">
                {TEXT_FIELDS.map(({ key, label }) => (
                    <div key={key}>
                        <div className="flex items-baseline justify-between mb-1.5">
                            <label htmlFor={`report-${key}`} className="text-xs font-medium text-fg-2">{label}</label>
                            <span className={cn('num text-xs', form[key].length >= TEXT_MAX ? 'text-severity-critical' : 'text-fg-3')}>
                                {form[key].length.toLocaleString()} / {TEXT_MAX.toLocaleString()}
                            </span>
                        </div>
                        <ReportTextarea caseId={caseId} id={`report-${key}`} rows={key === 'executive_summary' ? 6 : 4}
                            readOnly={readOnly} value={form[key]} onChange={(v) => update({ [key]: v } as Partial<Form>)}
                            placeholder={readOnly ? '' : `${label}...`} />
                    </div>
                ))}
            </div>

            {/* Milestones */}
            <div className="p-4 rounded-md bg-panel border border-line space-y-3">
                <div className="flex items-baseline justify-between">
                    <h3 className="text-sm font-semibold text-fg">Milestones</h3>
                    <span className="text-xs text-fg-3">Shared with the case timeline · leave blank to use the computed value</span>
                </div>
                {outOfOrder.length > 0 && (
                    <div className="flex items-start gap-2 text-xs text-fg bg-severity-medium/10 border border-severity-medium/30 rounded-md px-3 py-2">
                        <AlertTriangle size={14} className="mt-px shrink-0 text-severity-medium" aria-hidden />
                        <span>
                            Milestones are out of order ({DURATIONS.filter((d) => outOfOrder.includes(d.key)).map((d) => d.span).join(', ')}).
                            The affected durations are reported as unknown.
                        </span>
                    </div>
                )}
                <MilestonesEditor key={JSON.stringify(ms)} caseId={caseId} milestones={ms} canEdit={!readOnly} />
                <div className="flex flex-wrap gap-x-6 gap-y-1 text-xs text-fg-3 pt-1">
                    {DURATIONS.map((d) => (
                        <span key={d.key}>{d.label}: <span className="num text-fg">{fmtDuration(ms[d.key])}</span></span>
                    ))}
                </div>
            </div>

            {!readOnly && (
                <div className="flex items-center justify-end gap-3">
                    {dirty && (
                        <Button variant="secondary" onClick={() => { setEdited(null); setError(null); setNotice(null); }} disabled={save.isPending}>
                            Discard changes
                        </Button>
                    )}
                    <Button onClick={() => save.mutate()} disabled={!dirty || save.isPending}>
                        <Save size={14} aria-hidden />{save.isPending ? 'Saving…' : 'Save report'}
                    </Button>
                </div>
            )}

            {/* Previews */}
            <div className="space-y-3">
                <h3 className="text-sm font-semibold text-fg">Preview</h3>
                <ChartPreview caseId={caseId} name="lifecycle" version={chartVersion} alt="Incident lifecycle chart" />
                <ChartPreview caseId={caseId} name="timeline" version={chartVersion} alt="Incident timeline chart" />
            </div>

            {/* Export */}
            {canWrite && (
                <div className="p-4 rounded-md bg-panel border border-line flex flex-wrap items-center gap-3">
                    <h3 className="text-sm font-semibold text-fg mr-auto">Export</h3>
                    <label className="flex items-center gap-2 text-xs text-fg-2 cursor-pointer">
                        <input type="checkbox" checked={full} onChange={(e) => setFull(e.target.checked)} />
                        Include full timeline &amp; audit trail
                    </label>
                    <label className="flex items-center gap-2 text-xs text-fg-3">
                        <span className="label-caps">TLP</span>
                        <TlpSelect label="Export TLP" value={exportValue} min={data.min_tlp} onChange={setExportTlp} />
                    </label>
                    <Button size="sm" onClick={() => runExport('pdf')} disabled={!!exporting}>
                        <FileDown size={14} aria-hidden />{exporting === 'pdf' ? 'Exporting…' : 'Export PDF'}
                    </Button>
                    <Button size="sm" variant="secondary" onClick={() => runExport('json')} disabled={!!exporting}>
                        <FileJson size={14} aria-hidden />{exporting === 'json' ? 'Exporting…' : 'Export JSON'}
                    </Button>
                    {dirty && <p className="w-full text-xs text-severity-medium">Exports use the last saved report. Save first to include your changes.</p>}
                    <p className="w-full text-xs text-fg-3">Cover logo, colors and disclaimer come from Settings → Organization → Report branding.</p>
                </div>
            )}
        </div>
    );
}
