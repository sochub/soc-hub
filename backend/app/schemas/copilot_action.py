from typing import Any, Dict, List, Optional
from pydantic import BaseModel

# Action types the copilot may propose.
WRITE_ACTIONS = {"create_case", "add_artifact", "add_timeline_note", "update_case"}
READ_ACTIONS = {"find_related"}
ACTION_TYPES = WRITE_ACTIONS | READ_ACTIONS


class ActionProposal(BaseModel):
    """A structured action the copilot proposes; executed only after the user
    confirms (writes) or automatically for reads."""
    type: str
    summary: str = ""
    params: Dict[str, Any] = {}


class ActionExecuteRequest(BaseModel):
    type: str
    params: Dict[str, Any] = {}
    case_id: Optional[int] = None


class RelatedCase(BaseModel):
    case_id: int
    title: str
    shared_values: List[str]


class ActionResult(BaseModel):
    ok: bool
    message: str
    case_id: Optional[int] = None
    related: Optional[List[RelatedCase]] = None
