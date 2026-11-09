from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


class TimeEntryIn(BaseModel):
    started_at: datetime
    ended_at: datetime
    note: Optional[str] = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def _check(self):
        if self.started_at.tzinfo is None or self.ended_at.tzinfo is None:
            raise ValueError("timestamps must be timezone-aware")
        if self.ended_at <= self.started_at:
            raise ValueError("ended_at must be after started_at")
        return self


class TimeEntryPatch(BaseModel):
    started_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None
    note: Optional[str] = Field(default=None, max_length=500)


class TimeEntryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    user_id: int
    started_at: datetime
    ended_at: datetime
    source: str
    note: Optional[str] = None
    seconds: int


class TimeByUser(BaseModel):
    user_id: int
    email: str
    seconds: int


class TimeSummary(BaseModel):
    entries: List[TimeEntryOut]
    total_seconds: int
    by_user: List[TimeByUser]
