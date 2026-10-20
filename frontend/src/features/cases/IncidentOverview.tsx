import { useState } from 'react';
import { Flag } from 'lucide-react';

import type { Case } from '../../types';
import { formatDateTime } from '../../utils/datetime';

const STATUS_COLOR: Record<string, string> = {
    new: 'var(--color-lime)', open: 'var(--color-accent)', in_progress: 'var(--color-magenta)',
    pending: 'var(--color-severity-medium)', resolved: 'var(--color-severity-low)', closed: 'var(--color-fg-3)',
};
const TYPE_COLOR: Record<string, string> = {
    status_change: 'var(--color-accent)', containment: 'var(--color-severity-high)', remediation: 'var(--color-lime)',
    investigation: 'var(--color-magenta)', artifact_added: 'var(--color-severity-low)',
};
const STATUS_RE = /Status changed from (\w+) to (\w+)/;
const ROWS = 4; // flag heights, staggered like a plan chart

function duration(ms: number): string {
    const m = Math.max(0, Math.round(ms / 60000));
    if (m < 60) return `${m}m`;
    const h = Math.floor(m / 60);
    if (h < 48) return `${h}h${m % 60 ? ` ${m % 60}m` : ''}`;
    return `${Math.floor(h / 24)}d${h % 24 ? ` ${h % 24}h` : ''}`;
}

