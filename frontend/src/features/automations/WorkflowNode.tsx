import { Handle, Position, NodeResizer, type NodeProps } from '@xyflow/react';
import { cn } from '../../lib/utils';
import { NODE_DEF } from './nodeCatalog';
import type { WfFlowNode } from './flow';

const RING: Record<string, string> = {
    succeeded: 'border-emerald-500', failed: 'border-red-500', skipped: 'border-zinc-300 opacity-60',
    waiting: 'border-amber-500', running: 'border-accent-500', pending: 'border-accent-300', cancelled: 'border-zinc-400',
};

export default function WorkflowNode({ id, data, selected }: NodeProps<WfFlowNode>) {
    const def = NODE_DEF[data.wf.type];
    const Icon = def?.icon;
    const isTrigger = data.wf.type === 'trigger';
    if (def?.container) {
        return (
            <div className={cn('w-full h-full border-2 border-dashed bg-accent-50/40',
                data.status ? RING[data.status] : 'border-accent-300', data.invalid && 'border-red-500', selected && 'ring-2 ring-accent-400')}>
                <NodeResizer isVisible={selected} minWidth={260} minHeight={160} />
                <Handle type="target" position={Position.Top} className="bg-zinc-500! w-2.5! h-2.5! rounded-none!" />
                <div className="flex items-center gap-2 px-3 py-1.5 border-b border-dashed border-accent-200 bg-white/70">
                    {Icon && <Icon size={14} className="text-accent-600" />}
                    <span className="text-sm font-medium text-zinc-900">{def.label}</span>
                    <span className="font-mono text-[10px] text-zinc-500">{id}</span>
                    {data.status && <span className="ml-auto font-mono text-[10px] uppercase text-zinc-500">{data.status}</span>}
                </div>
                <p className="px-3 py-1 text-[10px] font-mono text-zinc-500">runs once per item · loop.item · loop.index</p>
                <Handle type="source" position={Position.Bottom} className="bg-zinc-500! w-2.5! h-2.5! rounded-none!" />
            </div>
        );
    }
    return (
        <div className={cn('bg-white border-2 min-w-[180px] shadow-xs',
            data.status ? RING[data.status] : 'border-zinc-300',
            data.invalid && 'border-red-500', selected && 'ring-2 ring-accent-400')}>
            {!isTrigger && <Handle type="target" position={Position.Top} className="bg-zinc-500! w-2.5! h-2.5! rounded-none!" />}
            <div className="flex items-center gap-2 px-3 py-2 border-b border-zinc-100">
                {Icon && <Icon size={14} className="text-accent-600 shrink-0" />}
                <span className="text-sm font-medium text-zinc-900">{def?.label ?? data.wf.type}</span>
            </div>
            <div className="px-3 py-1 flex items-center justify-between gap-2">
                <span className="font-mono text-[10px] text-zinc-500">{id}</span>
                {data.status && <span className="font-mono text-[10px] uppercase text-zinc-500">{data.status}</span>}
            </div>
            {def?.branching ? (
                <>
                    <Handle type="source" id="true" position={Position.Bottom} style={{ left: '30%' }} className="bg-emerald-600! w-2.5! h-2.5! rounded-none!" />
                    <Handle type="source" id="false" position={Position.Bottom} style={{ left: '70%' }} className="bg-red-600! w-2.5! h-2.5! rounded-none!" />
                    <div className="flex justify-between px-6 pb-1 font-mono text-[9px] text-zinc-400"><span>true</span><span>false</span></div>
                </>
            ) : (
                <Handle type="source" position={Position.Bottom} className="bg-zinc-500! w-2.5! h-2.5! rounded-none!" />
            )}
        </div>
    );
}
