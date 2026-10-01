from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

TriggerType = Literal["case.created", "case.updated", "alert.ingested", "manual"]


class WorkflowIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: Optional[str] = None
    trigger_type: TriggerType
    trigger_filter: Optional[str] = None
    graph: Dict[str, Any]


class WorkflowSummary(BaseModel):
    id: int
    name: str
    description: Optional[str]
    enabled: bool
    trigger_type: str
    version: int
    updated_at: Optional[datetime]
    created_at: Optional[datetime]
    last_run_status: Optional[str] = None
    last_run_at: Optional[datetime] = None
    run_count: int = 0


class WorkflowOut(WorkflowSummary):
    trigger_filter: Optional[str]
    graph: Dict[str, Any]
    validation_errors: List[Dict[str, Any]] = []


class ManualRunIn(BaseModel):
    case_id: int


class DryRunIn(BaseModel):
    case_id: Optional[int] = None
    alert_id: Optional[int] = None
    payload: Optional[Dict[str, Any]] = None
    mocks: Dict[str, Dict[str, Any]] = {}
    ask_user_answers: Dict[str, str] = {}


class AllowlistIn(BaseModel):
    hosts: List[str]


class RunSummary(BaseModel):
    id: int
    workflow_id: int
    workflow_name: Optional[str] = None
    workflow_version: int
    status: str
    case_id: Optional[int]
    alert_id: Optional[int]
    is_dry_run: bool
    depth: int
    error: Optional[str]
    started_at: Optional[datetime]
    finished_at: Optional[datetime]

    class Config:
        from_attributes = True


class StepOut(BaseModel):
    id: int
    node_id: str
    status: str
    input: Optional[Any]
    output: Optional[Any]
    error: Optional[str]
    attempt: int
    started_at: Optional[datetime]
    finished_at: Optional[datetime]

    class Config:
        from_attributes = True


class ChildRunOut(BaseModel):
    id: int
    parent_step_id: Optional[int]
    loop_index: Optional[int]
    status: str
    error: Optional[str]

    class Config:
        from_attributes = True


class RunDetail(RunSummary):
    trigger_payload: Optional[Any]
    graph_snapshot: Dict[str, Any]
    parent_run_id: Optional[int]
    loop_index: Optional[int]
    loop_item: Optional[Any]
    steps: List[StepOut]
    children: List[ChildRunOut]
