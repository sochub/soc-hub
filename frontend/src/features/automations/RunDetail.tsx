import { useMemo, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ReactFlow, ReactFlowProvider, Background, Controls } from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import { ArrowLeft, Lock } from 'lucide-react';
import { api } from '../../api/client';
import type { ChildRun, RunDetail as Run, RunStep } from './types';
import { FLOW_DOT_COLOR, FLOW_PANEL_CLASS, FLOW_THEME, toFlow } from './flow';
import { NODE_DEF } from './nodeCatalog';
import WorkflowNode from './WorkflowNode';
import { RelTime, StatusBadge } from './RunsTable';
import { Badge, Button, ErrorState, Skeleton } from '../../components/ui';
import { cn } from '../../lib/utils';

const nodeTypes = { wf: WorkflowNode };
const LIVE = ['running', 'waiting', 'queued'];

// Real placeholders are nonce-bound (`#<16 hex>`); show only the NAME. Nonce-less look-alikes (e.g. from
// alert data) stay plain text. split() with one capture group puts names at odd indices.
const PLACEHOLDER_RE = /⟦secret:([A-Z][A-Z0-9_]{1,63})#[0-9a-f]{16}⟧/g;

function WithChips({ text }: { text: string }) {
    return <>{text.split(PLACEHOLDER_RE).map((part, i) => i % 2 === 0 ? part : (
        <span key={i} title="secret placeholder (value never stored)"
            className="inline-flex items-center gap-1 px-1 py-px mx-0.5 border border-accent/40 bg-accent/10 text-accent font-mono text-xs align-baseline rounded-sm">
            <Lock size={10} aria-hidden />{part}</span>
    ))}</>;
}

function Json({ value }: { value: unknown }) {
    return <pre className="text-xs font-mono bg-surface border border-line rounded-sm p-2 overflow-x-auto whitespace-pre-wrap break-all"><WithChips text={JSON.stringify(value, null, 2) ?? ''} /></pre>;
}

