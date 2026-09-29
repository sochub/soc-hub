import type { Edge, Node } from '@xyflow/react';
import { NODE_DEF } from './nodeCatalog';
import type { WfGraph, WfNode } from './types';

export interface WfNodeData { wf: WfNode; status?: string; invalid?: boolean; [k: string]: unknown }
export type WfFlowNode = Node<WfNodeData, 'wf'>;

const DEFAULT_CONTAINER = { width: 420, height: 260 };

export function toFlow(graph: WfGraph, opts: { statusByNode?: Record<string, string>; invalidIds?: Set<string> } = {}) {
    // React Flow requires parents before their children in the array.
    const sorted = [...graph.nodes].sort((a, b) => Number(!!a.parent_id) - Number(!!b.parent_id));
    const nodes: WfFlowNode[] = sorted.map((n) => {
        const container = NODE_DEF[n.type]?.container;
        return {
            id: n.id,
            type: 'wf',
            position: n.position ?? { x: 0, y: 0 },
            data: { wf: n, status: opts.statusByNode?.[n.id], invalid: opts.invalidIds?.has(n.id) },
            ...(n.parent_id ? { parentId: n.parent_id, extent: 'parent' as const } : {}),
            ...(container ? { style: { ...(n.size ?? DEFAULT_CONTAINER) }, zIndex: -1 } : {}),
        };
    });
    const edges: Edge[] = graph.edges.map((e) => ({
        id: e.id, source: e.source, target: e.target,
        sourceHandle: e.source_handle ?? undefined,
        label: e.source_handle ?? undefined,
    }));
    return { nodes, edges };
}

export function fromFlow(nodes: WfFlowNode[], edges: Edge[]): WfGraph {
    return {
        nodes: nodes.map((n) => {
            const container = NODE_DEF[n.data.wf.type]?.container;
            const size = container
                ? { width: Math.round(n.measured?.width ?? Number(n.style?.width ?? DEFAULT_CONTAINER.width)),
                    height: Math.round(n.measured?.height ?? Number(n.style?.height ?? DEFAULT_CONTAINER.height)) }
                : undefined;
            return { ...n.data.wf, id: n.id, position: n.position, parent_id: n.parentId ?? null, ...(size ? { size } : {}) };
        }),
        edges: edges.map((e) => ({
            id: e.id, source: e.source, target: e.target,
            source_handle: (e.sourceHandle === 'true' || e.sourceHandle === 'false') ? e.sourceHandle : null,
        })),
    };
}

export function nextNodeId(type: string, taken: Set<string>): string {
    const base = type.replace(/[^a-z0-9_]/g, '_');
    let i = 1;
    while (taken.has(`${base}_${i}`)) i++;
    return `${base}_${i}`;
}
