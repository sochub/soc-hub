from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator

TEXT_MAX = 20000
TLP = Literal["white", "green", "amber", "red"]
MILESTONE_FIELDS = ("first_seen_at", "detected_at", "contained_at", "recovered_at")


class ReportIn(BaseModel):
    executive_summary: Optional[str] = Field(None, max_length=TEXT_MAX)
    impact: Optional[str] = Field(None, max_length=TEXT_MAX)
    lessons_learned: Optional[str] = Field(None, max_length=TEXT_MAX)
    tlp: TLP = "amber"
    first_seen_at: Optional[datetime] = None
    detected_at: Optional[datetime] = None
    contained_at: Optional[datetime] = None
    recovered_at: Optional[datetime] = None

    @field_validator(*MILESTONE_FIELDS)
    @classmethod
    def _aware(cls, v: Optional[datetime]) -> Optional[datetime]:
        if v is not None and (v.tzinfo is None or v.utcoffset() is None):
            raise ValueError("Milestone times must include a timezone")
        return v


class ReportDraft(BaseModel):
    executive_summary: str
    impact: str
    lessons_learned: str