export function StepPanel({ step, children = [] }: { step: RunStep; children?: ChildRun[] }) {
    const out = step.output ?? {};
    const simulated = (out as Record<string, unknown>).simulated === true;
    return (
        <div className="p-4 space-y-3 text-sm">
            <div className="flex items-center gap-2">
                <span className="font-mono text-fg">{step.node_id}</span>
                <StatusBadge status={step.status} />
                {simulated && <Badge>Simulated</Badge>}
            </div>
            {step.attempt > 0 && <p className="text-xs text-fg-3">retried {step.attempt}×</p>}
            {step.error && <p className="text-xs text-severity-critical bg-severity-critical/5 border border-severity-critical/30 rounded-sm p-2"><WithChips text={step.error} /></p>}
            {simulated ? (
                <><p className="label-mono">would do</p><Json value={(out as Record<string, unknown>).would_do} /></>
            ) : (
                <><p className="label-mono">input</p><Json value={step.input} /></>
            )}
            <p className="label-mono">output</p><Json value={out} />
            {children.length > 0 && (
                <>
                    <p className="label-mono">items ({children.length})</p>
                    <ul className="divide-y divide-line border border-line rounded-sm">
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

/** Plain-text form for titles/truncated cells: placeholder names only, never the nonce. */
const maskNonce = (t: string | null) => t ? t.replace(PLACEHOLDER_RE, '⟦secret:$1⟧') : undefined;

function stepDuration(s: RunStep) {
    if (!s.started_at || !s.finished_at) return '—';
    const ms = new Date(s.finished_at).getTime() - new Date(s.started_at).getTime();
    return ms < 1000 ? `${ms}ms` : ms < 60_000 ? `${(ms / 1000).toFixed(1)}s` : `${Math.floor(ms / 60_000)}m ${Math.round((ms % 60_000) / 1000)}s`;
}

function Detail() {
    const { runId } = useParams();
    const qc = useQueryClient();
    const [selected, setSelected] = useState<string | null>(null);

    const { data: run, isLoading, isError, refetch } = useQuery({
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

    if (isLoading) return <div className="p-6"><Skeleton rows={6} /></div>;
    if (isError || !run) return <div className="p-6"><ErrorState message="Could not load this run." onRetry={() => refetch()} /></div>;
    const step = run.steps.find((s) => s.node_id === selected);
    const typeOf = Object.fromEntries(run.graph_snapshot.nodes.map((n) => [n.id, n.type]));

    return (
        <div className="flex flex-col h-[calc(100vh-3.5rem)]">
            <div className="flex items-center gap-3 px-4 py-2 border-b border-line bg-panel flex-wrap">
                <Link to={run.parent_run_id ? `/workflows/runs/${run.parent_run_id}` : `/workflows/${run.workflow_id}`} className="p-1 text-fg-3 hover:text-fg" aria-label="Back"><ArrowLeft size={18} aria-hidden /></Link>
                <span className="font-semibold text-fg">{run.workflow_name}</span>
                <span className="num text-xs text-muted">run {run.id} · v{run.workflow_version}{run.loop_index !== null && ` · item ${run.loop_index}`}</span>
                <StatusBadge status={run.status} />
                {run.is_dry_run && <Badge>Dry run</Badge>}
                {run.case_id && <Link to={`/cases/${run.case_id}`} className="text-xs text-accent hover:underline">case {run.case_id}</Link>}
                {run.alert_id && <span className="text-xs text-fg-3">alert {run.alert_id}</span>}
                {run.error && <span className="text-xs text-severity-critical"><WithChips text={run.error} /></span>}
                {LIVE.includes(run.status) && (
                    <Button variant="danger" size="sm" className="ml-auto" onClick={() => cancel.mutate()} disabled={cancel.isPending}>Cancel run</Button>
                )}
            </div>
            <div className="flex flex-1 min-h-0 flex-col lg:flex-row overflow-y-auto lg:overflow-hidden">
                <div className="flex flex-col lg:flex-1 lg:min-w-0 lg:min-h-0">
                    <div className="h-[45vh] lg:h-auto lg:flex-1 lg:min-h-[240px]">
                        <ReactFlow style={FLOW_THEME} nodes={flow.nodes} edges={flow.edges} nodeTypes={nodeTypes} nodesDraggable={false} nodesConnectable={false}
                            onNodeClick={(_, n) => setSelected(n.id)} fitView>
                            <Background gap={16} color={FLOW_DOT_COLOR} />
                            <Controls showInteractive={false} className={FLOW_PANEL_CLASS} />
                        </ReactFlow>
                    </div>
                    <section aria-label="Steps" className="border-t border-line bg-panel lg:max-h-[40%] overflow-auto">
                        <table className="w-full text-sm">
                            <thead className="text-left label-caps sticky top-0 bg-panel">
                                <tr><th className="px-3 py-2">Step</th><th className="px-3 py-2">Type</th><th className="px-3 py-2">Status</th>
                                    <th className="px-3 py-2">Started</th><th className="px-3 py-2">Duration</th><th className="px-3 py-2">Error</th></tr>
                            </thead>
                            <tbody className="divide-y divide-line">
                                {run.steps.length === 0 && <tr><td colSpan={6} className="px-3 py-6 text-center text-fg-3">No steps have run yet.</td></tr>}
                                {run.steps.map((s) => (
                                    <tr key={s.id} onClick={() => setSelected(s.node_id)}
                                        className={cn('cursor-pointer hover:bg-raised', selected === s.node_id && 'bg-raised')}>
                                        <td className="px-3 py-2.5">
                                            <button type="button" onClick={(e) => { e.stopPropagation(); setSelected(s.node_id); }}
                                                aria-pressed={selected === s.node_id} className="font-mono text-xs text-accent hover:underline">{s.node_id}</button>
                                        </td>
                                        <td className="px-3 py-2.5 text-xs text-fg-2">{NODE_DEF[typeOf[s.node_id]]?.label ?? typeOf[s.node_id] ?? '—'}</td>
                                        <td className="px-3 py-2.5"><span className="flex items-center gap-1.5"><StatusBadge status={s.status} />
                                            {s.attempt > 0 && <span className="num text-xs text-fg-3">retried {s.attempt}×</span>}</span></td>
                                        <td className="px-3 py-2.5 text-xs text-fg-3">{s.started_at ? <RelTime iso={s.started_at} /> : '—'}</td>
                                        <td className="px-3 py-2.5 num text-xs">{stepDuration(s)}</td>
                                        <td className="px-3 py-2.5 text-xs text-severity-critical max-w-[28ch] truncate" title={maskNonce(s.error)}>{maskNonce(s.error)}</td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </section>
                </div>
                <aside aria-label="Step detail" className="w-full lg:w-96 shrink-0 border-t lg:border-t-0 lg:border-l border-line bg-panel lg:overflow-y-auto">
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
