import { useEffect, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { AlertTriangle, FileDown, FileJson, Save, Sparkles } from 'lucide-react';
import { api } from '../../api/client';
import { cn } from '../../lib/utils';
import { useCanRun } from '../enrichment/useCanRun';
import { btnPrimary, btnSecondary } from '../../components/layout/Modal';
import type { CaseReportView, MilestoneKey, ReportDraft, TLPLevel } from '../../types';

const TEXT_MAX = 20000;
const TLP_ORDER: TLPLevel[] = ['white', 'green', 'amber', 'red'];
const TLP_LABEL: Record<TLPLevel, string> = { white: 'CLEAR', green: 'GREEN', amber: 'AMBER', red: 'RED' };
const TLP_STYLE: Record<TLPLevel, { backgroundColor: string; color: string }> = {
    white: { backgroundColor: '#FFFFFF', color: '#000000' },
    green: { backgroundColor: '#33FF00', color: '#000000' },
    amber: { backgroundColor: '#FFC000', color: '#000000' },
    red: { backgroundColor: '#FF2B2B', color: '#FFFFFF' },
};

const TEXT_FIELDS = [
    { key: 'executive_summary', label: 'Executive summary' },
    { key: 'impact', label: 'Impact' },
    { key: 'lessons_learned', label: 'Lessons learned' },
] as const;
type TextKey = typeof TEXT_FIELDS[number]['key'];

const MILESTONES: { key: MilestoneKey; label: string }[] = [
    { key: 'first_seen', label: 'First seen' },
    { key: 'detected', label: 'Detected' },
    { key: 'contained', label: 'Contained' },
    { key: 'recovered', label: 'Recovered' },
];
const DURATIONS = [
    { key: 'ttd_seconds', label: 'Time to detect', span: 'first seen → detected' },
    { key: 'ttc_seconds', label: 'Time to contain', span: 'detected → contained' },
    { key: 'ttr_seconds', label: 'Time to recover', span: 'contained → recovered' },
] as const;

type Form = Record<TextKey, string> & Record<MilestoneKey, string> & { tlp: TLPLevel };

const tlpIndex = (t: TLPLevel) => TLP_ORDER.indexOf(t);
const clampTlp = (t: TLPLevel, min: TLPLevel): TLPLevel => (tlpIndex(t) < tlpIndex(min) ? min : t);

/** UTC ISO → value for a datetime-local input (local wall time, minute precision). */
function isoToLocal(iso: string | null): string {
    if (!iso) return '';
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return '';
    return new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
}

function fmtLocal(iso: string | null): string {
    if (!iso) return 'unknown';
    const d = new Date(iso);
    return Number.isNaN(d.getTime()) ? 'unknown' : d.toLocaleString();
}

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
        first_seen: isoToLocal(r.first_seen_at),
        detected: isoToLocal(r.detected_at),
        contained: isoToLocal(r.contained_at),
        recovered: isoToLocal(r.recovered_at),
    };
}

function sameForm(a: Form, b: Form): boolean {
    return (Object.keys(a) as (keyof Form)[]).every((k) => a[k] === b[k]);
}

