from typing import List, Optional
from datetime import datetime
from pydantic import BaseModel, Field, ValidationError, field_validator

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

    @field_validator("action", mode="before")
    @classmethod
    def _drop_malformed_action(cls, v):
        # A proposal stored before model output was validated may not have the proposal's shape (e.g. params
        # as a string). Drop it rather than fail the whole session read.
        try:
            return None if v is None else ActionProposal.model_validate(v)
        except ValidationError:
            return None


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
    message: str = Field(..., max_length=16_000)
    session_id: Optional[int] = None
