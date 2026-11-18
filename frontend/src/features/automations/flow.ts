import type { CSSProperties } from 'react';
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

/** React Flow theming from the app tokens (both themes), set as CSS variables on the <ReactFlow> element. */
export const FLOW_THEME = {
    '--xy-background-color': 'var(--color-canvas)',
    '--xy-edge-stroke': 'var(--color-fg-3)',
    '--xy-edge-stroke-selected': 'var(--color-accent)',
    '--xy-connectionline-stroke': 'var(--color-accent)',
    '--xy-edge-label-background-color': 'var(--color-panel)',
    '--xy-edge-label-color': 'var(--color-fg-2)',
    '--xy-controls-button-background-color': 'var(--color-panel)',
    '--xy-controls-button-background-color-hover': 'var(--color-raised)',
    '--xy-controls-button-color': 'var(--color-fg-2)',
    '--xy-controls-button-color-hover': 'var(--color-fg)',
    '--xy-controls-button-border-color': 'var(--color-line)',
    '--xy-controls-box-shadow': 'none',
    '--xy-minimap-background-color': 'var(--color-panel)',
    '--xy-minimap-mask-background-color': 'color-mix(in srgb, var(--color-canvas) 70%, transparent)',
    '--xy-minimap-mask-stroke-color': 'var(--color-line-strong)',
    '--xy-minimap-node-background-color': 'var(--color-raised-2)',
    '--xy-minimap-node-stroke-color': 'var(--color-line-strong)',
    '--xy-attribution-background-color': 'transparent',
} as CSSProperties;

/** Grid dots, MiniMap and Controls frame, shared by the editor and run detail. */
export const FLOW_DOT_COLOR = 'var(--color-line-strong)';
export const FLOW_PANEL_CLASS = 'rounded-md border border-line overflow-hidden';
