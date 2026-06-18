from typing import Optional, List, Any
from datetime import datetime
from pydantic import BaseModel
from app.models.case import CaseStatus, CaseSeverity


class TimelineEventUser(BaseModel):
    id: int
    email: str
    full_name: Optional[str] = None

    class Config:
        from_attributes = True

# Alerts
class AlertBase(BaseModel):
    source: str
    external_id: str
    title: str
    payload: Any
    status: str = "pending"

class AlertCreate(AlertBase):
    pass

class AlertWebhookCreate(BaseModel):
    external_id: str
    title: str
    payload: Any = None

class Alert(AlertBase):
    id: int
    case_id: Optional[int] = None
    created_at: datetime

    class Config:
        from_attributes = True

# Timeline
class TimelineEventBase(BaseModel):
    event_type: str
    content: str

class TimelineEventCreate(TimelineEventBase):
    pass


class TimelineEventUpdate(BaseModel):
    event_type: Optional[str] = None
    content: Optional[str] = None

class TimelineEvent(TimelineEventBase):
    id: int
    case_id: int
    user_id: Optional[int] = None
    created_at: datetime
    user: Optional[TimelineEventUser] = None

    class Config:
        from_attributes = True

# Case Links
class CaseLinkBase(BaseModel):
    system: str
    external_id: str
    url: str

class CaseLinkCreate(CaseLinkBase):
    pass

class CaseLink(CaseLinkBase):
    id: int
    case_id: int

    class Config:
        from_attributes = True

# Artifacts (Forward reference or separate file, I'll put it here for simplicity or use artifact.py)
# Using separate file is better for avoiding circular imports if needed, but here simple is fine.
# I'll Reference Artifact Schema from artifact.py in the response model if needed, but for now skipping explicit artifact list in Case detail unless requested.

# Cases
class CaseBase(BaseModel):
    title: str
    description: Optional[str] = None
    status: CaseStatus = CaseStatus.NEW
    severity: CaseSeverity = CaseSeverity.MEDIUM
    owner_id: Optional[int] = None
    tags: List[str] = []
    source: str = "user-reported"

class CaseCreate(CaseBase):
    pass

class CaseUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    status: Optional[CaseStatus] = None
    severity: Optional[CaseSeverity] = None
    owner_id: Optional[int] = None
    tags: Optional[List[str]] = None
    source: Optional[str] = None

class Case(CaseBase):
    id: int
    created_at: datetime
    updated_at: Optional[datetime] = None
    resolved_at: Optional[datetime] = None
    timeline_events: List[TimelineEvent] = []

    class Config:
        from_attributes = True
