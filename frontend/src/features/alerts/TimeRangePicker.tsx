import { useEffect, useRef, useState } from 'react';
import { Clock } from 'lucide-react';
import { cn } from '../../lib/utils';
import { Button, Input, Label, Select } from '../../components/ui';
import { PRESETS, RANGE_RE, absoluteLabel, browserZone, rangeLabel, recentRanges, rememberRange } from './timeRange';

export type TimeRange = { range: string; from: string; to: string };
const REFRESH: [number, string][] = [[0, 'Off'], [30, 'Every 30 seconds'], [60, 'Every minute'], [300, 'Every 5 minutes']];
const item = 'w-full text-left px-2 py-1 rounded-sm text-sm text-fg-2 hover:bg-raised hover:text-fg';

/** One control for the received-time filter: quick presets, a relative "last N", an absolute from/to, the recently
 *  used ranges and auto-refresh. Always about when sochub received the alert, in the browser's timezone. */
export default function TimeRangePicker({ value, refresh, onChange, onRefresh }: {
    value: TimeRange; refresh: number; onChange: (v: TimeRange) => void; onRefresh: (seconds: number) => void;
}) {
    const [open, setOpen] = useState(false);
    const [n, setN] = useState('6');
    const [unit, setUnit] = useState('h');
    const [from, setFrom] = useState(value.from);
    const [to, setTo] = useState(value.to);
    const ref = useRef<HTMLDivElement>(null);
    useEffect(() => {
        if (!open) return;
        const onDown = (e: MouseEvent) => { if (!ref.current?.contains(e.target as Node)) setOpen(false); };
        document.addEventListener('mousedown', onDown);
        return () => document.removeEventListener('mousedown', onDown);
    }, [open]);

    const active = !!value.range || !!value.from || !!value.to;
    const label = value.range ? rangeLabel(value.range) : active ? absoluteLabel(value.from, value.to) : 'Any time';
    const apply = (v: TimeRange) => {
        if (v.range) rememberRange(v.range); else if (v.from || v.to) rememberRange(`${v.from}|${v.to}`);
        onChange(v); setOpen(false);
    };
    const relative = `${Number(n)}${unit}`;
    const absoluteOk = (!!from || !!to) && (!from || !to || new Date(from) < new Date(to));
    const recent = open ? recentRanges() : [];

    return (
        <div ref={ref} className="relative" onKeyDown={(e) => { if (e.key === 'Escape') setOpen(false); }}>
            <Button variant={active ? 'secondary' : 'ghost'} size="sm" aria-haspopup="dialog" aria-expanded={open}
                aria-label={`Received time: ${label}`} title="Filters by when sochub received the alert"
                onClick={() => { setFrom(value.from); setTo(value.to); setOpen((o) => !o); }}>
                <Clock size={13} aria-hidden />{label}{refresh > 0 && <span className="text-fg-3"> · auto</span>}
            </Button>
            {open && (
                <div role="dialog" aria-label="Received time range"
                    className="absolute right-0 z-30 mt-1 w-[min(22rem,calc(100vw-2rem))] rounded-lg border border-line bg-panel p-3 shadow-lg space-y-3">
                    <div>
                        <p className="label-caps mb-1">Quick ranges</p>
                        <div className="grid grid-cols-2 gap-x-2">
                            {PRESETS.map(([k, name]) => (
                                <button key={k} type="button" aria-pressed={value.range === k} onClick={() => apply({ range: k, from: '', to: '' })}
                                    className={cn(item, value.range === k && 'bg-raised text-fg')}>{name}</button>
                            ))}
                        </div>
                    </div>
                    <form className="flex items-end gap-2" onSubmit={(e) => { e.preventDefault(); if (RANGE_RE.test(relative)) apply({ range: relative, from: '', to: '' }); }}>
                        <div className="w-20"><Label htmlFor="range-n">Last</Label>
                            <Input id="range-n" inputSize="sm" type="number" min={1} max={9999} value={n} onChange={(e) => setN(e.target.value)} /></div>
                        <Select inputSize="sm" className="w-auto" aria-label="Unit" value={unit} onChange={(e) => setUnit(e.target.value)}>
                            <option value="m">minutes</option><option value="h">hours</option><option value="d">days</option>
                        </Select>
                        <Button type="submit" size="sm" variant="secondary" disabled={!RANGE_RE.test(relative)}>Apply</Button>
                    </form>
                    <form className="space-y-2" onSubmit={(e) => { e.preventDefault(); if (absoluteOk) apply({ range: '', from, to }); }}>
                        <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                            <div><Label htmlFor="range-from">From</Label>
                                <Input id="range-from" inputSize="sm" type="datetime-local" value={from} onChange={(e) => setFrom(e.target.value)} /></div>
                            <div><Label htmlFor="range-to">Before</Label>
                                <Input id="range-to" inputSize="sm" type="datetime-local" value={to} onChange={(e) => setTo(e.target.value)} /></div>
                        </div>
                        {!!from && !!to && !absoluteOk && <p role="alert" className="text-xs text-severity-critical">From must be earlier than Before.</p>}
                        <Button type="submit" size="sm" variant="secondary" disabled={!absoluteOk}>Apply dates</Button>
                    </form>
                    {recent.length > 0 && (
                        <div>
                            <p className="label-caps mb-1">Recently used</p>
                            {recent.map((r) => {
                                const [f, t] = r.includes('|') ? r.split('|') : ['', ''];
                                return (
                                    <button key={r} type="button" className={item}
                                        onClick={() => apply(r.includes('|') ? { range: '', from: f, to: t } : { range: r, from: '', to: '' })}>
                                        {r.includes('|') ? absoluteLabel(f, t) : rangeLabel(r)}
                                    </button>
                                );
                            })}
                        </div>
                    )}
                    <div className="flex items-end justify-between gap-2 border-t border-line pt-3">
                        <div><Label htmlFor="range-refresh">Auto-refresh</Label>
                            <Select id="range-refresh" inputSize="sm" className="w-auto" value={refresh} onChange={(e) => onRefresh(Number(e.target.value))}>
                                {REFRESH.map(([s, name]) => <option key={s} value={s}>{name}</option>)}
                            </Select></div>
                        <Button size="sm" variant="ghost" disabled={!active} onClick={() => { onChange({ range: '', from: '', to: '' }); setOpen(false); }}>Any time</Button>
                    </div>
                    <p className="text-xs text-fg-3">By when sochub received the alert, not the event time inside it. Times are in {browserZone()}.</p>
                </div>
            )}
        </div>
    );
}
