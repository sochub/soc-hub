from typing import List, Optional
from pydantic import BaseModel


class SLAPolicyItem(BaseModel):
    severity: str
    response_target_minutes: Optional[int] = None
    resolution_target_minutes: Optional[int] = None


class SLAPolicyUpdate(BaseModel):
    policies: List[SLAPolicyItem]
