from typing import List, Optional
from datetime import datetime
from pydantic import BaseModel


class CaseTriageOut(BaseModel):
    id: int
    case_id: int
    triggered_by: str
    status: str
    error_message: Optional[str] = None

    proposed_severity: Optional[str] = None
    proposed_tags: Optional[List[str]] = None
    severity_tags_status: str

    proposed_playbook_template_id: Optional[int] = None
    playbook_status: str

    related_case_ids: Optional[List[int]] = None

    next_steps_text: Optional[str] = None
    next_steps_status: str

    created_at: datetime

    class Config:
        from_attributes = True


class TriageItemUpdate(BaseModel):
    field: str  # 'severity_tags' | 'playbook' | 'next_steps'
    status: str  # 'confirmed' | 'dismissed'
