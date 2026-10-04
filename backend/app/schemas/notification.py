from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class NotificationOut(BaseModel):
    id: int
    type: str
    summary: str
    case_id: int
    case_title: Optional[str] = None
    actor_name: Optional[str] = None
    timeline_event_id: Optional[int] = None
    read_at: Optional[datetime] = None
    created_at: datetime


class UnreadCount(BaseModel):
    count: int


class ReadAllOut(BaseModel):
    updated: int


class FollowState(BaseModel):
    following: bool


class MentionableUser(BaseModel):
    id: int
    name: Optional[str] = None
    email: str
