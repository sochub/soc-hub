from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, Field, field_validator
from app.models.case_asset import ASSET_TYPES, COMPROMISE_STATUSES


def _one_of(name, allowed):
    def check(v):
        if v is not None and v not in allowed:
            raise ValueError(f"{name} must be one of {', '.join(allowed)}")
        return v
    return check


class CaseAssetCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    asset_type: str = "host"
    ip: Optional[str] = None
    domain: Optional[str] = None
    description: Optional[str] = None
    compromise_status: str = "unknown"
    tags: List[str] = []

    _t = field_validator("asset_type")(_one_of("asset_type", ASSET_TYPES))
    _s = field_validator("compromise_status")(_one_of("compromise_status", COMPROMISE_STATUSES))


class CaseAssetUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    asset_type: Optional[str] = None
    ip: Optional[str] = None
    domain: Optional[str] = None
    description: Optional[str] = None
    compromise_status: Optional[str] = None
    tags: Optional[List[str]] = None

    _t = field_validator("asset_type")(_one_of("asset_type", ASSET_TYPES))
    _s = field_validator("compromise_status")(_one_of("compromise_status", COMPROMISE_STATUSES))


class CaseAssetOut(BaseModel):
    id: int
    case_id: int
    name: str
    asset_type: str
    ip: Optional[str] = None
    domain: Optional[str] = None
    description: Optional[str] = None
    compromise_status: str
    tags: List[str] = []
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True
