import { useEffect, useRef, useState, useMemo, useCallback } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { getPage } from '../../api/paging';
import {
    ShieldAlert, Database, Crosshair, X, ArrowUpRight, Search,
    SlidersHorizontal, ChevronLeft, Maximize2,
} from 'lucide-react';
import { cn } from '../../lib/utils';
import { useArtifactTypes } from './useArtifactTypes';
import { PageHeader, Button, Input, SeverityStamp, StatusStamp, Stamp, Badge, TONE_VAR, TONE_TEXT, severityTone, tlpTone } from '../../components/ui';

/* ------------------------------------------------------------------ *
 * Investigation Graph — cases ◉, artifacts ▢, IOCs ◇ and the links
 * between them, including dashed "value match" bridges where an IOC's
 * value equals an artifact's value (cross-case correlation).
 * ------------------------------------------------------------------ */

// Colours come from the theme tokens (CSS variables), applied through `style` so SVG resolves var().
const sevColor = (s?: string | null) => TONE_VAR[severityTone(s)];
const C = {
    caseFallback: TONE_VAR.accent,
    artifact: TONE_VAR.neutral,
    iocFallback: 'var(--color-magenta)',
    match: TONE_VAR.medium,
    focus: TONE_VAR.accent,
    link: 'var(--color-line-strong)',
    nodeRing: 'var(--color-panel)',
    selectedRing: 'var(--color-fg)',
    caseIcon: 'var(--color-panel)',
};
const SEVERITIES = ['critical', 'high', 'medium', 'low', 'info'];
const THREATS = ['critical', 'high', 'medium', 'low'];

interface GNode {
    id: string;
    kind: 'case' | 'artifact' | 'ioc';
    label: string;
    color: string;
    data: any;
    x: number; y: number; vx: number; vy: number;
}
interface GLink { source: string; target: string; match?: boolean }

const GRAPH_CAP = 500;

