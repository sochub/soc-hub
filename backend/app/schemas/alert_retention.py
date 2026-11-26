from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.alert_retention import RETENTION_STATUSES
from app.schemas.suppression import Conditions


def _statuses(v):
    if v is None:
        return v
    bad = set(v) - set(RETENTION_STATUSES)
    if bad or not v:
        raise ValueError(f"statuses must be a non-empty subset of {', '.join(RETENTION_STATUSES)}")
    return sorted(set(v))


class RetentionScope(BaseModel):
    """What a policy deletes: alerts older than retain_days, in these statuses, matching every condition."""
    retain_days: int = Field(ge=1, le=3650)
    statuses: List[str] = Field(default_factory=lambda: ["dismissed"])
    conditions: Conditions = Field(default_factory=list, max_length=10)

    @field_validator("statuses")
    @classmethod
    def _check_statuses(cls, v):
        return _statuses(v)


class RetentionCreate(RetentionScope):
    name: str = Field(min_length=1, max_length=200)
    enabled: bool = False


class RetentionUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    retain_days: Optional[int] = Field(default=None, ge=1, le=3650)
    statuses: Optional[List[str]] = None
    conditions: Optional[Conditions] = Field(default=None, max_length=10)
    enabled: Optional[bool] = None

    @field_validator("statuses")
    @classmethod
    def _check_statuses(cls, v):
        return _statuses(v)


class RetentionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    retain_days: int
    statuses: list
    conditions: list
    enabled: bool
    last_run_at: Optional[datetime] = None
    last_deleted: int
    total_deleted: int
    created_at: Optional[datetime] = None