const label = (s: string) => s.replace(/_/g, ' ');
const plain = (s: string) => s.replace(/@\[([^\]]+)\]\(user:\d+\)/g, '@$1').replace(/[#*_`>~[\]]/g, '').trim();

/** Horizontal "incident at a glance": status phases, milestones and every event on one time axis. */
export default function IncidentOverview({ caseData, tz }: { caseData: Case; tz?: string | null }) {
    const [now] = useState(() => Date.now()); // "now" for open cases, fixed per mount
    const events = [...(caseData.timeline_events ?? [])].sort((a, b) => +new Date(a.created_at) - +new Date(b.created_at));
    const start = +new Date(caseData.created_at);
    const resolved = caseData.resolved_at ? +new Date(caseData.resolved_at) : null;
    const last = events.length ? +new Date(events[events.length - 1].created_at) : start;
    // Resolved cases end at resolution; later follow-ups are pinned to the right edge instead of stretching the axis.
    const end = Math.max(resolved ?? Math.max(now, last), start + 60000);
    const pos = (t: number) => `${((Math.min(t, end) - start) / (end - start)) * 100}%`;
    const ack = caseData.acknowledged_at ? +new Date(caseData.acknowledged_at) : null;

    // Status phases: initial status runs until the first change, then each change opens the next one.
    const changes = events.flatMap((e) => {
        const m = e.event_type === 'status_change' ? STATUS_RE.exec(e.content) : null;
        return m ? [{ at: +new Date(e.created_at), from: m[1], to: m[2] }] : [];
    });
    let phases: { status: string; from: number; to: number }[] = [];
    if (changes.length) {
        let cur = { status: changes[0].from, from: start };
        for (const c of changes) {
            phases.push({ ...cur, to: c.at });
            cur = { status: c.to, from: c.at };
        }
        phases.push({ ...cur, to: end });
    } else {
        // No parsable status history: derive phases from the case's own timestamps.
        if (ack) phases.push({ status: 'new', from: start, to: ack });
        phases.push({ status: resolved ? 'in_progress' : caseData.status, from: ack ?? start, to: end });
    }
    phases = phases.filter((p) => Math.min(p.to, end) > p.from);

    const flagged = events.filter((e) => e.event_type !== 'comment' && e.event_type !== 'guest_comment');
    const comments = events.length - flagged.length;

    const stats: [string, string][] = [
        ['Total duration', `${duration((resolved ?? now) - start)}${resolved ? '' : ' · ongoing'}`],
        ['Time to acknowledge', ack ? duration(ack - start) : '—'],
        ['Time to resolve', resolved ? duration(resolved - start) : '—'],
        ['Actions · comments', `${flagged.length} · ${comments}`],
    ];
    const after = (t: string) => (resolved && +new Date(t) > resolved ? ` (${duration(+new Date(t) - resolved)} after resolution)` : '');
    const jump = (id: number) => document.getElementById(`event-${id}`)?.scrollIntoView({ behavior: 'smooth', block: 'start' });

    return (
        <section aria-label="Incident at a glance" className="glass-panel rounded-xl border border-line bg-panel p-4 space-y-4">
            <div className="flex flex-wrap items-baseline justify-between gap-2">
                <h3 className="text-sm font-semibold text-fg">Incident at a glance</h3>
                <span className="text-[11px] font-mono text-muted">
                    {formatDateTime(caseData.created_at, tz)} → {resolved ? formatDateTime(caseData.resolved_at, tz) : 'now'}
                </span>
            </div>

            <dl className="grid grid-cols-2 sm:grid-cols-4 gap-2">
                {stats.map(([k, v]) => (
                    <div key={k} className="rounded-lg bg-raised px-3 py-2">
                        <dt className="text-[10px] uppercase tracking-wide text-muted">{k}</dt>
                        <dd className="text-sm font-semibold text-fg font-mono">{v}</dd>
                    </div>
                ))}
            </dl>

            {/* ponytail: linear time scale; bursts of events crowd together, switch to a log/ordinal scale if that hurts */}
            <div className="overflow-x-auto">
                <div className="relative min-w-[640px] px-2">
                    {/* Flags: non-comment actions, staggered over ROWS heights, pole down to the axis */}
                    <div className="relative" style={{ height: ROWS * 26 + 8 }}>
                        {flagged.map((e, i) => {
                            const row = i % ROWS;
                            const color = TYPE_COLOR[e.event_type] ?? 'var(--color-fg-3)';
                            const left = (+new Date(e.created_at) - start) / (end - start);
                            return (
                                <button key={e.id} type="button" onClick={() => jump(e.id)}
                                    title={`${formatDateTime(e.created_at, tz)} · ${label(e.event_type)}${after(e.created_at)}\n${plain(e.content)}`}
                                    className="absolute bottom-0 flex items-start gap-1 group/flag text-left"
                                    style={left > 0.7 ? { right: `calc(100% - ${pos(+new Date(e.created_at))})`, top: row * 26, flexDirection: 'row-reverse' } : { left: pos(+new Date(e.created_at)), top: row * 26 }}>
                                    <span className="absolute top-0 bottom-0 w-px bg-line-strong" style={left > 0.7 ? { right: 0 } : { left: 0 }} />
                                    <Flag size={14} className="shrink-0 relative" style={{ color, fill: color }} />
                                    <span className="text-[11px] leading-4 text-fg-2 group-hover/flag:text-fg max-w-[180px] truncate">
                                        {plain(e.content).slice(0, 80) || label(e.event_type)}
                                    </span>
                                </button>
                            );
                        })}
                    </div>

                    {/* Axis with a dot per event (comments included) */}
                    <div className="relative h-3 rounded-full bg-raised-2">
                        {events.map((e) => (
                            <button key={e.id} type="button" onClick={() => jump(e.id)} aria-label={`${label(e.event_type)} at ${formatDateTime(e.created_at, tz)}`}
                                title={`${formatDateTime(e.created_at, tz)} · ${label(e.event_type)}${after(e.created_at)}`}
                                className="absolute top-1/2 -translate-x-1/2 -translate-y-1/2 size-2.5 rounded-full ring-2 ring-panel hover:scale-150 transition-transform"
                                style={{ left: pos(+new Date(e.created_at)), background: TYPE_COLOR[e.event_type] ?? 'var(--color-fg-3)' }} />
                        ))}
                        {ack && <span title={`Acknowledged ${formatDateTime(caseData.acknowledged_at, tz)}`} className="absolute -top-1 bottom-[-4px] w-0.5 bg-lime" style={{ left: pos(ack) }} />}
                    </div>

                    {/* Status phases with their durations */}
                    <div className="relative h-11 mt-2">
                        {phases.map((p, i) => (
                            <div key={i} className="absolute top-0 h-full overflow-hidden border-r border-panel px-1.5 py-1"
                                style={{ left: pos(p.from), width: `${((Math.min(p.to, end) - p.from) / (end - start)) * 100}%`, background: `color-mix(in srgb, ${STATUS_COLOR[p.status] ?? 'var(--color-fg-3)'} 22%, transparent)` }}
                                title={`${label(p.status)} · ${duration(p.to - p.from)}`}>
                                <div className="text-[10px] uppercase tracking-wide text-fg-2 truncate">{label(p.status)}</div>
                                <div className="text-xs font-semibold font-mono truncate" style={{ color: STATUS_COLOR[p.status] ?? 'var(--color-fg-3)' }}>{duration(p.to - p.from)}</div>
                            </div>
                        ))}
                    </div>

                    {/* Spans: response and resolution brackets */}
                    <div className="relative h-5 mt-2 text-[10px] font-mono">
                        {ack && <Span from="0%" to={pos(ack)} color="var(--color-lime)" text={`respond ${duration(ack - start)}`} />}
                        <Span from={ack ? pos(ack) : '0%'} to={pos(resolved ?? end)} color="var(--color-accent)"
                            text={resolved ? `resolve ${duration(resolved - (ack ?? start))}` : 'in progress →'} />
                    </div>
                </div>
            </div>

            <div className="flex flex-wrap gap-3 text-[10px] text-muted">
                {Object.entries(TYPE_COLOR).map(([t, c]) => (
                    <span key={t} className="inline-flex items-center gap-1"><span className="size-2 rounded-full" style={{ background: c }} />{label(t)}</span>
                ))}
                <span className="inline-flex items-center gap-1"><span className="size-2 rounded-full bg-fg-3" />comment / other</span>
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
