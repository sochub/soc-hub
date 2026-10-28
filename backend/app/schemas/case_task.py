from typing import Optional
from datetime import datetime
from pydantic import BaseModel


class CaseTaskCreate(BaseModel):
    phase: str = "identification"
    title: str
    description: Optional[str] = None
    workflow_id: Optional[int] = None  # a manual workflow the analyst can run from the task


class CaseTaskUpdate(BaseModel):
    phase: Optional[str] = None
    title: Optional[str] = None
    description: Optional[str] = None
    status: Optional[str] = None  # 'todo' | 'done'
    workflow_id: Optional[int] = None  # sent explicitly as null: unlink


class CaseTaskOut(BaseModel):
    id: int
    case_id: int
    phase: str
    title: str
    description: Optional[str] = None
    status: str
    order: int
    completed_at: Optional[datetime] = None
    completed_by: Optional[int] = None
    created_at: datetime
    workflow_id: Optional[int] = None
    workflow_name: Optional[str] = None
    workflow_runnable: bool = False  # enabled + manual: the Run button works
    last_run_id: Optional[int] = None
    last_run_status: Optional[str] = None
    result: Optional[str] = None
    result_status: Optional[str] = None
    result_at: Optional[datetime] = None
    ticket: Optional[dict] = None  # its Jira/HaloPSA/ConnectWise ticket, when ticketing is set up

    class Config:
        from_attributes = True
