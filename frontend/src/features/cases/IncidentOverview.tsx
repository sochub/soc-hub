import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Flag, History, Pencil } from 'lucide-react';

import { api } from '../../api/client';
import type { Case, CaseEvent, MilestoneKey } from '../../types';
import { formatDateTime } from '../../utils/datetime';
import MilestonesEditor from './MilestonesEditor';
import StatusHistoryEditor from './StatusHistoryEditor';
import { MILESTONES } from './milestones';
import { TONE_VAR, statusTone } from '../../components/ui';

// Phase colours follow the case-status tones used by StatusStamp everywhere else.
const statusColor = (s: string) => TONE_VAR[statusTone(s)];
const TYPE_COLOR: Record<string, string> = {
    status_change: 'var(--color-accent)', containment: 'var(--color-severity-high)', remediation: 'var(--color-lime)',
    investigation: 'var(--color-magenta)', artifact_added: 'var(--color-severity-low)',
    task: 'var(--color-severity-medium)', severity_change: 'var(--color-severity-critical)',
};
const MILESTONE_COLOR: Record<MilestoneKey, string> = {
    first_seen: 'var(--color-severity-critical)', detected: 'var(--color-accent)',
    contained: 'var(--color-severity-high)', recovered: 'var(--color-lime)',
};
const ALWAYS_SHOWN = new Set(['status_change', 'severity_change']); // they drive the phase bar
const STATUS_RE = /Status changed from (\w+) to (\w+)/;
const ROWS = 4; // flag heights, staggered like a plan chart
const ROW_H = 26;

function duration(ms: number): string {
    const m = Math.max(0, Math.round(ms / 60000));
    if (m < 60) return `${m}m`;
    const h = Math.floor(m / 60);
    if (h < 48) return `${h}h${m % 60 ? ` ${m % 60}m` : ''}`;
    return `${Math.floor(h / 24)}d${h % 24 ? ` ${h % 24}h` : ''}`;
}
const secs = (s: number | null | undefined) => (s === null || s === undefined || s < 0 ? '—' : duration(s * 1000));

