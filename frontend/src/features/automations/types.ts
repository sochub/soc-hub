export type TriggerType = 'case.created' | 'case.updated' | 'alert.ingested' | 'manual';

export interface WfNode {
    id: string;
    type: string;
    position: { x: number; y: number };
    config: Record<string, unknown>;
    parent_id?: string | null;
    size?: { width: number; height: number };
    continue_on_error?: boolean;
    retries?: number;
    execute_in_dry_run?: boolean;
    mock_output?: Record<string, unknown> | null;
}

export interface WfEdge { id: string; source: string; target: string; source_handle: 'true' | 'false' | null }
export interface WfGraph { nodes: WfNode[]; edges: WfEdge[] }
export interface ValidationError { node_id: string | null; message: string }

export interface WorkflowSummary {
    id: number;
    name: string;
    description: string | null;
    enabled: boolean;
    trigger_type: TriggerType;
    version: number;
    updated_at: string | null;
    created_at: string | null;
    last_run_status: string | null;
    last_run_at: string | null;
    run_count: number;
}

export interface Workflow extends WorkflowSummary {
    trigger_filter: string | null;
    graph: WfGraph;
    validation_errors: ValidationError[];
}

export interface RunSummary {
    id: number;
    workflow_id: number;
    workflow_name: string | null;
    workflow_version: number;
    status: string;
    case_id: number | null;
    alert_id: number | null;
    is_dry_run: boolean;
    depth: number;
    error: string | null;
    started_at: string | null;
    finished_at: string | null;
}

export interface RunStep {
    id: number;
    node_id: string;
    status: string;
    input: unknown;
    output: Record<string, unknown> | null;
    error: string | null;
    attempt: number;
    started_at: string | null;
    finished_at: string | null;
}

export interface ChildRun { id: number; parent_step_id: number | null; loop_index: number | null; status: string; error: string | null }

export interface RunDetail extends RunSummary {
    trigger_payload: unknown;
    graph_snapshot: WfGraph;
    parent_run_id: number | null;
    loop_index: number | null;
    loop_item: unknown;
    steps: RunStep[];
    children: ChildRun[];
}
