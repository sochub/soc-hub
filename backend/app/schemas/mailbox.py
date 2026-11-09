from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class MailboxIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    host: str = Field(min_length=1, max_length=255)
    port: int = Field(default=993, ge=1, le=65535)
    use_ssl: bool = True
    username: str = Field(min_length=1, max_length=255)
    password: Optional[str] = None  # write-only; required on create, omitted on update = keep existing
    folder: str = Field(default="INBOX", min_length=1, max_length=100)
    poll_interval_seconds: int = Field(default=120, ge=60)
    enabled: bool = True
    fallback_create_alert: bool = False


class MailboxUpdate(BaseModel):
    """Partial update: omitted fields keep their stored value."""
    name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    host: Optional[str] = Field(default=None, min_length=1, max_length=255)
    port: Optional[int] = Field(default=None, ge=1, le=65535)
    use_ssl: Optional[bool] = None
    username: Optional[str] = Field(default=None, min_length=1, max_length=255)
    password: Optional[str] = None
    folder: Optional[str] = Field(default=None, min_length=1, max_length=100)
    poll_interval_seconds: Optional[int] = Field(default=None, ge=60)
    enabled: Optional[bool] = None
    fallback_create_alert: Optional[bool] = None


class MailboxOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    host: str
    port: int
    use_ssl: bool
    username: str
    folder: str
    poll_interval_seconds: int
    enabled: bool
    fallback_create_alert: bool
    last_polled_at: Optional[datetime] = None
    last_error: Optional[str] = None
    created_at: Optional[datetime] = None
    password_set: bool = True


class MailboxTestResult(BaseModel):
    ok: bool
    message_count: Optional[int] = None
    error: Optional[str] = None
