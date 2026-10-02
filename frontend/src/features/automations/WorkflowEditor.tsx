import { useCallback, useEffect, useMemo, useRef, useState, type DragEvent } from 'react';
import { useNavigate, useParams, Link } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
    ReactFlow, ReactFlowProvider, Background, Controls, MiniMap, addEdge, useEdgesState, useNodesState, useReactFlow,
    type Connection, type Edge,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import { ArrowLeft, Save, CheckCircle2, AlertTriangle, FlaskConical } from 'lucide-react';
import { api } from '../../api/client';
import { cn } from '../../lib/utils';
import type { User } from '../../types';
import type { TriggerType, ValidationError, Workflow, WfGraph, WfNode } from './types';
import { NODE_DEF, NODE_DEFS, TRIGGER_LABEL } from './nodeCatalog';
import { fromFlow, nextNodeId, toFlow, type WfFlowNode } from './flow';
import WorkflowNode from './WorkflowNode';
import NodeInspector from './NodeInspector';
import RunsTable from './RunsTable';
import DryRunDialog from './DryRunDialog';

type ApiErr = { response?: { data?: { detail?: { errors?: ValidationError[]; message?: string } } } };
const nodeTypes = { wf: WorkflowNode };
const EMPTY_GRAPH: WfGraph = { nodes: [{ id: 'start', type: 'trigger', position: { x: 250, y: 40 }, config: {} }], edges: [] };

