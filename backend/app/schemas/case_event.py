from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from typing import List, Literal, Optional
from pydantic import BaseModel, Field, field_validator
from app.models.case_event import EVENT_CATEGORIES


def _category(v: str) -> str:
    if v not in EVENT_CATEGORIES:
        raise ValueError(f"category must be one of {', '.join(EVENT_CATEGORIES)}")
    return v


class CaseEventCreate(BaseModel):
    event_date: datetime
    title: str = Field(min_length=1, max_length=500)
    content: Optional[str] = None
    raw: Optional[str] = None
    source: Optional[str] = Field(default=None, max_length=200)
    category: str = "unspecified"
    tags: List[str] = []
    flagged: bool = False
    in_summary: bool = True
    artifact_ids: List[int] = []

    _cat = field_validator("category")(_category)


class CaseEventUpdate(BaseModel):
    event_date: Optional[datetime] = None
    title: Optional[str] = Field(default=None, min_length=1, max_length=500)
    content: Optional[str] = None
    raw: Optional[str] = None
    source: Optional[str] = Field(default=None, max_length=200)
    category: Optional[str] = None
    tags: Optional[List[str]] = None
    flagged: Optional[bool] = None
    in_summary: Optional[bool] = None
    artifact_ids: Optional[List[int]] = None

    _cat = field_validator("category")(lambda v: v if v is None else _category(v))


class EventArtifactOut(BaseModel):
    id: int
    value: str
    type_key: str

    class Config:
        from_attributes = True


class CaseEventOut(BaseModel):
    id: int
    case_id: int
    user_id: Optional[int] = None
    event_date: datetime
    title: str
    content: Optional[str] = None
    raw: Optional[str] = None
    source: Optional[str] = None
    category: str
    tags: List[str] = []
    flagged: bool
    in_summary: bool
    artifacts: List[EventArtifactOut] = []
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class EventSuggestRequest(BaseModel):
    text: Optional[str] = Field(None, max_length=50000)  # pasted logs or notes
    use_ai: bool = True
    naive_tz: str = Field("UTC", max_length=64)  # IANA zone for pasted timestamps that carry no offset

    @field_validator("naive_tz")
    @classmethod
    def _tz(cls, v: str) -> str:
        try:
            ZoneInfo(v)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError("naive_tz must be an IANA time zone such as UTC or Europe/Madrid")
        return v


class SuggestedEvent(BaseModel):
    event_date: datetime
    title: str
    content: Optional[str] = None
    raw: Optional[str] = None
    source: Optional[str] = None
    category: str
    origin: Literal["ai", "alert", "log"]
    artifact_ids: List[int] = []


class EventSuggestResponse(BaseModel):
    events: List[SuggestedEvent]
    ai: Literal["used", "unavailable", "off"]
