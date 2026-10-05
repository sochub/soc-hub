from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel


class ArtifactBase(BaseModel):
    artifact_type: str
    value: str
    description: Optional[str] = None


class ArtifactCreate(ArtifactBase):
    case_id: int
    isolated: bool = False


class ArtifactUpdate(BaseModel):
    artifact_type: Optional[str] = None
    value: Optional[str] = None
    description: Optional[str] = None


class Artifact(ArtifactBase):
    id: int
    tenant_id: int
    isolated: bool
    custom_type_id: Optional[int] = None
    created_at: datetime
    created_by: Optional[int] = None

    class Config:
        from_attributes = True


class ArtifactWithCases(Artifact):
    case_ids: List[int] = []
    case_count: int = 0
