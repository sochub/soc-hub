import { useCallback, useEffect, useMemo, useRef, useState, type DragEvent } from 'react';
import { useNavigate, useParams, Link } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
    ReactFlow, ReactFlowProvider, Background, Controls, MiniMap, addEdge, useEdgesState, useNodesState, useReactFlow,
    type Connection, type Edge,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import { ArrowLeft, Save, CheckCircle2, AlertTriangle, FlaskConical, PanelLeft, PanelRight, X } from 'lucide-react';
import { api } from '../../api/client';
import { cn } from '../../lib/utils';
import type { User } from '../../types';
import type { TriggerType, ValidationError, Workflow, WfGraph, WfNode } from './types';
import { NODE_DEF, NODE_DEFS, TRIGGER_LABEL } from './nodeCatalog';
import { FLOW_DOT_COLOR, FLOW_PANEL_CLASS, FLOW_THEME, fromFlow, nextNodeId, toFlow, type WfFlowNode } from './flow';
import WorkflowNode from './WorkflowNode';
import NodeInspector from './NodeInspector';
import RunsTable from './RunsTable';
import DryRunDialog from './DryRunDialog';
import EnabledSwitch from './EnabledSwitch';
import { Button, FolderTabs, Select, Textarea, Label, TONE_TEXT } from '../../components/ui';

type ApiErr = { response?: { data?: { detail?: { errors?: ValidationError[]; message?: string } } } };
const nodeTypes = { wf: WorkflowNode };
const EMPTY_GRAPH: WfGraph = { nodes: [{ id: 'start', type: 'trigger', position: { x: 250, y: 40 }, config: {} }], edges: [] };

interface Meta { name: string; description: string; trigger_type: TriggerType; trigger_filter: string }
const NEW_META: Meta = { name: 'Untitled workflow', description: '', trigger_type: 'case.created', trigger_filter: '' };
type Notice = { kind: 'status' | 'alert'; text: string };

/** What a save would send; compared against the last saved/loaded snapshot to show "Unsaved changes". */
const snapshot = (meta: Meta, nodes: WfFlowNode[], edges: Edge[]) => JSON.stringify({ meta, graph: fromFlow(nodes, edges) });

