import { useMemo, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ReactFlow, ReactFlowProvider, Background, Controls } from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import { ArrowLeft, Lock } from 'lucide-react';
import { api } from '../../api/client';
import type { ChildRun, RunDetail as Run, RunStep } from './types';
import { toFlow } from './flow';
import WorkflowNode from './WorkflowNode';
import { StatusBadge } from './RunsTable';

const nodeTypes = { wf: WorkflowNode };
const LIVE = ['running', 'waiting', 'queued'];

// Real placeholders are nonce-bound (`#<16 hex>`); show only the NAME. Nonce-less look-alikes (e.g. from
// alert data) stay plain text. split() with one capture group puts names at odd indices.
const PLACEHOLDER_RE = /⟦secret:([A-Z][A-Z0-9_]{1,63})#[0-9a-f]{16}⟧/g;

function WithChips({ text }: { text: string }) {
    return <>{text.split(PLACEHOLDER_RE).map((part, i) => i % 2 === 0 ? part : (
        <span key={i} title="secret placeholder (value never stored)"
            className="inline-flex items-center gap-1 px-1 py-px mx-0.5 border border-accent/40 bg-accent/10 text-accent font-mono text-[10px] align-baseline">
            <Lock size={9} />{part}</span>
    ))}</>;
}

function Json({ value }: { value: unknown }) {
    return <pre className="text-[11px] font-mono bg-surface border border-line p-2 overflow-x-auto whitespace-pre-wrap break-all"><WithChips text={JSON.stringify(value, null, 2) ?? ''} /></pre>;
}

export function StepPanel({ step, children = [] }: { step: RunStep; children?: ChildRun[] }) {
    const out = step.output ?? {};
    const simulated = (out as Record<string, unknown>).simulated === true;
    return (
        <div className="p-4 space-y-3 text-sm">
            <div className="flex items-center gap-2">
                <span className="font-mono text-fg">{step.node_id}</span>
                <StatusBadge status={step.status} />
                {simulated && <span className="px-1.5 py-0.5 text-[10px] font-bold border border-violet-500/30 bg-violet-500/10 text-violet-300">SIMULATED</span>}
            </div>
            {step.attempt > 0 && <p className="text-xs text-fg-3">retried {step.attempt}×</p>}
            {step.error && <p className="text-xs text-red-300 bg-red-500/10 border border-red-500/30 p-2"><WithChips text={step.error} /></p>}
            {simulated ? (
                <><p className="label-mono">would do</p><Json value={(out as Record<string, unknown>).would_do} /></>
            ) : (
                <><p className="label-mono">input</p><Json value={step.input} /></>
            )}
            <p className="label-mono">output</p><Json value={out} />
            {children.length > 0 && (
                <>
                    <p className="label-mono">items ({children.length})</p>
                    <ul className="divide-y divide-line border border-line">
                        {children.map((c) => (
                            <li key={c.id} className="flex items-center gap-2 px-2 py-1.5 text-xs">
                                <span className="num w-10">#{c.loop_index}</span>
                                <StatusBadge status={c.status} />
                                <Link to={`/workflows/runs/${c.id}`} className="ml-auto text-accent hover:underline">open</Link>
                            </li>
                        ))}
                    </ul>
                </>
            )}
        </div>
    );
}

function Detail() {
    const { runId } = useParams();
    const qc = useQueryClient();
    const [selected, setSelected] = useState<string | null>(null);

    const { data: run } = useQuery({
        queryKey: ['workflow-run', runId],
        queryFn: async () => (await api.get(`/workflow-runs/${runId}`)).data as Run,
        refetchInterval: (q) => (LIVE.includes((q.state.data as Run | undefined)?.status ?? '') ? 2000 : false),
    });

    const cancel = useMutation({
        mutationFn: async () => api.post(`/workflow-runs/${runId}/cancel`),
        onSuccess: () => qc.invalidateQueries({ queryKey: ['workflow-run', runId] }),
    });

    const flow = useMemo(() => {
        if (!run) return { nodes: [], edges: [] };
        const statusByNode = Object.fromEntries(run.steps.map((s) => [s.node_id, s.status]));
        return toFlow(run.graph_snapshot, { statusByNode });
    }, [run]);

    if (!run) return <div className="p-6 text-fg-3">Loading…</div>;
    const step = run.steps.find((s) => s.node_id === selected);

    return (
        <div className="flex flex-col h-[calc(100vh-3.5rem)]">
            <div className="flex items-center gap-3 px-4 py-2 border-b border-line bg-panel flex-wrap">
                <Link to={run.parent_run_id ? `/workflows/runs/${run.parent_run_id}` : `/workflows/${run.workflow_id}`} className="p-1 text-fg-3 hover:text-fg" aria-label="Back"><ArrowLeft size={18} /></Link>
                <span className="font-semibold text-fg">{run.workflow_name}</span>
                <span className="num text-xs text-muted">run {run.id} · v{run.workflow_version}{run.loop_index !== null && ` · item ${run.loop_index}`}</span>
                <StatusBadge status={run.status} />
                {run.is_dry_run && <span className="px-1.5 py-0.5 text-[10px] font-bold border border-violet-500/30 bg-violet-500/10 text-violet-300">DRY RUN</span>}
                {run.case_id && <Link to={`/cases/${run.case_id}`} className="text-xs text-accent hover:underline">case {run.case_id}</Link>}
                {run.alert_id && <span className="text-xs text-fg-3">alert {run.alert_id}</span>}
                {run.error && <span className="text-xs text-red-300"><WithChips text={run.error} /></span>}
                {LIVE.includes(run.status) && (
                    <button onClick={() => cancel.mutate()} className="ml-auto h-8 px-3 border border-red-500/30 text-red-300 text-sm hover:bg-red-500/10">Cancel run</button>
                )}
            </div>
            <div className="flex flex-1 min-h-0">
                <div className="flex-1">
                    <ReactFlow colorMode="dark" nodes={flow.nodes} edges={flow.edges} nodeTypes={nodeTypes} nodesDraggable={false} nodesConnectable={false}
                        onNodeClick={(_, n) => setSelected(n.id)} fitView>
                        <Background gap={16} color="#27272a" />
                        <Controls showInteractive={false} />
                    </ReactFlow>
                </div>
                <aside className="w-96 border-l border-line bg-panel overflow-y-auto">
                    {step ? <StepPanel step={step} children={run.children.filter((c) => c.parent_step_id === step.id)} /> : (
                        <div className="p-4 space-y-3 text-sm">
                            <p className="text-fg-3">Click a node to see its input and output.</p>
                            <p className="label-mono">trigger payload</p><Json value={run.trigger_payload} />
                            {run.loop_item !== null && run.loop_item !== undefined && <><p className="label-mono">loop item</p><Json value={run.loop_item} /></>}
                        </div>
                    )}
                </aside>
            </div>
        </div>
    );
}

export default function RunDetail() {
    return <ReactFlowProvider><Detail /></ReactFlowProvider>;
}