interface Meta { name: string; description: string; trigger_type: TriggerType; trigger_filter: string }

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

    const [meta, setMeta] = useState<Meta>({ name: 'Untitled workflow', description: '', trigger_type: 'case.created', trigger_filter: '' });
    const [nodes, setNodes, onNodesChange] = useNodesState<WfFlowNode>([]);
    const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);
    const [selectedId, setSelectedId] = useState<string | null>(null);
    const [errors, setErrors] = useState<ValidationError[]>([]);
    const [tab, setTab] = useState<'editor' | 'runs'>('editor');
    const [dryRunOpen, setDryRunOpen] = useState(false);
    const [banner, setBanner] = useState<string | null>(null);
    const [loadedVersion, setLoadedVersion] = useState<number | null>(null);
    // id of the workflow the canvas was last hydrated for; server responses never re-hydrate after that
    const hydratedFor = useRef<string | null>(null);

    /* eslint-disable react-hooks/set-state-in-effect -- one-time hydration of editor state from fetched workflow */
    useEffect(() => {
        if (isNew && hydratedFor.current !== 'new') {
            hydratedFor.current = 'new';
            const f = toFlow(EMPTY_GRAPH); setNodes(f.nodes); setEdges(f.edges); setLoadedVersion(0);
        }
        if (wf && hydratedFor.current !== String(wf.id)) {
            hydratedFor.current = String(wf.id);
            setMeta({ name: wf.name, description: wf.description ?? '', trigger_type: wf.trigger_type, trigger_filter: wf.trigger_filter ?? '' });
            const invalid = new Set(wf.validation_errors.map((e) => e.node_id).filter(Boolean) as string[]);
            const f = toFlow(wf.graph, { invalidIds: invalid });
            setNodes(f.nodes); setEdges(f.edges); setErrors(wf.validation_errors); setLoadedVersion(wf.version);
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
        if (nodes.some((n) => n.id === newId)) { setBanner(`Node id "${newId}" already exists`); return false; }
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
            const body = { ...meta, trigger_filter: meta.trigger_filter || null, description: meta.description || null, graph: fromFlow(nodes, edges) };
            return (isNew ? await api.post('/workflows/', body) : await api.put(`/workflows/${id}`, body)).data as Workflow;
        },
        onSuccess: (saved) => {
            hydratedFor.current = String(saved.id);
            setLoadedVersion(saved.version);
            setErrors(saved.validation_errors);
            setBanner(saved.validation_errors.length ? `Saved with ${saved.validation_errors.length} problem(s)` : 'Saved');
            qc.invalidateQueries({ queryKey: ['workflows'] });
            qc.setQueryData(['workflow', String(saved.id)], saved);
            if (isNew) navigate(`/automations/${saved.id}`, { replace: true });
        },
        onError: (err: ApiErr) => {
            const detail = err?.response?.data?.detail;
            if (detail?.errors) setErrors(detail.errors);
            setBanner(detail?.message ?? 'Save failed');
        },
    });

    const toggle = useMutation({
        mutationFn: async () => (await api.post(`/workflows/${id}/${wf?.enabled ? 'disable' : 'enable'}`)).data as Workflow,
        onSuccess: (w) => { qc.setQueryData(['workflow', id], w); qc.invalidateQueries({ queryKey: ['workflows'] }); setBanner(w.enabled ? 'Enabled' : 'Disabled'); },
        onError: (err: ApiErr) => { setErrors(err?.response?.data?.detail?.errors ?? []); setBanner('Fix the problems below before enabling'); },
    });

    const invalidIds = useMemo(() => new Set(errors.map((e) => e.node_id).filter(Boolean) as string[]), [errors]);
    const displayNodes = useMemo(() => nodes.map((n) => ({ ...n, data: { ...n.data, invalid: invalidIds.has(n.id) } })), [nodes, invalidIds]);

    const palette = NODE_DEFS.filter((d) => d.type !== 'trigger' && (!d.alertOnly || meta.trigger_type === 'alert.ingested'));

    return (
        <div className="flex flex-col h-[calc(100vh-3.5rem)]">
            <div className="flex items-center gap-3 px-4 py-2 border-b border-zinc-200 bg-white flex-wrap">
                <Link to="/automations" className="p-1 text-zinc-500 hover:text-zinc-900" aria-label="Back"><ArrowLeft size={18} /></Link>
                <input className="text-base font-semibold text-zinc-900 border-b border-transparent focus:border-accent-500 outline-none min-w-[220px]"
                    disabled={readOnly} value={meta.name} onChange={(e) => setMeta({ ...meta, name: e.target.value })} aria-label="Workflow name" />
                {wf && <span className="num text-xs text-zinc-400">v{wf.version}</span>}
                <div className="flex gap-3 ml-2">
                    {(['editor', 'runs'] as const).map((t) => (
                        <button key={t} disabled={isNew && t === 'runs'} onClick={() => setTab(t)}
                            className={cn('text-sm capitalize', tab === t ? 'text-accent-700 font-medium' : 'text-zinc-500')}>{t}</button>
                    ))}
                </div>
                <div className="ml-auto flex items-center gap-2">
                    {banner && <span className="text-xs text-zinc-600">{banner}</span>}
                    {!readOnly && (
                        <button onClick={() => save.mutate()} disabled={save.isPending}
                            className="inline-flex items-center gap-1.5 h-8 px-3 bg-accent-600 text-white text-sm hover:bg-accent-700 disabled:opacity-50">
                            <Save size={14} /> Save
                        </button>
                    )}
                    {!readOnly && !isNew && wf && (
                        <button onClick={() => setDryRunOpen(true)} title="Uses the last saved version"
                            className="inline-flex items-center gap-1.5 h-8 px-3 border border-violet-300 text-violet-700 text-sm hover:bg-violet-50">
                            <FlaskConical size={14} /> Dry run
                        </button>
                    )}
                    {!readOnly && !isNew && (
                        <button onClick={() => toggle.mutate()} disabled={toggle.isPending}
                            className={cn('h-8 px-3 text-sm border', wf?.enabled ? 'border-emerald-300 text-emerald-700 bg-emerald-50' : 'border-zinc-300 text-zinc-700')}>
                            {wf?.enabled ? 'Enabled' : 'Disabled'}
                        </button>
                    )}
                </div>
            </div>

            {tab === 'runs' && wf ? <div className="p-4 overflow-auto"><RunsTable workflowId={wf.id} /></div> : (
                <div className="flex flex-1 min-h-0">
                    <aside className="w-56 border-r border-zinc-200 bg-white overflow-y-auto p-3 space-y-4">
                        <div className="space-y-2">
                            <p className="label-mono">trigger</p>
                            <select className="w-full border border-zinc-300 px-2 py-1.5 text-sm" disabled={readOnly} value={meta.trigger_type}
                                onChange={(e) => setMeta({ ...meta, trigger_type: e.target.value as TriggerType })}>
                                {Object.entries(TRIGGER_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
                            </select>
                            <textarea rows={2} className="w-full border border-zinc-300 px-2 py-1.5 font-mono text-xs" disabled={readOnly}
                                placeholder="filter, e.g. 'x' in case.tags" value={meta.trigger_filter}
                                onChange={(e) => setMeta({ ...meta, trigger_filter: e.target.value })} />
                        </div>
                        {!readOnly && (['Logic', 'Cases', 'Alerts', 'HTTP', 'Slack'] as const).map((cat) => {
                            const items = palette.filter((d) => d.category === cat);
                            if (!items.length) return null;
                            return (
                                <div key={cat}>
                                    <p className="label-mono mb-1">{cat}</p>
                                    {items.map((d) => (
                                        <div key={d.type} draggable onDragStart={(e) => { e.dataTransfer.setData('application/wf-node', d.type); e.dataTransfer.effectAllowed = 'move'; }}
                                            onClick={() => addNode(d.type, screenToFlowPosition({ x: window.innerWidth / 2, y: window.innerHeight / 2 }))}
                                            className="flex items-center gap-2 px-2 py-1.5 mb-1 border border-zinc-200 text-sm cursor-grab hover:border-accent-400 bg-zinc-50">
                                            <d.icon size={14} className="text-accent-600" />{d.label}
                                        </div>
                                    ))}
                                </div>
                            );
                        })}
                    </aside>

                    <div className="flex-1 relative" onDragOver={(e) => { e.preventDefault(); e.dataTransfer.dropEffect = 'move'; }} onDrop={onDrop}>
                        {graphErrors.length > 0 && (
                            <div className="absolute z-10 top-2 left-2 right-2 bg-red-50 border border-red-200 text-red-700 text-xs p-2">
                                <AlertTriangle size={12} className="inline mr-1" />{graphErrors.map((e) => e.message).join(' · ')}
                            </div>
                        )}
                        {errors.length === 0 && loadedVersion !== null && loadedVersion > 0 && (
                            <div className="absolute z-10 top-2 right-2 text-xs text-emerald-700 flex items-center gap-1"><CheckCircle2 size={12} />valid</div>
                        )}
                        <ReactFlow nodes={displayNodes} edges={edges} nodeTypes={nodeTypes}
                            onNodesChange={readOnly ? undefined : onNodesChange} onEdgesChange={readOnly ? undefined : onEdgesChange}
                            onConnect={readOnly ? undefined : onConnect}
                            onNodeClick={(_, n) => setSelectedId(n.id)} onPaneClick={() => setSelectedId(null)}
                            nodesDraggable={!readOnly} nodesConnectable={!readOnly} deleteKeyCode={readOnly ? null : undefined} onBeforeDelete={onBeforeDelete} fitView>
                            <Background gap={16} color="#e4e4e7" />
                            <Controls />
                            <MiniMap pannable zoomable />
                        </ReactFlow>
                    </div>

                    <aside className="w-80 border-l border-zinc-200 bg-white overflow-y-auto">
                        {selected ? (
                            <NodeInspector key={selected.id} node={selected} readOnly={readOnly} errors={selectedErrors}
                                onChange={updateNode} onRename={renameNode} onDelete={() => deleteNode(selected.id)} />
                        ) : (
                            <div className="p-4 text-sm text-zinc-500 space-y-3">
                                <p>Select a node to configure it. Drag nodes from the left palette; connect handles to build the flow.</p>
                                <label className="block"><span className="label-mono">description</span>
                                    <textarea rows={3} className="w-full border border-zinc-300 px-2 py-1.5 text-sm" disabled={readOnly}
                                        value={meta.description} onChange={(e) => setMeta({ ...meta, description: e.target.value })} /></label>
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