function Editor() {
    const { id } = useParams();
    const isNew = id === 'new';
    const navigate = useNavigate();
    const qc = useQueryClient();
    const { screenToFlowPosition, getIntersectingNodes, getInternalNode } = useReactFlow();

    const { data: me } = useQuery({ queryKey: ['currentUser'], queryFn: async () => (await api.get('/users/me')).data as User, staleTime: 300_000 });
    const readOnly = !(me?.role === 'admin' || me?.is_super_admin);

    const { data: wf } = useQuery({
        queryKey: ['workflow', id],
        queryFn: async () => (await api.get(`/workflows/${id}`)).data as Workflow,
        enabled: !isNew,
    });

    const [meta, setMeta] = useState<Meta>(NEW_META);
    const [nodes, setNodes, onNodesChange] = useNodesState<WfFlowNode>([]);
    const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);
    const [selectedId, setSelectedId] = useState<string | null>(null);
    const [errors, setErrors] = useState<ValidationError[]>([]);
    const [tab, setTab] = useState<'editor' | 'runs'>('editor');
    const [dryRunOpen, setDryRunOpen] = useState(false);
    const [notice, setNotice] = useState<Notice | null>(null);
    const setStatus = (text: string) => setNotice({ kind: 'status', text });
    const setAlert = (text: string) => setNotice({ kind: 'alert', text });
    const [paletteOpen, setPaletteOpen] = useState(false);
    const [inspectorOpen, setInspectorOpen] = useState(false);
    const [savedSnap, setSavedSnap] = useState<string | null>(null);
    const [loadedVersion, setLoadedVersion] = useState<number | null>(null);
    // id of the workflow the canvas was last hydrated for; server responses never re-hydrate after that
    const hydratedFor = useRef<string | null>(null);

    /* eslint-disable react-hooks/set-state-in-effect -- one-time hydration of editor state from fetched workflow */
    useEffect(() => {
        if (isNew && hydratedFor.current !== 'new') {
            hydratedFor.current = 'new';
            const f = toFlow(EMPTY_GRAPH); setNodes(f.nodes); setEdges(f.edges); setLoadedVersion(0);
            setSavedSnap(snapshot(NEW_META, f.nodes, f.edges));
        }
        if (wf && hydratedFor.current !== String(wf.id)) {
            hydratedFor.current = String(wf.id);
            const m: Meta = { name: wf.name, description: wf.description ?? '', trigger_type: wf.trigger_type, trigger_filter: wf.trigger_filter ?? '' };
            setMeta(m);
            const invalid = new Set(wf.validation_errors.map((e) => e.node_id).filter(Boolean) as string[]);
            const f = toFlow(wf.graph, { invalidIds: invalid });
            setNodes(f.nodes); setEdges(f.edges); setErrors(wf.validation_errors); setLoadedVersion(wf.version);
            setSavedSnap(snapshot(m, f.nodes, f.edges));
        }
    }, [wf, isNew, setNodes, setEdges]);
    /* eslint-enable react-hooks/set-state-in-effect */

    const onConnect = useCallback((c: Connection) => setEdges((eds) => addEdge({ ...c, id: `e_${c.source}_${c.target}_${c.sourceHandle ?? 'out'}`, label: c.sourceHandle ?? undefined }, eds)), [setEdges]);

    const addNode = useCallback((type: string, flowPos: { x: number; y: number }, parentId?: string) => {
        setNodes((ns) => {
            const taken = new Set(ns.map((n) => n.id));
            const nid = nextNodeId(type, taken);
            let position = flowPos;
            if (parentId) {
                const abs = getInternalNode(parentId)?.internals.positionAbsolute ?? { x: 0, y: 0 };
                position = { x: flowPos.x - abs.x, y: flowPos.y - abs.y };
            }
            const wfNode: WfNode = { id: nid, type, position, parent_id: parentId ?? null,
                                     config: type === 'http_request' ? { method: 'GET' } : {} };
            return [...ns, ...toFlow({ nodes: [wfNode], edges: [] }).nodes];
        });
    }, [setNodes, getInternalNode]);

    const onDrop = useCallback((e: DragEvent) => {
        e.preventDefault();
        const type = e.dataTransfer.getData('application/wf-node');
        if (!type) return;
        const pos = screenToFlowPosition({ x: e.clientX, y: e.clientY });
        // ponytail: only palette drops can enter a loop; moving an existing node in/out means delete + re-add.
        const container = type === 'for_each' ? undefined : getIntersectingNodes({ x: pos.x, y: pos.y, width: 1, height: 1 })
            .find((n) => NODE_DEF[(n as WfFlowNode).data.wf.type]?.container);
        addNode(type, pos, container?.id);
    }, [addNode, screenToFlowPosition, getIntersectingNodes]);

    const selected = nodes.find((n) => n.id === selectedId)?.data.wf;
    const selectedErrors = errors.filter((e) => e.node_id === selectedId).map((e) => e.message);
    const graphErrors = errors.filter((e) => !e.node_id);

    const updateNode = (updated: WfNode) =>
        setNodes((ns) => ns.map((n) => (n.id === updated.id ? { ...n, data: { ...n.data, wf: updated } } : n)));
    const renameNode = (oldId: string, newId: string) => {
        if (nodes.some((n) => n.id === newId)) { setAlert(`Node id "${newId}" already exists`); return false; }
        setNodes((ns) => ns.map((n) => {
            if (n.id === oldId) return { ...n, id: newId, data: { ...n.data, wf: { ...n.data.wf, id: newId } } };
            if (n.parentId === oldId) return { ...n, parentId: newId };
            return n;
        }));
        setEdges((es) => es.map((e) => ({ ...e, source: e.source === oldId ? newId : e.source, target: e.target === oldId ? newId : e.target })));
        setSelectedId(newId);
        return true;
    };
    const deleteNode = (nid: string) => {
        const gone = new Set([nid, ...nodes.filter((n) => n.parentId === nid).map((n) => n.id)]);
        setNodes((ns) => ns.filter((n) => !gone.has(n.id)));
        setEdges((es) => es.filter((e) => !gone.has(e.source) && !gone.has(e.target)));
        setSelectedId(null);
    };

    const onBeforeDelete = useCallback(async ({ nodes: dn, edges: de }: { nodes: WfFlowNode[]; edges: Edge[] }) => {
        const gone = new Set(dn.filter((n) => n.data.wf.type !== 'trigger').map((n) => n.id));
        const kids = nodes.filter((n) => n.parentId && gone.has(n.parentId)).map((n) => n.id);
        kids.forEach((k) => gone.add(k));
        return {
            nodes: nodes.filter((n) => gone.has(n.id)),
            edges: edges.filter((e) => gone.has(e.source) || gone.has(e.target) || (e.selected && de.some((d) => d.id === e.id))),
        };
    }, [nodes, edges]);

    const save = useMutation({
        mutationFn: async () => {
            const snap = snapshot(meta, nodes, edges);
            const body = { ...meta, trigger_filter: meta.trigger_filter || null, description: meta.description || null, graph: fromFlow(nodes, edges) };
            const saved = (isNew ? await api.post('/workflows/', body) : await api.put(`/workflows/${id}`, body)).data as Workflow;
            return { saved, snap };
        },
        onSuccess: ({ saved, snap }) => {
            setSavedSnap(snap);
            hydratedFor.current = String(saved.id);
            setLoadedVersion(saved.version);
            setErrors(saved.validation_errors);
            setStatus(saved.validation_errors.length ? `Saved with ${saved.validation_errors.length} problem(s)` : 'Saved');
            qc.invalidateQueries({ queryKey: ['workflows'] });
            qc.setQueryData(['workflow', String(saved.id)], saved);
            if (isNew) navigate(`/workflows/${saved.id}`, { replace: true });
        },
        onError: (err: ApiErr) => {
            const detail = err?.response?.data?.detail;
            if (detail?.errors) setErrors(detail.errors);
            setAlert(detail?.message ?? 'Save failed');
        },
    });

    const toggle = useMutation({
        mutationFn: async () => (await api.post(`/workflows/${id}/${wf?.enabled ? 'disable' : 'enable'}`)).data as Workflow,
        onSuccess: (w) => { qc.setQueryData(['workflow', id], w); qc.invalidateQueries({ queryKey: ['workflows'] }); setStatus(w.enabled ? 'Enabled' : 'Disabled'); },
        onError: (err: ApiErr) => { setErrors(err?.response?.data?.detail?.errors ?? []); setAlert('Fix the problems below before enabling'); },
    });

    const invalidIds = useMemo(() => new Set(errors.map((e) => e.node_id).filter(Boolean) as string[]), [errors]);
    const displayNodes = useMemo(() => nodes.map((n) => ({ ...n, data: { ...n.data, invalid: invalidIds.has(n.id) } })), [nodes, invalidIds]);

    const dirty = !readOnly && savedSnap !== null && savedSnap !== snapshot(meta, nodes, edges);

    // Cmd/Ctrl+S saves (also from inside inputs).
    const { mutate: saveNow, isPending: saving } = save;
    useEffect(() => {
        const onKey = (e: KeyboardEvent) => {
            if ((e.metaKey || e.ctrlKey) && !e.shiftKey && !e.altKey && e.key.toLowerCase() === 's') {
                e.preventDefault();
                if (!readOnly && !saving) saveNow();
            }
        };
        window.addEventListener('keydown', onKey);
        return () => window.removeEventListener('keydown', onKey);
    }, [readOnly, saving, saveNow]);

    // Click-to-add drops the node in the middle of the visible canvas.
    const addAtCentre = useCallback((type: string) => {
        const r = document.getElementById('wf-canvas')?.getBoundingClientRect();
        const pt = r ? { x: r.left + r.width / 2, y: r.top + r.height / 2 } : { x: window.innerWidth / 2, y: window.innerHeight / 2 };
        addNode(type, screenToFlowPosition(pt));
        setPaletteOpen(false);
    }, [addNode, screenToFlowPosition]);

    const palette = NODE_DEFS.filter((d) => d.type !== 'trigger' && (!d.alertOnly || meta.trigger_type === 'alert.ingested'));

    const panelBase = 'bg-panel overflow-y-auto absolute inset-y-0 z-20 shadow-console-hover lg:static lg:z-auto lg:shadow-none';

    return (
        <div className="flex flex-col h-[calc(100vh-3.5rem)]">
            <div className="flex items-center gap-x-3 gap-y-2 px-4 pt-2 border-b border-line bg-panel flex-wrap">
                <Link to="/workflows" className="p-1 text-fg-3 hover:text-fg" aria-label="Back to workflows"><ArrowLeft size={18} aria-hidden /></Link>
                <input className="text-base font-semibold text-fg bg-transparent border-b border-transparent focus:border-accent min-w-0 w-56 sm:w-72"
                    disabled={readOnly} value={meta.name} onChange={(e) => setMeta({ ...meta, name: e.target.value })} aria-label="Workflow name" />
                {wf && <span className="num text-xs text-muted">v{wf.version}</span>}
                {dirty && <span className="text-xs font-medium text-severity-medium">Unsaved changes</span>}
                <FolderTabs className="border-b-0 self-end"
                    items={isNew ? [{ value: 'editor' as const, label: 'Editor' }] : [{ value: 'editor' as const, label: 'Editor' }, { value: 'runs' as const, label: 'Runs' }]}
                    value={tab} onChange={setTab} label="Workflow views" idPrefix="wf-tab" />
                <div className="ml-auto flex items-center gap-2 pb-2 flex-wrap">
                    {notice?.kind === 'status' && <span role="status" className={cn('text-xs', TONE_TEXT.ok)}>{notice.text}</span>}
                    {!readOnly && !isNew && (
                        <EnabledSwitch checked={!!wf?.enabled} label="Workflow enabled" disabled={toggle.isPending} onClick={() => toggle.mutate()}>
                            {wf?.enabled ? 'Enabled' : 'Disabled'}
                        </EnabledSwitch>
                    )}
                    {!readOnly && !isNew && wf && (
                        <Button variant="secondary" size="sm" onClick={() => setDryRunOpen(true)} title="Uses the last saved version">
                            <FlaskConical size={14} aria-hidden /> Dry run
                        </Button>
                    )}
                    {!readOnly && (
                        <Button size="sm" onClick={() => save.mutate()} disabled={save.isPending} title="Save (Ctrl+S / ⌘S)">
                            <Save size={14} aria-hidden /> {save.isPending ? 'Saving…' : 'Save'}
                        </Button>
                    )}
                </div>
            </div>
            {notice?.kind === 'alert' && (
                <div role="alert" className="flex items-center gap-2 px-4 py-2 text-sm border-b border-severity-critical/30 bg-severity-critical/5 text-severity-critical">
                    <AlertTriangle size={14} aria-hidden className="shrink-0" />
                    <span className="mr-auto">{notice.text}</span>
                    <button type="button" onClick={() => setNotice(null)} aria-label="Dismiss" className="p-1 rounded-sm hover:bg-raised"><X size={14} aria-hidden /></button>
                </div>
            )}

            {tab === 'runs' && wf ? <div id="wf-tab-panel" role="tabpanel" aria-labelledby="wf-tab-runs" className="p-4 overflow-auto"><RunsTable workflowId={wf.id} /></div> : (
                <div id="wf-tab-panel" role="tabpanel" aria-labelledby="wf-tab-editor" className="relative flex flex-1 min-h-0">
                    <aside id="wf-palette" aria-label="Trigger and nodes"
                        className={cn(panelBase, 'left-0 w-64 max-w-[85%] border-r border-line p-3 space-y-4 lg:w-56', paletteOpen ? 'block' : 'hidden lg:block')}>
                        <div className="flex items-center justify-between lg:hidden">
                            <span className="text-sm font-semibold text-fg">Nodes</span>
                            <button type="button" onClick={() => setPaletteOpen(false)} aria-label="Close nodes panel" className="p-1 rounded-sm text-fg-3 hover:bg-raised"><X size={16} aria-hidden /></button>
                        </div>
                        <div className="space-y-2">
                            <Label htmlFor="wf-trigger" className="label-mono">Trigger</Label>
                            <Select id="wf-trigger" inputSize="sm" disabled={readOnly} value={meta.trigger_type}
                                onChange={(e) => setMeta({ ...meta, trigger_type: e.target.value as TriggerType })}>
                                {Object.entries(TRIGGER_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
                            </Select>
                            {meta.trigger_type === 'schedule' && <p className="label-mono">Cron expression (UTC)</p>}
                            <Textarea rows={2} className="font-mono text-xs" disabled={readOnly}
                                aria-label={meta.trigger_type === 'schedule' ? 'Cron expression (UTC)' : 'Trigger filter'}
                                placeholder={meta.trigger_type === 'schedule' ? '*/15 * * * *' : "filter, e.g. 'x' in case.tags"} value={meta.trigger_filter}
                                onChange={(e) => setMeta({ ...meta, trigger_filter: e.target.value })} />
                            {meta.trigger_type === 'email.received' && (
                                <p className="text-xs text-fg-3">
                                    Fields: <code>email.to_joined</code>, <code>email.sender</code>, <code>email.subject</code>,{' '}
                                    <code>email.has_attachments</code>, <code>trigger.mailbox_id</code>. Example:{' '}
                                    <code>'soc+phishing' in email.to_joined</code>. Leave empty to match every email.
                                </p>
                            )}
                        </div>
                        {!readOnly && (['Logic', 'Cases', 'Alerts', 'HTTP', 'Slack'] as const).map((cat) => {
                            const items = palette.filter((d) => d.category === cat);
                            if (!items.length) return null;
                            return (
                                <div key={cat}>
                                    <p className="label-mono mb-1">{cat}</p>
                                    {items.map((d) => (
                                        <button type="button" key={d.type} draggable
                                            onDragStart={(e) => { e.dataTransfer.setData('application/wf-node', d.type); e.dataTransfer.effectAllowed = 'move'; }}
                                            onClick={() => addAtCentre(d.type)} title={`Add ${d.label} (or drag onto the canvas)`}
                                            className="w-full flex items-center gap-2 px-2 py-1.5 mb-1 rounded-sm border border-line text-left text-sm text-fg cursor-grab hover:border-line-strong hover:bg-raised bg-surface">
                                            <d.icon size={14} className="text-accent shrink-0" aria-hidden />{d.label}
                                        </button>
                                    ))}
                                </div>
                            );
                        })}
                    </aside>

                    <div id="wf-canvas" className="flex-1 min-w-0 relative" onDragOver={(e) => { e.preventDefault(); e.dataTransfer.dropEffect = 'move'; }} onDrop={onDrop}>
                        <div className="absolute z-10 top-2 left-2 flex gap-1.5 lg:hidden">
                            <Button variant="secondary" size="sm" onClick={() => { setPaletteOpen((o) => !o); setInspectorOpen(false); }}
                                aria-expanded={paletteOpen} aria-controls="wf-palette">
                                <PanelLeft size={14} aria-hidden /> Nodes
                            </Button>
                            <Button variant="secondary" size="sm" onClick={() => { setInspectorOpen((o) => !o); setPaletteOpen(false); }}
                                aria-expanded={inspectorOpen} aria-controls="wf-inspector">
                                <PanelRight size={14} aria-hidden /> Inspector
                            </Button>
                        </div>
                        {graphErrors.length > 0 && (
                            <div role="alert" className="absolute z-10 top-12 lg:top-2 left-2 right-2 rounded-md bg-panel border border-severity-critical/30 text-severity-critical text-xs p-2">
                                <AlertTriangle size={12} className="inline mr-1" aria-hidden />{graphErrors.map((e) => e.message).join(' · ')}
                            </div>
                        )}
                        {errors.length === 0 && loadedVersion !== null && loadedVersion > 0 && (
                            <div className={cn('absolute z-10 top-2 right-2 text-xs flex items-center gap-1', TONE_TEXT.ok)}><CheckCircle2 size={12} aria-hidden />Valid</div>
                        )}
                        <ReactFlow style={FLOW_THEME} nodes={displayNodes} edges={edges} nodeTypes={nodeTypes}
                            onNodesChange={readOnly ? undefined : onNodesChange} onEdgesChange={readOnly ? undefined : onEdgesChange}
                            onConnect={readOnly ? undefined : onConnect}
                            onNodeClick={(_, n) => { setSelectedId(n.id); setInspectorOpen(true); setPaletteOpen(false); }} onPaneClick={() => setSelectedId(null)}
                            nodesDraggable={!readOnly} nodesConnectable={!readOnly} deleteKeyCode={readOnly ? null : undefined} onBeforeDelete={onBeforeDelete} fitView>
                            <Background gap={16} color={FLOW_DOT_COLOR} />
                            <Controls className={FLOW_PANEL_CLASS} />
                            <MiniMap pannable zoomable className={cn(FLOW_PANEL_CLASS, 'hidden! sm:block!')} />
                        </ReactFlow>
                    </div>

                    <aside id="wf-inspector" aria-label="Inspector"
                        className={cn(panelBase, 'right-0 w-80 max-w-[90%] border-l border-line', inspectorOpen ? 'block' : 'hidden lg:block')}>
                        <div className="flex items-center justify-between px-4 pt-3 lg:hidden">
                            <span className="text-sm font-semibold text-fg">Inspector</span>
                            <button type="button" onClick={() => setInspectorOpen(false)} aria-label="Close inspector" className="p-1 rounded-sm text-fg-3 hover:bg-raised"><X size={16} aria-hidden /></button>
                        </div>
                        {selected ? (
                            <NodeInspector key={selected.id} node={selected} readOnly={readOnly} errors={selectedErrors}
                                onChange={updateNode} onRename={renameNode} onDelete={() => deleteNode(selected.id)} />
                        ) : (
                            <div className="p-4 text-sm text-fg-3 space-y-3">
                                <p>Select a node to configure it. Add nodes from the palette (click or drag); connect handles to build the flow.</p>
                                <div>
                                    <Label htmlFor="wf-description" className="label-mono">Description</Label>
                                    <Textarea id="wf-description" rows={3} disabled={readOnly}
                                        value={meta.description} onChange={(e) => setMeta({ ...meta, description: e.target.value })} />
                                </div>
                            </div>
                        )}
                    </aside>
                </div>
            )}
            {dryRunOpen && wf && <DryRunDialog workflow={wf} onClose={() => setDryRunOpen(false)} />}
        </div>
    );
}

export default function WorkflowEditor() {
    return <ReactFlowProvider><Editor /></ReactFlowProvider>;
}