/** Without `caseId`: the whole tenant. With it: that case, its artifacts and IOCs, and whatever they link to (2 hops). */
export default function ArtifactMindMap({ caseId }: { caseId?: number } = {}) {
    const wrapRef = useRef<HTMLDivElement>(null);
    const [dims, setDims] = useState({ width: 900, height: 600 });

    // ---- data ----
    // The newest GRAPH_CAP of each kind; when a tenant has more, the header says so.
    const { data: casePage } = useQuery({
        queryKey: ['cases', 'all', 'graph'],
        queryFn: () => getPage<Record<string, unknown>>('/cases/', { limit: GRAPH_CAP }),
    });
    const { data: artifactPage } = useQuery({
        queryKey: ['artifacts', 'all', 'graph'],
        queryFn: () => getPage<Record<string, unknown>>('/artifacts/', { limit: GRAPH_CAP }),
    });
    const { data: iocPage } = useQuery({
        queryKey: ['iocs', 'all', 'graph'],
        queryFn: () => getPage<Record<string, unknown>>('/iocs/', { limit: GRAPH_CAP }),
    });
    const cases = useMemo(() => casePage?.items ?? [], [casePage]);
    const artifacts = useMemo(() => artifactPage?.items ?? [], [artifactPage]);
    const iocs = useMemo(() => iocPage?.items ?? [], [iocPage]);
    const truncated = ([['cases', casePage], ['artifacts', artifactPage], ['IOCs', iocPage]] as const)
        .filter(([, p]) => p && p.total > p.items.length)
        .map(([label, p]) => `${p!.items.length} of ${p!.total} ${label}`);

    // ---- filters ----
    // Wide screens start with the filter rail open; below md it overlays the graph and starts hidden.
    const [railOpen, setRailOpen] = useState(() => typeof window === 'undefined' || window.matchMedia('(min-width: 768px)').matches);
    const [showKind, setShowKind] = useState({ case: true, artifact: true, ioc: true });
    const [sevFilter, setSevFilter] = useState<Set<string>>(new Set());      // empty = all
    const [threatFilter, setThreatFilter] = useState<Set<string>>(new Set()); // empty = all
    const [query, setQuery] = useState('');
    const [connectedOnly, setConnectedOnly] = useState(false);

    const toggleSet = (set: Set<string>, v: string, apply: (s: Set<string>) => void) => {
        const next = new Set(set);
        next.has(v) ? next.delete(v) : next.add(v);
        apply(next);
    };

    // ---- graph build (filtered) ----
    const { custom: customTypes } = useArtifactTypes();
    const hiddenTypes = useMemo(() => new Set(customTypes.filter((d) => !d.show_in_mindmap).map((d) => d.key)), [customTypes]);

    const graph = useMemo(() => {
        const q = query.trim().toLowerCase();
        const ns: GNode[] = [];
        const ls: GLink[] = [];

        const matches = (s: string) => !q || (s || '').toLowerCase().includes(q);

        if (showKind.case) {
            cases.forEach((c: any) => {
                if (sevFilter.size && !sevFilter.has(c.severity)) return;
                if (!matches(c.title)) return;
                ns.push({ id: `case-${c.id}`, kind: 'case', label: c.title, color: c.severity ? sevColor(c.severity) : C.caseFallback, data: c, x: 0, y: 0, vx: 0, vy: 0 });
            });
        }
        if (showKind.artifact) {
            artifacts.forEach((a: any) => {
                if (hiddenTypes.has(a.artifact_type)) return;
                if (!matches(a.value)) return;
                ns.push({ id: `artifact-${a.id}`, kind: 'artifact', label: a.value, color: C.artifact, data: a, x: 0, y: 0, vx: 0, vy: 0 });
            });
        }
        if (showKind.ioc) {
            iocs.forEach((i: any) => {
                if (threatFilter.size && !threatFilter.has(i.threat_level)) return;
                if (!matches(i.value)) return;
                ns.push({ id: `ioc-${i.id}`, kind: 'ioc', label: i.value, color: i.threat_level ? sevColor(i.threat_level) : C.iocFallback, data: i, x: 0, y: 0, vx: 0, vy: 0 });
            });
        }

        const have = new Set(ns.map(n => n.id));
        artifacts.forEach((a: any) => {
            (a.case_ids || []).forEach((cid: number) => {
                if (have.has(`artifact-${a.id}`) && have.has(`case-${cid}`))
                    ls.push({ source: `case-${cid}`, target: `artifact-${a.id}` });
            });
        });
        iocs.forEach((i: any) => {
            if (i.case_id && have.has(`ioc-${i.id}`) && have.has(`case-${i.case_id}`))
                ls.push({ source: `case-${i.case_id}`, target: `ioc-${i.id}` });
        });
        // value-match bridges: ioc.value === artifact.value
        const artByValue = new Map<string, any[]>();
        artifacts.forEach((a: any) => {
            const k = (a.value || '').toLowerCase();
            artByValue.set(k, [...(artByValue.get(k) || []), a]);
        });
        iocs.forEach((i: any) => {
            (artByValue.get((i.value || '').toLowerCase()) || []).forEach((a: any) => {
                if (have.has(`ioc-${i.id}`) && have.has(`artifact-${a.id}`))
                    ls.push({ source: `ioc-${i.id}`, target: `artifact-${a.id}`, match: true });
            });
        });

        let nodes = ns;
        let links = ls;
        if (caseId !== undefined) {
            const near = new Set([`case-${caseId}`]);
            for (let hop = 0; hop < 2; hop++) {
                links.forEach(l => {
                    if (near.has(l.source) || near.has(l.target)) { near.add(l.source); near.add(l.target); }
                });
            }
            nodes = nodes.filter(n => near.has(n.id));
            links = links.filter(l => near.has(l.source) && near.has(l.target));
        }
        if (connectedOnly) {
            const linked = new Set<string>();
            links.forEach(l => { linked.add(l.source); linked.add(l.target); });
            nodes = nodes.filter(n => linked.has(n.id));
            const keep = new Set(nodes.map(n => n.id));
            links = links.filter(l => keep.has(l.source) && keep.has(l.target));
        }
        return { nodes, links };
    }, [cases, artifacts, iocs, showKind, sevFilter, threatFilter, query, connectedOnly, hiddenTypes, caseId]);

    // adjacency for hover/selection highlighting
    const neighbors = useMemo(() => {
        const m = new Map<string, Set<string>>();
        graph.links.forEach(l => {
            if (!m.has(l.source)) m.set(l.source, new Set());
            if (!m.has(l.target)) m.set(l.target, new Set());
            m.get(l.source)!.add(l.target);
            m.get(l.target)!.add(l.source);
        });
        return m;
    }, [graph]);

    // ---- simulation (settles, keeps prior positions across filter changes) ----
    const [nodes, setNodes] = useState<GNode[]>([]);
    const posRef = useRef<Map<string, { x: number; y: number }>>(new Map());

    useEffect(() => {
        if (graph.nodes.length === 0) { setNodes([]); return; }
        const W = dims.width, H = dims.height;
        const sim = graph.nodes.map(n => {
            const prev = posRef.current.get(n.id);
            return { ...n, x: prev?.x ?? (W / 2 + (Math.random() - 0.5) * W * 0.7), y: prev?.y ?? (H / 2 + (Math.random() - 0.5) * H * 0.7) };
        });
        const idx = new Map(sim.map((n, i) => [n.id, i]));
        const linkDistance = 105, repulsion = 1600, centerPull = 0.035;
        let frame = 0, raf = 0;
        const tick = () => {
            for (let i = 0; i < sim.length; i++) {
                const n = sim[i];
                n.vx += (W / 2 - n.x) * centerPull * 0.05;
                n.vy += (H / 2 - n.y) * centerPull * 0.05;
                for (let j = 0; j < sim.length; j++) {
                    if (i === j) continue;
                    const o = sim[j];
                    const dx = n.x - o.x, dy = n.y - o.y;
                    const d = Math.sqrt(dx * dx + dy * dy) || 1;
                    const f = repulsion / (d * d);
                    n.vx += (dx / d) * f; n.vy += (dy / d) * f;
                }
            }
            graph.links.forEach(l => {
                const s = sim[idx.get(l.source)!], t = sim[idx.get(l.target)!];
                if (!s || !t) return;
                const dx = t.x - s.x, dy = t.y - s.y;
                const d = Math.sqrt(dx * dx + dy * dy) || 1;
                const f = (d - linkDistance) * 0.05;
                const fx = (dx / d) * f, fy = (dy / d) * f;
                s.vx += fx; s.vy += fy; t.vx -= fx; t.vy -= fy;
            });
            sim.forEach(n => {
                n.x += n.vx; n.y += n.vy; n.vx *= 0.85; n.vy *= 0.85;
                posRef.current.set(n.id, { x: n.x, y: n.y });
            });
            setNodes([...sim]);
            frame++;
            if (frame < 280) raf = requestAnimationFrame(tick);
        };
        tick();
        return () => cancelAnimationFrame(raf);
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [graph, dims]);

    // ---- resize ----
    useEffect(() => {
        const onResize = () => {
            const p = wrapRef.current;
            if (p && p.clientWidth && p.clientHeight) setDims({ width: p.clientWidth, height: p.clientHeight });
        };
        window.addEventListener('resize', onResize);
        onResize();
        const t = setTimeout(onResize, 60);
        return () => { window.removeEventListener('resize', onResize); clearTimeout(t); };
    }, []);

    // ---- zoom / pan / node drag ----
    const [view, setView] = useState({ x: 0, y: 0, k: 1 });
    const dragRef = useRef<{ mode: 'pan' | 'node'; id?: string; sx: number; sy: number; ox: number; oy: number } | null>(null);

    useEffect(() => {
        const el = wrapRef.current;
        if (!el) return;
        const onWheel = (e: WheelEvent) => {
            e.preventDefault();
            const rect = el.getBoundingClientRect();
            const mx = e.clientX - rect.left, my = e.clientY - rect.top;
            setView(v => {
                const k = Math.min(3, Math.max(0.35, v.k * (e.deltaY < 0 ? 1.12 : 0.89)));
                // zoom around cursor
                return { k, x: mx - ((mx - v.x) / v.k) * k, y: my - ((my - v.y) / v.k) * k };
            });
        };
        el.addEventListener('wheel', onWheel, { passive: false });
        return () => el.removeEventListener('wheel', onWheel);
    }, []);

    const toGraph = useCallback((clientX: number, clientY: number) => {
        const rect = wrapRef.current!.getBoundingClientRect();
        return { x: (clientX - rect.left - view.x) / view.k, y: (clientY - rect.top - view.y) / view.k };
    }, [view]);

    const onMouseDown = (e: React.MouseEvent) => {
        dragRef.current = { mode: 'pan', sx: e.clientX, sy: e.clientY, ox: view.x, oy: view.y };
    };
    const onNodeMouseDown = (e: React.MouseEvent, id: string) => {
        e.stopPropagation();
        dragRef.current = { mode: 'node', id, sx: e.clientX, sy: e.clientY, ox: 0, oy: 0 };
    };
    const onMouseMove = (e: React.MouseEvent) => {
        const d = dragRef.current;
        if (!d) return;
        if (d.mode === 'pan') {
            setView(v => ({ ...v, x: d.ox + (e.clientX - d.sx), y: d.oy + (e.clientY - d.sy) }));
        } else if (d.id) {
            const p = toGraph(e.clientX, e.clientY);
            setNodes(ns => ns.map(n => n.id === d.id ? { ...n, x: p.x, y: p.y } : n));
            posRef.current.set(d.id, p);
        }
    };
    const endDrag = () => { dragRef.current = null; };

    // ---- selection / hover ----
    const [selected, setSelected] = useState<GNode | null>(null);
    const [hoverId, setHoverId] = useState<string | null>(null);
    const focusId = hoverId ?? selected?.id ?? null;
    const focusSet = useMemo(() => {
        if (!focusId) return null;
        const s = new Set([focusId]);
        (neighbors.get(focusId) || []).forEach(id => s.add(id));
        return s;
    }, [focusId, neighbors]);

    const matchCount = graph.links.filter(l => l.match).length;
    const counts = { case: cases.length, artifact: artifacts.length, ioc: iocs.length };
    const kindMeta = {
        case: { label: 'Cases', icon: ShieldAlert },
        artifact: { label: 'Artifacts', icon: Database },
        ioc: { label: 'IOCs', icon: Crosshair },
    } as const;

    const resetView = () => setView({ x: 0, y: 0, k: 1 });

    return (
        <div className={caseId === undefined ? 'p-4 sm:p-6 flex flex-col h-[calc(100vh-3.5rem)]' : 'flex flex-col h-[70vh]'}>
            {(() => {
                const legend = (
                    <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-fg-3">
                        <span className="flex items-center gap-1.5"><span className="w-3 h-3 rounded-full bg-accent" /> Case</span>
                        <span className="flex items-center gap-1.5"><span className="w-3 h-3 bg-muted" /> Artifact</span>
                        <span className="flex items-center gap-1.5"><span className="w-3 h-3 rotate-45 bg-severity-high" /> IOC</span>
                        <span className="flex items-center gap-1.5"><span className="w-5 border-t-2 border-dashed border-severity-medium" /> Value match</span>
                    </div>
                );
                const filtersToggle = (
                    <Button variant="secondary" size="sm" className="md:hidden" aria-expanded={railOpen} aria-controls="graph-filters"
                        onClick={() => setRailOpen(o => !o)}>
                        <SlidersHorizontal size={13} aria-hidden /> Filters
                    </Button>
                );
                return caseId === undefined ? (
                    <PageHeader className="shrink-0" title="Investigation graph"
                        description="Cases, artifacts and IOCs, and the links between them. Drag, zoom, and click a node for details."
                        actions={<>{legend}{filtersToggle}</>} />
                ) : (
                    <div className="flex flex-wrap items-center justify-between gap-2 mb-3 shrink-0">{legend}{filtersToggle}</div>
                );
            })()}
            {truncated.length > 0 && (
                <p className="text-xs text-fg-3 -mt-2 mb-3 shrink-0">Graph built from the newest {truncated.join(', ')}.</p>
            )}

            <div className="flex-1 min-h-0 flex border border-line bg-panel relative overflow-hidden">
                {/* Filter rail */}
                <aside id="graph-filters" aria-label="Graph filters"
                    className={cn('border-line bg-surface overflow-y-auto',
                        railOpen
                            ? 'absolute inset-y-0 left-0 z-10 w-56 border-r shadow-lg md:static md:z-auto md:shrink-0 md:shadow-none'
                            : 'hidden md:block md:w-9 md:shrink-0 md:border-r')}>
                    {railOpen ? (
                        <div className="p-3 space-y-4 w-56">
                            <div className="flex items-center justify-between">
                                <h2 className="label-caps">Filters</h2>
                                <button onClick={() => setRailOpen(false)} className="p-1 text-muted hover:text-fg" aria-label="Collapse filters">
                                    <ChevronLeft size={14} />
                                </button>
                            </div>

                            <div className="relative">
                                <Search size={13} className="absolute left-2 top-2.5 text-muted" aria-hidden />
                                <Input inputSize="sm" value={query} onChange={e => setQuery(e.target.value)} placeholder="Search value or title…"
                                    aria-label="Search value or title" className="pl-7" />
                            </div>

                            <div className="space-y-1">
                                {(Object.keys(kindMeta) as Array<keyof typeof kindMeta>).map(k => {
                                    const Meta = kindMeta[k];
                                    return (
                                        <label key={k} className="flex items-center gap-2 text-sm text-fg-2 cursor-pointer hover:bg-raised px-1.5 py-1">
                                            <input type="checkbox" checked={showKind[k]}
                                                onChange={() => setShowKind(s => ({ ...s, [k]: !s[k] }))}
                                            />
                                            <Meta.icon size={13} className="text-fg-3" aria-hidden />
                                            {Meta.label}
                                            <span className="num text-xs text-fg-3 ml-auto">{counts[k]}</span>
                                        </label>
                                    );
                                })}
                            </div>

                            <div>
                                <p className="label-caps mb-1.5">Case severity</p>
                                <div className="flex flex-wrap gap-1">
                                    {SEVERITIES.map(s => (
                                        <button key={s} onClick={() => toggleSet(sevFilter, s, setSevFilter)} aria-pressed={sevFilter.has(s)}
                                            className={cn('text-xs px-1.5 py-0.5 rounded-sm border border-current/40 transition-opacity', TONE_TEXT[severityTone(s)],
                                                sevFilter.size === 0 || sevFilter.has(s)
                                                    ? 'opacity-100' : 'opacity-40')}>
                                            {s}
                                        </button>
                                    ))}
                                </div>
                            </div>

                            <div>
                                <p className="label-caps mb-1.5">IOC threat</p>
                                <div className="flex flex-wrap gap-1">
                                    {THREATS.map(s => (
                                        <button key={s} onClick={() => toggleSet(threatFilter, s, setThreatFilter)} aria-pressed={threatFilter.has(s)}
                                            className={cn('text-xs px-1.5 py-0.5 rounded-sm border border-current/40 transition-opacity', TONE_TEXT[severityTone(s)],
                                                threatFilter.size === 0 || threatFilter.has(s) ? 'opacity-100' : 'opacity-40')}>
                                            {s}
                                        </button>
                                    ))}
                                </div>
                            </div>

                            <label className="flex items-center gap-2 text-xs text-fg-2 cursor-pointer">
                                <input type="checkbox" checked={connectedOnly} onChange={() => setConnectedOnly(v => !v)} />
                                Connected nodes only
                            </label>

                            <Button variant="secondary" size="sm" className="w-full" onClick={resetView}>
                                <Maximize2 size={12} aria-hidden /> Reset view
                            </Button>
                        </div>
                    ) : (
                        <button onClick={() => setRailOpen(true)}
                            className="w-9 h-full flex items-start justify-center pt-3 text-muted hover:text-fg" aria-label="Open filters">
                            <SlidersHorizontal size={15} />
                        </button>
                    )}
                </aside>

                {/* Canvas */}
                <div ref={wrapRef} className="flex-1 min-w-0 relative console-grid cursor-grab active:cursor-grabbing"
                    onMouseDown={onMouseDown} onMouseMove={onMouseMove} onMouseUp={endDrag} onMouseLeave={endDrag}>
                    <svg className="w-full h-full block select-none">
                        <g transform={`translate(${view.x},${view.y}) scale(${view.k})`}>
                            {graph.links.map((l, i) => {
                                const s = nodes.find(n => n.id === l.source);
                                const t = nodes.find(n => n.id === l.target);
                                if (!s || !t) return null;
                                const inFocus = !focusSet || (focusSet.has(l.source) && focusSet.has(l.target));
                                return (
                                    <line key={i} x1={s.x} y1={s.y} x2={t.x} y2={t.y}
                                        style={{ stroke: l.match ? C.match : inFocus && focusSet ? C.focus : C.link }}
                                        strokeWidth={l.match ? 1.6 : inFocus && focusSet ? 1.6 : 1}
                                        strokeDasharray={l.match ? '5 4' : undefined}
                                        opacity={inFocus ? (l.match ? 0.95 : 0.7) : 0.08} />
                                );
                            })}
                            {nodes.map(n => {
                                const dim = focusSet ? !focusSet.has(n.id) : false;
                                const isSel = selected?.id === n.id;
                                const r = n.kind === 'case' ? 15 : 9;
                                return (
                                    <g key={n.id} transform={`translate(${n.x},${n.y})`} opacity={dim ? 0.15 : 1}
                                        className="cursor-pointer"
                                        onMouseDown={(e) => onNodeMouseDown(e, n.id)}
                                        onMouseEnter={() => setHoverId(n.id)}
                                        onMouseLeave={() => setHoverId(null)}
                                        onClick={(e) => { e.stopPropagation(); setSelected(n); }}>
                                        {n.kind === 'case' && (
                                            <circle r={r} style={{ fill: n.color, stroke: isSel ? C.selectedRing : C.nodeRing }} strokeWidth={isSel ? 3 : 2} />
                                        )}
                                        {n.kind === 'artifact' && (
                                            <rect x={-r} y={-r} width={r * 2} height={r * 2}
                                                style={{ fill: n.color, stroke: isSel ? C.selectedRing : C.nodeRing }} strokeWidth={isSel ? 3 : 2} />
                                        )}
                                        {n.kind === 'ioc' && (
                                            <rect x={-r} y={-r} width={r * 2} height={r * 2} transform="rotate(45)"
                                                style={{ fill: n.color, stroke: isSel ? C.selectedRing : C.nodeRing }} strokeWidth={isSel ? 3 : 2} />
                                        )}
                                        {n.kind === 'case' && <ShieldAlert size={15} x={-7.5} y={-7.5} className="pointer-events-none" style={{ color: C.caseIcon }} />}
                                        <text y={n.kind === 'case' ? 28 : 22} textAnchor="middle"
                                            className={cn('text-xs pointer-events-none',
                                                isSel ? 'fill-accent font-semibold' : 'fill-muted',
                                                n.kind === 'case' && 'font-medium')}>
                                            {(n.label || '').length > 22 ? n.label.slice(0, 20) + '…' : n.label}
                                        </text>
                                    </g>
                                );
                            })}
                        </g>
                    </svg>

                    {/* stats strip */}
                    <div className="absolute bottom-2 left-2 num text-xs text-fg-3 bg-panel border border-line rounded-sm px-2 py-1">
                        {nodes.length} nodes · {graph.links.length} links · <span className="text-severity-medium">{matchCount} value match{matchCount === 1 ? '' : 'es'}</span> · zoom {Math.round(view.k * 100)}%
                    </div>

                    {nodes.length === 0 && (
                        <div role="status" className="absolute inset-0 flex items-center justify-center text-fg-3 text-sm pointer-events-none">
                            {graph.nodes.length === 0 ? 'Nothing matches the current filters.' : 'Rendering graph…'}
                        </div>
                    )}

                    {/* Detail dossier */}
                    {selected && (
                        <div className="absolute top-3 right-3 w-72 max-w-[calc(100%-1.5rem)] bg-panel border border-line rounded-md shadow-lg animate-fade-in" onMouseDown={e => e.stopPropagation()}>
                            <div className="flex items-center justify-between px-3 h-9 border-b border-line">
                                <h2 className="label-caps">{selected.kind === 'ioc' ? 'IOC' : selected.kind} detail</h2>
                                <button onClick={() => setSelected(null)} className="text-muted hover:text-fg" aria-label="Close details"><X size={15} /></button>
                            </div>
                            <div className="p-3 space-y-2 text-sm">
                                {selected.kind === 'case' && (
                                    <>
                                        <p className="font-semibold text-fg">{selected.data.title}</p>
                                        <div className="flex items-center gap-2">
                                            <SeverityStamp severity={selected.data.severity} />
                                            <StatusStamp status={selected.data.status} />
                                        </div>
                                        {selected.data.description && <p className="text-xs text-fg-3 line-clamp-3">{selected.data.description}</p>}
                                        <p className="text-xs text-fg-3">
                                            linked: <span className="num text-fg">{(neighbors.get(selected.id)?.size ?? 0)}</span> node(s)
                                        </p>
                                        <Link to={`/cases/${selected.data.id}`} className="inline-flex items-center gap-1 text-xs text-accent hover:underline font-medium">
                                            Open case #{selected.data.id} <ArrowUpRight size={12} />
                                        </Link>
                                    </>
                                )}
                                {selected.kind === 'artifact' && (
                                    <>
                                        <p className="num text-sm text-fg break-all">{selected.data.value}</p>
                                        <div className="flex items-center gap-2">
                                            <Badge>{selected.data.artifact_type}</Badge>
                                            {selected.data.isolated && <Badge variant="medium">Isolated</Badge>}
                                        </div>
                                        <p className="text-xs text-fg-3">in <span className="num text-fg">{selected.data.case_ids?.length ?? 0}</span> case(s)</p>
                                        <div className="flex flex-wrap gap-1.5">
                                            {(selected.data.case_ids || []).map((cid: number) => (
                                                <Link key={cid} to={`/cases/${cid}`} className="num text-xs text-accent hover:underline border border-line rounded-sm px-1.5">#{cid}</Link>
                                            ))}
                                        </div>
                                    </>
                                )}
                                {selected.kind === 'ioc' && (
                                    <>
                                        <p className="num text-sm text-fg break-all">{selected.data.value}</p>
                                        <div className="flex items-center gap-2 flex-wrap">
                                            <Badge>{selected.data.ioc_type}</Badge>
                                            <SeverityStamp severity={selected.data.threat_level} />
                                            <Stamp tone={tlpTone(selected.data.tlp)}>TLP:{selected.data.tlp}</Stamp>
                                        </div>
                                        <p className="text-xs text-fg-3">
                                            confidence <span className="num text-fg">{selected.data.confidence}%</span> · status <span className="text-fg-2">{selected.data.status}</span>
                                        </p>
                                        {selected.data.case_id && (
                                            <Link to={`/cases/${selected.data.case_id}`} className="inline-flex items-center gap-1 text-xs text-accent hover:underline font-medium">
                                                Open case #{selected.data.case_id} <ArrowUpRight size={12} />
                                            </Link>
                                        )}
                                    </>
                                )}
                            </div>
                        </div>
                    )}
                </div>
            </div>
        </div>
    );
}
