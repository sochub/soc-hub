import re
from typing import List, Optional

from pydantic import BaseModel, Field, field_validator

SLACK_CHANNEL_RE = re.compile(r"^[#]?[a-z0-9_-]{1,80}$")


class GroupMemberOut(BaseModel):
    user_id: int
    email: str
    full_name: Optional[str] = None


class GroupIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: Optional[str] = Field(default=None, max_length=500)
    member_user_ids: List[int] = []

    @field_validator("name")
    @classmethod
    def _strip(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("name required")
        return v


class GroupOut(BaseModel):
    id: int
    name: str
    description: Optional[str] = None
    members: List[GroupMemberOut] = []
    tier_ids: List[int] = []


class TierIn(BaseModel):
    id: Optional[int] = None
    name: str = Field(min_length=1, max_length=100)
    accept_timeout_minutes: Optional[int] = Field(default=None, ge=1, le=10080)
    notify_email: bool = False
    notify_slack: bool = False
    slack_channel: Optional[str] = None
    group_ids: List[int] = Field(min_length=1)

    @field_validator("slack_channel")
    @classmethod
    def _chan(cls, v: Optional[str]) -> Optional[str]:
        if v is None or v == "":
            return None
        if not SLACK_CHANNEL_RE.match(v):
            raise ValueError("invalid slack channel")
        return v


class PolicyIn(BaseModel):
    tiers: List[TierIn]


class TierOut(BaseModel):
    id: int
    position: int
    name: str
    accept_timeout_minutes: Optional[int] = None
    notify_email: bool
    notify_slack: bool
    slack_channel: Optional[str] = None
    group_ids: List[int] = []


class PolicyOut(BaseModel):
    tiers: List[TierOut]