function TlpSelect({ value, onChange, min, disabled, label }:
    { value: TLPLevel; onChange: (t: TLPLevel) => void; min: TLPLevel; disabled?: boolean; label: string }) {
    return (
        <select
            aria-label={label}
            value={value}
            disabled={disabled}
            onChange={(e) => onChange(e.target.value as TLPLevel)}
            style={TLP_STYLE[value]}
            className="h-8 px-2 text-xs font-bold num border border-zinc-400 focus:outline-hidden focus:ring-1 focus:ring-accent-500 disabled:opacity-100 disabled:cursor-default"
        >
            {TLP_ORDER.map((t) => {
                const below = tlpIndex(t) < tlpIndex(min);
                return (
                    <option key={t} value={t} disabled={below} style={TLP_STYLE[t]}
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
        <div className="border border-zinc-200 bg-white p-3 overflow-hidden">
            {current?.url ? (
                <img src={current.url} alt={alt} style={{ maxWidth: '100%', height: 'auto' }} className="block mx-auto" />
            ) : (
                <div className="py-10 text-center text-xs text-zinc-400">{current?.failed ? 'Chart unavailable' : 'Loading chart...'}</div>
            )}
        </div>
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

    const update = (patch: Partial<Form>) => {
        if (!form) return;
        setEdited({ ...form, ...patch });
        setNotice(null);
    };

    const save = useMutation({
        mutationFn: async () => {
            if (!form || !data) throw new Error('not loaded');
            const body: Record<string, unknown> = { tlp: form.tlp };
            for (const { key } of TEXT_FIELDS) body[key] = form[key] === '' ? null : form[key];
            for (const { key } of MILESTONES) {
                const field = `${key}_at` as const;
                const local = form[key];
                // Untouched overrides keep their exact stored value (inputs are minute precision).
                if (local === isoToLocal(data.report[field])) body[field] = data.report[field];
                else body[field] = local ? new Date(local).toISOString() : null;
            }
            return (await api.put(`/cases/${caseId}/report`, body)).data as CaseReportView;
        },
        onSuccess: (view) => {
            queryClient.setQueryData(queryKey, view);
            setEdited(null);
            setError(null);
            setNotice('Report saved');
            queryClient.invalidateQueries({ queryKey: ['audit-logs', 'case', String(caseId)] });
        },
        onError: async (e) => { setNotice(null); setError(await errorDetail(e)); },
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

    if (isLoading) return <div className="text-center py-8 text-zinc-400">Loading report...</div>;
    if (isError || !data || !form) {
        return <div role="alert" className="text-xs text-red-700 bg-red-50 border border-red-200 rounded-sm px-3 py-2">Could not load the report.</div>;
    }

    const ms = data.milestones;
    const readOnly = !canWrite;
    const chartVersion = data.report.updated_at ?? 'none';
    // Never below the floor, also when min_tlp rises after a refetch.
    const exportValue = clampTlp(exportTlp ?? data.report.tlp, data.min_tlp);
    const outOfOrder = ms.out_of_order ?? [];

    return (
        <div className="space-y-4">
            {/* Header */}
            <div className="glass-panel p-4 rounded-lg bg-white border border-zinc-200 flex flex-wrap items-center gap-3">
                <h3 className="text-sm font-semibold text-zinc-700 mr-auto">Incident report</h3>
                <label className="flex items-center gap-2 text-xs text-zinc-500">
                    <span className="label-mono">TLP</span>
                    <TlpSelect label="Report TLP" value={form.tlp} min={data.min_tlp} disabled={readOnly}
                        onChange={(t) => update({ tlp: t })} />
                </label>
                {canWrite && data.ai_available && (
                    confirmDraft ? (
                        <span className="inline-flex items-center gap-2 text-xs">
                            <span className="text-zinc-600">Replace current text with an AI draft?</span>
                            <button onClick={() => draft.mutate()} disabled={draft.isPending}
                                className="px-2 py-1 bg-accent-600 text-white rounded-sm hover:bg-accent-700 disabled:opacity-50">
                                {draft.isPending ? 'Drafting...' : 'Replace'}
                            </button>
                            <button onClick={() => setConfirmDraft(false)} disabled={draft.isPending}
                                className="px-2 py-1 bg-zinc-100 text-zinc-700 rounded-sm hover:bg-zinc-200 disabled:opacity-50">Cancel</button>
                        </span>
                    ) : (
                        <button onClick={requestDraft} disabled={draft.isPending} className={btnSecondary}>
                            <Sparkles size={14} />{draft.isPending ? 'Drafting...' : 'Draft with AI'}
                        </button>
                    )
                )}
                <div className="w-full text-xs text-zinc-400">
                    {data.report.updated_at
                        ? <>Last saved by {data.report.updated_by_email ?? 'unknown'} · {relTime(data.report.updated_at)}</>
                        : 'Not saved yet'}
                    {dirty && <span className="ml-2 text-amber-700">· Unsaved changes</span>}
                </div>
            </div>

            {error && (
                <div role="alert" className="text-xs text-red-700 bg-red-50 border border-red-200 rounded-sm px-3 py-2">{error}</div>
            )}
            {notice && !error && (
                <div role="status" className="text-xs text-accent-700 bg-blue-50 border border-blue-200 rounded-sm px-3 py-2">{notice}</div>
            )}

            {/* Narrative */}
            <div className="glass-panel p-4 rounded-lg bg-white border border-zinc-200 space-y-4">
                {TEXT_FIELDS.map(({ key, label }) => (
                    <div key={key}>
                        <div className="flex items-baseline justify-between mb-1.5">
                            <label htmlFor={`report-${key}`} className="text-xs font-medium text-zinc-600">{label}</label>
                            <span className={cn('num text-[11px]', form[key].length >= TEXT_MAX ? 'text-red-700' : 'text-zinc-400')}>
                                {form[key].length.toLocaleString()} / {TEXT_MAX.toLocaleString()}
                            </span>
                        </div>
                        <textarea
                            id={`report-${key}`}
                            rows={key === 'executive_summary' ? 6 : 4}
                            maxLength={TEXT_MAX}
                            readOnly={readOnly}
                            value={form[key]}
                            onChange={(e) => update({ [key]: e.target.value } as Partial<Form>)}
                            placeholder={readOnly ? '' : `${label}...`}
                            className={cn('w-full text-sm border border-zinc-200 rounded-sm px-3 py-2 bg-white text-zinc-800 focus:outline-hidden focus:border-accent-600 resize-y',
                                readOnly && 'bg-zinc-50 text-zinc-700')}
                        />
                    </div>
                ))}
            </div>

            {/* Milestones */}
            <div className="glass-panel p-4 rounded-lg bg-white border border-zinc-200 space-y-3">
                <div className="flex items-baseline justify-between">
                    <h4 className="text-xs font-semibold text-zinc-600 uppercase tracking-wider">Milestones</h4>
                    <span className="text-[11px] text-zinc-400">Local time · leave blank to use the computed value</span>
                </div>
                {outOfOrder.length > 0 && (
                    <div className="flex items-start gap-2 text-xs text-amber-800 bg-amber-50 border border-amber-200 rounded-sm px-3 py-2">
                        <AlertTriangle size={14} className="mt-px shrink-0" />
                        <span>
                            Milestones are out of order ({DURATIONS.filter((d) => outOfOrder.includes(d.key)).map((d) => d.span).join(', ')}).
                            The affected durations are reported as unknown.
                        </span>
                    </div>
                )}
                <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
                    {MILESTONES.map(({ key, label }) => {
                        const m = ms[key];
                        const computedText = m.source === 'override' ? null : fmtLocal(m.at);
                        const hasOverride = form[key] !== '';
                        return (
                            <div key={key}>
                                <label htmlFor={`report-ms-${key}`} className="block text-xs font-medium text-zinc-600 mb-1.5">{label}</label>
                                <input
                                    id={`report-ms-${key}`}
                                    type="datetime-local"
                                    disabled={readOnly}
                                    value={form[key]}
                                    placeholder={computedText ?? ''}
                                    onChange={(e) => update({ [key]: e.target.value } as Partial<Form>)}
                                    className="w-full text-sm num border border-zinc-200 rounded-sm px-2 py-1.5 bg-white text-zinc-800 focus:outline-hidden focus:border-accent-600 disabled:bg-zinc-50"
                                />
                                <div className="mt-1 text-[11px] text-zinc-400 flex items-center gap-2 min-h-[16px]">
                                    {hasOverride ? (
                                        <>
                                            <span>Override</span>
                                            {!readOnly && (
                                                <button type="button" onClick={() => update({ [key]: '' } as Partial<Form>)}
                                                    className="text-accent-700 hover:underline">use computed</button>
                                            )}
                                        </>
                                    ) : (
                                        m.source === 'override' ? (
                                            // The saved value is an override, so the computed one is not known until the save.
                                            <span>Computed value shown after saving</span>
                                        ) : (
                                            <span title="Computed from case data">Computed: <span className="num">{computedText ?? 'unknown'}</span></span>
                                        )
                                    )}
                                </div>
                            </div>
                        );
                    })}
                </div>
                <div className="flex flex-wrap gap-x-6 gap-y-1 text-xs text-zinc-500 pt-1">
                    {DURATIONS.map((d) => (
                        <span key={d.key}>{d.label}: <span className="num text-zinc-800">{fmtDuration(ms[d.key])}</span></span>
                    ))}
                </div>
            </div>

            {canWrite && (
                <div className="flex items-center justify-end gap-3">
                    {dirty && (
                        <button onClick={() => { setEdited(null); setError(null); setNotice(null); }} disabled={save.isPending} className={btnSecondary}>
                            Discard changes
                        </button>
                    )}
                    <button onClick={() => save.mutate()} disabled={!dirty || save.isPending} className={btnPrimary}>
                        <Save size={14} />{save.isPending ? 'Saving...' : 'Save report'}
                    </button>
                </div>
            )}

            {/* Previews */}
            <div className="space-y-3">
                <h4 className="text-xs font-semibold text-zinc-600 uppercase tracking-wider">Preview</h4>
                <ChartPreview caseId={caseId} name="lifecycle" version={chartVersion} alt="Incident lifecycle chart" />
                <ChartPreview caseId={caseId} name="timeline" version={chartVersion} alt="Incident timeline chart" />
            </div>

            {/* Export */}
            {canWrite && (
                <div className="glass-panel p-4 rounded-lg bg-white border border-zinc-200 flex flex-wrap items-center gap-3">
                    <h4 className="text-xs font-semibold text-zinc-600 uppercase tracking-wider mr-auto">Export</h4>
                    <label className="flex items-center gap-2 text-xs text-zinc-600 cursor-pointer">
                        <input type="checkbox" checked={full} onChange={(e) => setFull(e.target.checked)} />
                        Include full timeline &amp; audit trail
                    </label>
                    <label className="flex items-center gap-2 text-xs text-zinc-500">
                        <span className="label-mono">TLP</span>
                        <TlpSelect label="Export TLP" value={exportValue} min={data.min_tlp} onChange={setExportTlp} />
                    </label>
                    <button onClick={() => runExport('pdf')} disabled={!!exporting} className={btnPrimary}>
                        <FileDown size={14} />{exporting === 'pdf' ? 'Exporting...' : 'Export PDF'}
                    </button>
                    <button onClick={() => runExport('json')} disabled={!!exporting} className={btnSecondary}>
                        <FileJson size={14} />{exporting === 'json' ? 'Exporting...' : 'Export JSON'}
                    </button>
                    {dirty && <p className="w-full text-[11px] text-amber-700">Exports use the last saved report. Save first to include your changes.</p>}
                </div>
            )}
        </div>
    );
}
