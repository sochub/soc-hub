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
    # Milestones live on the case now (PUT /cases/{id}/milestones); extra keys sent by old clients are ignored.


class ReportDraft(BaseModel):
    executive_summary: str
    impact: str
    lessons_learned: str
