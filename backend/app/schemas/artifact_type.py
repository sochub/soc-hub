import re
from typing import Optional

from pydantic import BaseModel, Field, field_validator

KEY_PATTERN = r"^[a-z][a-z0-9_]{0,31}$"
PAYLOAD_KEY_RE = re.compile(r"^[A-Za-z0-9_\-]+(\.[A-Za-z0-9_\-]+)*$")


def _clean_payload_key(v):
    """Blank -> None; otherwise must be a dotted path."""
    if v is None or not str(v).strip():
        return None
    v = str(v).strip()
    if not PAYLOAD_KEY_RE.match(v):
        raise ValueError("payload_key must be a dotted path like user_name or user.email")
    return v


class ArtifactTypeCreate(BaseModel):
    key: str = Field(pattern=KEY_PATTERN)
    label: str = Field(min_length=1, max_length=64)
    show_in_mindmap: bool = True
    private: bool = False
    payload_key: Optional[str] = Field(default=None, max_length=128)

    _payload = field_validator("payload_key", mode="before")(_clean_payload_key)


class ArtifactTypeUpdate(BaseModel):
    """key is intentionally absent: immutable after create (extra fields are ignored)."""
    label: Optional[str] = Field(default=None, min_length=1, max_length=64)
    show_in_mindmap: Optional[bool] = None
    private: Optional[bool] = None
    payload_key: Optional[str] = Field(default=None, max_length=128)

    _payload = field_validator("payload_key", mode="before")(_clean_payload_key)


class ArtifactTypeOut(BaseModel):
    id: int
    key: str
    label: str
    show_in_mindmap: bool
    private: bool
    payload_key: Optional[str] = None

    class Config:
        from_attributes = True
