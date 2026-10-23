from typing import Any, Dict, List, Optional
from pydantic import BaseModel

# Action types the copilot may propose.
WRITE_ACTIONS = {"create_case", "add_artifact", "add_timeline_note", "update_case", "apply_playbook"}
READ_ACTIONS = {"find_related"}
ACTION_TYPES = WRITE_ACTIONS | READ_ACTIONS


class ActionProposal(BaseModel):
    """A structured action the copilot proposes; executed only after the user
    confirms (writes) or automatically for reads."""
    type: str
    summary: str = ""
    params: Dict[str, Any] = {}
    result: Optional[Dict[str, Any]] = None  # set once executed (ActionResult shape)


class ActionExecuteRequest(BaseModel):
    type: str
    params: Dict[str, Any] = {}
    case_id: Optional[int] = None
    # The copilot message that proposed it: the result is stored there, so the card survives a
    # reload and a second Confirm returns the stored result instead of running again.
    message_id: Optional[int] = None
    suggestion_index: Optional[int] = None  # None = the message's main action


class RelatedCase(BaseModel):
    case_id: int
    title: str
    shared_values: List[str]


class ActionResult(BaseModel):
    ok: bool
    message: str
    case_id: Optional[int] = None
    related: Optional[List[RelatedCase]] = None
