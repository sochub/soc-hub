from typing import Optional, List, Any, Literal
from datetime import datetime, timedelta, timezone
from pydantic import BaseModel, Field, field_validator, model_validator
from app.services.avatars import avatar_version
from app.models.case import CaseStatus, CaseSeverity, CASE_OUTCOMES


class TimelineEventUser(BaseModel):
    id: int
    email: str
    full_name: Optional[str] = None
    has_avatar: bool = False
    avatar_version: Optional[str] = None

    @model_validator(mode="before")
    @classmethod
    def _avatar(cls, v):
        key = getattr(v, "avatar_key", None)
        if isinstance(v, dict) or not hasattr(v, "avatar_key"):
            return v
        return {"id": v.id, "email": v.email, "full_name": v.full_name,
                "has_avatar": bool(key), "avatar_version": avatar_version(key)}

    class Config:
        from_attributes = True

# Alerts
ALERT_SEVERITIES = ("critical", "high", "medium", "low", "info")


def _check_alert_severity(v):
    if v is not None and v not in ALERT_SEVERITIES:
        raise ValueError(f"severity must be one of {', '.join(ALERT_SEVERITIES)}")
    return v


class AlertBase(BaseModel):
    source: str
    external_id: str
    title: str
    payload: Any
    status: str = "pending"
    severity: str = "medium"

class AlertCreate(AlertBase):
    pass

class AlertWebhookCreate(BaseModel):
    external_id: str
    title: str
    payload: Any = None
    severity: Optional[str] = None

    _sev = field_validator("severity")(_check_alert_severity)

class AlertUpdate(BaseModel):
    severity: Optional[str] = None
    owner_id: Optional[int] = None

    _sev = field_validator("severity")(_check_alert_severity)

class AlertDismiss(BaseModel):
    reason: Optional[str] = Field(None, max_length=1000)

class AlertBulk(BaseModel):
    ids: List[int] = Field(..., min_length=1, max_length=500)
    action: Literal["dismiss", "promote"]
    case_id: Optional[int] = None

class Alert(AlertBase):
    id: int
    owner_id: Optional[int] = None
    case_id: Optional[int] = None
    dismiss_reason: Optional[str] = None
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
    shared_with_guests: bool = False
    guest_id: Optional[int] = None
    guest_email: Optional[str] = None
    via: Optional[str] = None
    via_client: Optional[str] = None
    pinned: bool = False

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
def _tags_or_empty(v):
    """A NULL tags column (legacy rows, writers that pass None) must not 500 the whole case list."""
    return [] if v is None else v


class CaseBase(BaseModel):
    title: str
    description: Optional[str] = None
    status: CaseStatus = CaseStatus.NEW
    severity: CaseSeverity = CaseSeverity.MEDIUM
    owner_id: Optional[int] = None
    tags: List[str] = []
    source: str = "user-reported"

    _null_tags = field_validator("tags", mode="before")(_tags_or_empty)

def _require_tz(v: Optional[datetime]) -> Optional[datetime]:
    """Incident times must carry an offset: a naive time would be read in the server's zone."""
    if v is not None and (v.tzinfo is None or v.utcoffset() is None):
        raise ValueError("Milestone times must include a timezone")
    return v


class CaseCreate(CaseBase):
    # Analyst-declared incident start; omitted when the analyst left the form default.
    first_seen_at: Optional[datetime] = None

    @field_validator("first_seen_at")
    @classmethod
    def _not_future(cls, v):
        v = _require_tz(v)
        if v is not None and v > datetime.now(timezone.utc) + timedelta(seconds=60):
            raise ValueError("incident start cannot be in the future")
        return v


class PinIn(BaseModel):
    pinned: bool


class MilestonesIn(BaseModel):
    """Only keys present in the body change; null clears the declaration."""
    first_seen_at: Optional[datetime] = None
    detected_at: Optional[datetime] = None
    contained_at: Optional[datetime] = None
    recovered_at: Optional[datetime] = None

    _tz = field_validator("first_seen_at", "detected_at", "contained_at", "recovered_at")(_require_tz)

class CaseUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    status: Optional[CaseStatus] = None
    severity: Optional[CaseSeverity] = None
    owner_id: Optional[int] = None
    tags: Optional[List[str]] = None
    source: Optional[str] = None
    guest_summary: Optional[str] = Field(None, max_length=4000)
    outcome: Optional[str] = None
    closing_note: Optional[str] = Field(None, max_length=8000)

    _null_tags = field_validator("tags", mode="before")(_tags_or_empty)

    @field_validator("outcome")
    @classmethod
    def _outcome(cls, v):
        if v is not None and v not in CASE_OUTCOMES:
            raise ValueError(f"outcome must be one of {', '.join(CASE_OUTCOMES)}")
        return v

class Case(CaseBase):
    id: int
    created_at: datetime
    updated_at: Optional[datetime] = None
    resolved_at: Optional[datetime] = None
    acknowledged_at: Optional[datetime] = None
    timeline_events: List[TimelineEvent] = []
    guest_summary: Optional[str] = None
    outcome: Optional[str] = None
    closing_note: Optional[str] = None

    # Computed, not stored — attached to the ORM object by the endpoint via
    # app.utils.sla.compute_sla_state before serialization.
    sla_response_target_minutes: Optional[int] = None
    sla_response_status: Optional[str] = None
    sla_response_due_at: Optional[datetime] = None
    sla_resolution_target_minutes: Optional[int] = None
    sla_resolution_status: Optional[str] = None
    sla_resolution_due_at: Optional[datetime] = None
    sla_overall_status: Optional[str] = None
    # Detail endpoint only (app.services.milestones.case_milestones).
    milestones: Optional[dict] = None

    class Config:
        from_attributes = True