const label = (s: string) => s.replace(/_/g, ' ');
const plain = (s: string) => s.replace(/@\[([^\]]+)\]\(user:\d+\)/g, '@$1').replace(/[#*_`>~[\]]/g, '').trim();

type Item = { key: string; at: number; text: string; type: string; color: string; open: () => void };

/** Horizontal "incident at a glance": milestones, status phases and the pinned events on one time axis. */
export default function IncidentOverview({ caseData, tz, canEdit, onOpenInvestigation }: {
    caseData: Case; tz?: string | null; canEdit?: boolean; onOpenInvestigation?: (eventId: number) => void;
}) {
    const [now] = useState(() => Date.now()); // "now" for open cases, fixed per mount
    const [editing, setEditing] = useState(false);
    const [fixingStatus, setFixingStatus] = useState(false);
    const { data: invEvents = [] } = useQuery({
        queryKey: ['case-events', caseData.id],
        queryFn: async () => (await api.get(`/cases/${caseData.id}/events`)).data as CaseEvent[],
    });
    const ms = caseData.milestones ?? null;

    const activity = [...(caseData.timeline_events ?? [])].sort((a, b) => +new Date(a.created_at) - +new Date(b.created_at));
    const jump = (id: number) => document.getElementById(`event-${id}`)?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    // Curated: pinned activity, status/severity changes, and investigation events pinned (flagged) to the glance.
    const items: Item[] = [
        ...activity.filter((e) => e.pinned || ALWAYS_SHOWN.has(e.event_type)).map((e) => ({
            key: `a${e.id}`, at: +new Date(e.created_at), text: plain(e.content).slice(0, 80) || label(e.event_type),
            type: label(e.event_type), color: TYPE_COLOR[e.event_type] ?? 'var(--color-fg-3)', open: () => jump(e.id),
        })),
        ...invEvents.filter((e) => e.flagged).map((e) => ({
            key: `i${e.id}`, at: +new Date(e.event_date), text: e.title, type: label(e.category),
            color: TYPE_COLOR.investigation, open: () => onOpenInvestigation?.(e.id),
        })),
    ].sort((a, b) => a.at - b.at);

    const created = +new Date(caseData.created_at);
    const declaredFirst = ms?.first_seen.source === 'declared' && ms.first_seen.at ? +new Date(ms.first_seen.at) : null;
    const start = Math.min(declaredFirst ?? created, created); // only a declared First seen stretches the axis
    const recoveredAt = ms?.recovered.at ? +new Date(ms.recovered.at) : (caseData.resolved_at ? +new Date(caseData.resolved_at) : null);
    const last = items.length ? items[items.length - 1].at : start;
    // Recovered cases end at recovery; later follow-ups are pinned to the right edge instead of stretching the axis.
    const end = Math.max(recoveredAt ?? Math.max(now, last), start + 60000);
    const clamp = (t: number) => Math.min(Math.max(t, start), end);
    const pos = (t: number) => `${((clamp(t) - start) / (end - start)) * 100}%`;
    const ack = caseData.acknowledged_at ? +new Date(caseData.acknowledged_at) : null;

    // Status phases: initial status runs until the first change, then each change opens the next one.
    const changes = activity.flatMap((e) => {
        const m = e.event_type === 'status_change' ? STATUS_RE.exec(e.content) : null;
        return m ? [{ at: +new Date(e.created_at), from: m[1], to: m[2] }] : [];
    });
    let phases: { status: string; from: number; to: number }[] = [];
    if (start < created) phases.push({ status: 'before_case', from: start, to: created });
    if (changes.length) {
        let cur = { status: changes[0].from, from: created };
        for (const c of changes) {
            phases.push({ ...cur, to: c.at });
            cur = { status: c.to, from: c.at };
        }
        phases.push({ ...cur, to: end });
    } else {
        // No parsable status history: derive phases from the case's own timestamps.
        if (ack) phases.push({ status: 'new', from: created, to: ack });
        phases.push({ status: caseData.resolved_at ? 'in_progress' : caseData.status, from: ack ?? created, to: end });
    }
    phases = phases.filter((p) => Math.min(p.to, end) > p.from);

    const markers = ms ? MILESTONES.flatMap(({ key, label: name }) => (ms[key].at
        ? [{ key, name, at: +new Date(ms[key].at as string), source: ms[key].source }] : [])) : [];

    const stats: [string, string][] = [
        ['Total duration', `${duration((recoveredAt ?? now) - start)}${recoveredAt ? '' : ' · ongoing'}`],
        ['Time to detect', secs(ms?.ttd_seconds)],
        ['Time to contain', secs(ms?.ttc_seconds)],
        ['Time to recover', secs(ms?.ttr_seconds)],
    ];
    const after = (t: number) => (recoveredAt && t > recoveredAt ? ` (${duration(t - recoveredAt)} after recovery)` : '');

    return (
        <section aria-label="Incident at a glance" className="rounded-md border border-line bg-panel p-4 space-y-4">
            <div className="flex flex-wrap items-baseline justify-between gap-2">
                <h2 className="text-sm font-semibold text-fg">Incident at a glance</h2>
                <div className="flex items-baseline gap-3">
                    <span className="text-xs num text-fg-3">
                        {formatDateTime(new Date(start), tz)} → {recoveredAt ? formatDateTime(new Date(recoveredAt), tz) : 'now'}
                    </span>
                    {canEdit && (
                        <button type="button" onClick={() => setFixingStatus((v) => !v)} aria-expanded={fixingStatus}
                            className="inline-flex items-center gap-1 text-xs text-accent hover:underline rounded-sm">
                            <History size={11} aria-hidden /> {fixingStatus ? 'Close' : 'Status history'}
                        </button>
                    )}
                    {ms && canEdit && (
                        <button type="button" onClick={() => setEditing((v) => !v)} aria-expanded={editing}
                            className="inline-flex items-center gap-1 text-xs text-accent hover:underline rounded-sm">
                            <Pencil size={11} aria-hidden /> {editing ? 'Close' : 'Edit milestones'}
                        </button>
                    )}
                </div>
            </div>

            {ms && editing && (
                <div className="rounded-md border border-line bg-surface p-3">
                    <MilestonesEditor key={JSON.stringify(ms)} caseId={caseData.id} milestones={ms} canEdit={!!canEdit}
                        onDone={() => setEditing(false)} />
                </div>
            )}

            {canEdit && fixingStatus && (
                <div className="rounded-md border border-line bg-surface p-3">
                    <StatusHistoryEditor caseData={caseData} tz={tz} onDone={() => setFixingStatus(false)} />
                </div>
            )}

            <dl className="grid grid-cols-2 sm:grid-cols-4 gap-2">
                {stats.map(([k, v]) => (
                    <div key={k} className="rounded-md bg-raised px-3 py-2">
                        <dt className="label-caps">{k}</dt>
                        <dd className="text-sm font-semibold text-fg num">{v}</dd>
                    </div>
                ))}
            </dl>

            {/* ponytail: linear time scale; bursts of events crowd together, switch to a log/ordinal scale if that hurts */}
            <div className="overflow-x-auto">
                <div className="relative min-w-[640px] px-2">
                    {/* Flags: the curated items, staggered over ROWS heights, pole down to the axis */}
                    {/* Only as tall as the rows in use, so a quiet case doesn't leave an empty band above the axis. */}
                    <div className="relative" style={{ height: items.length ? Math.min(items.length, ROWS) * ROW_H + 8 : 0 }}>
                        {items.map((e, i) => {
                            const row = i % ROWS;
                            const left = (clamp(e.at) - start) / (end - start);
                            return (
                                <button key={e.key} type="button" onClick={e.open}
                                    title={`${formatDateTime(new Date(e.at), tz)} · ${e.type}${after(e.at)}\n${e.text}`}
                                    className="absolute bottom-0 flex items-start gap-1 group/flag text-left"
                                    style={left > 0.7 ? { right: `calc(100% - ${pos(e.at)})`, top: row * ROW_H, flexDirection: 'row-reverse' } : { left: pos(e.at), top: row * ROW_H }}>
                                    <span className="absolute top-0 bottom-0 w-px bg-line-strong" style={left > 0.7 ? { right: 0 } : { left: 0 }} />
                                    <Flag size={14} className="shrink-0 relative" style={{ color: e.color, fill: e.color }} />
                                    <span className="text-xs leading-4 text-fg-2 group-hover/flag:text-fg max-w-[180px] truncate">{e.text}</span>
                                </button>
                            );
                        })}
                    </div>

                    {/* Axis with a dot per item and a marker per milestone */}
                    <div className="relative h-3 rounded-full bg-raised-2">
                        {items.map((e) => (
                            <button key={e.key} type="button" onClick={e.open} aria-label={`${e.type} at ${formatDateTime(new Date(e.at), tz)}`}
                                title={`${formatDateTime(new Date(e.at), tz)} · ${e.type}${after(e.at)}`}
                                className="absolute top-1/2 -translate-x-1/2 -translate-y-1/2 size-2.5 rounded-full ring-2 ring-panel hover:scale-150 transition-transform"
                                style={{ left: pos(e.at), background: e.color }} />
                        ))}
                        {markers.map((m) => (
                            <span key={m.key} title={`${m.name} · ${formatDateTime(new Date(m.at), tz)} · ${m.source}`}
                                className="absolute -top-1 bottom-[-4px] w-0.5" style={{ left: pos(m.at), background: MILESTONE_COLOR[m.key] }} />
                        ))}
                    </div>

                    {/* Status phases with their durations */}
                    <div className="relative h-11 mt-2">
                        {phases.map((p, i) => (
                            <div key={i} className="absolute top-0 h-full overflow-hidden border-r border-panel px-1.5 py-1"
                                style={{ left: pos(p.from), width: `${((Math.min(p.to, end) - p.from) / (end - start)) * 100}%`, background: `color-mix(in srgb, ${statusColor(p.status)} 22%, transparent)` }}
                                title={`${label(p.status)} · ${duration(p.to - p.from)}`}>
                                <div className="label-caps text-fg-2 truncate">{label(p.status)}</div>
                                <div className="text-xs font-semibold num truncate" style={{ color: statusColor(p.status) }}>{duration(p.to - p.from)}</div>
                            </div>
                        ))}
                    </div>

                    {/* Spans: response and resolution brackets */}
                    <div className="relative h-5 mt-2 text-xs num">
                        {ack && <Span from={pos(created)} to={pos(ack)} color="var(--color-lime)" text={`respond ${duration(ack - created)}`} />}
                        <Span from={pos(ack ?? created)} to={pos(recoveredAt ?? end)} color="var(--color-accent)"
                            text={recoveredAt ? `resolve ${duration(recoveredAt - (ack ?? created))}` : 'in progress →'} />
                    </div>
                </div>
            </div>

            <div className="flex flex-wrap gap-3 text-xs text-fg-3">
                {Object.entries(TYPE_COLOR).map(([t, c]) => (
                    <span key={t} className="inline-flex items-center gap-1"><span className="size-2 rounded-full" style={{ background: c }} />{label(t)}</span>
                ))}
                {MILESTONES.map(({ key, label: name }) => (
                    <span key={key} className="inline-flex items-center gap-1"><span className="h-2 w-0.5" style={{ background: MILESTONE_COLOR[key] }} />{name}</span>
                ))}
                <span>· pin entries in the feed below to show them here</span>
            </div>
        </section>
    );
}

function Span({ from, to, color, text }: { from: string; to: string; color: string; text: string }) {
    return (
        <div className="absolute top-0 h-full flex items-center" style={{ left: from, width: `calc(${to} - ${from})` }}>
            <div className="absolute inset-x-0 top-1/2 h-px" style={{ background: color }} />
            <div className="absolute left-0 top-1 bottom-1 w-px" style={{ background: color }} />
            <div className="absolute right-0 top-1 bottom-1 w-px" style={{ background: color }} />
            <span className="relative mx-auto px-1.5 bg-panel whitespace-nowrap" style={{ color }}>{text}</span>
        </div>
    );
}
