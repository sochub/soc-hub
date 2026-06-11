from typing import List, Optional
from datetime import datetime
from pydantic import BaseModel


class TaskTemplateIn(BaseModel):
    phase: str = "identification"
    title: str
    description: Optional[str] = None
    order: int = 0


class TaskTemplateOut(TaskTemplateIn):
    id: int

    class Config:
        from_attributes = True


class PlaybookTemplateCreate(BaseModel):
    name: str
    category: str = "other"
    description: Optional[str] = None
    tasks: List[TaskTemplateIn] = []


class PlaybookTemplateUpdate(BaseModel):
    name: Optional[str] = None
    category: Optional[str] = None
    description: Optional[str] = None
    tasks: Optional[List[TaskTemplateIn]] = None  # when present, replaces all tasks


class PlaybookTemplateOut(BaseModel):
    id: int
    tenant_id: Optional[int] = None
    name: str
    category: str
    description: Optional[str] = None
    is_system: bool
    source_template_id: Optional[int] = None
    created_at: datetime
    tasks: List[TaskTemplateOut] = []

    class Config:
        from_attributes = True


class PlaybookTemplateSummary(BaseModel):
    """Lighter row for list/marketplace views (task_count instead of full tasks)."""
    id: int
    tenant_id: Optional[int] = None
    name: str
    category: str
    description: Optional[str] = None
    is_system: bool
    source_template_id: Optional[int] = None
    task_count: int = 0
    already_imported: bool = False


class PlaybookImportRequest(BaseModel):
    template_ids: List[int]


class PlaybookImportResult(BaseModel):
    imported: int
    skipped: int
    imported_ids: List[int] = []
