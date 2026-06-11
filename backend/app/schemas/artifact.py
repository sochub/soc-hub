from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel
from app.models.artifact import ArtifactType


class ArtifactBase(BaseModel):
    artifact_type: ArtifactType
    value: str
    description: Optional[str] = None


class ArtifactCreate(ArtifactBase):
    case_id: int
    isolated: bool = False


class ArtifactUpdate(BaseModel):
    artifact_type: Optional[ArtifactType] = None
    value: Optional[str] = None
    description: Optional[str] = None


class Artifact(ArtifactBase):
    id: int
    tenant_id: int
    isolated: bool
    created_at: datetime
    created_by: Optional[int] = None

    class Config:
        from_attributes = True


class ArtifactWithCases(Artifact):
    case_ids: List[int] = []
    case_count: int = 0
