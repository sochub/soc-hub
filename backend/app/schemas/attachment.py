from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict


class AttachmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    case_id: int
    filename: str
    content_type: Optional[str] = None
    size_bytes: int
    sha256: str
    is_malicious: bool
    description: Optional[str] = None
    uploaded_by: Optional[int] = None
    uploaded_by_email: Optional[str] = None
    created_at: datetime
    deleted_at: Optional[datetime] = None
    deleted_by: Optional[int] = None
    deleted_by_email: Optional[str] = None


class AttachmentList(BaseModel):
    max_upload_mb: int
    items: List[AttachmentOut]
