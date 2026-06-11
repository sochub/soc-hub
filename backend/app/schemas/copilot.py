from typing import List, Optional
from datetime import datetime
from pydantic import BaseModel

from app.schemas.copilot_action import ActionProposal


class CopilotMessageSchema(BaseModel):
    id: int
    session_id: int
    role: str
    content: str
    action: Optional[ActionProposal] = None
    suggestions: Optional[List[ActionProposal]] = None
    created_at: datetime

    class Config:
        from_attributes = True


class CopilotSessionSchema(BaseModel):
    id: int
    case_id: Optional[int] = None
    tenant_id: int
    user_id: int
    created_at: datetime
    updated_at: Optional[datetime] = None
    messages: List[CopilotMessageSchema] = []

    class Config:
        from_attributes = True


class CopilotChatRequest(BaseModel):
    case_id: Optional[int] = None
    message: str
    session_id: Optional[int] = None
