from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, Field, model_validator

# Action types the copilot may propose.
WRITE_ACTIONS = {"create_case", "add_artifact", "add_timeline_note", "update_case", "apply_playbook"}
READ_ACTIONS = {"find_related"}
ACTION_TYPES = WRITE_ACTIONS | READ_ACTIONS


# How long a confirmed write may hold its "running" claim. An action is a handful of DB writes (well under a
# second; a blocked row lock gives up long before this), and writes are not idempotent, so the window is two
# orders of magnitude above the worst real run: a claim this old belongs to a worker that died, never to a
# run still in progress, and releasing it cannot double-write.
CLAIM_TTL = timedelta(minutes=2)


def live_state(proposal: Dict[str, Any]) -> Optional[str]:
    """A stored proposal's state as it counts now: "running" lapses once its claim is older than CLAIM_TTL
    (a "running" without a timestamp predates timestamps and is treated the same way)."""
    state = proposal.get("state")
    if state != "running":
        return state
    try:
        claimed = datetime.fromisoformat(proposal.get("claimed_at") or "")
    except (TypeError, ValueError):
        return None
    if claimed.tzinfo is None:
        claimed = claimed.replace(tzinfo=timezone.utc)
    return state if datetime.now(timezone.utc) - claimed < CLAIM_TTL else None


class ActionProposal(BaseModel):
    """A structured action the copilot proposes; executed only after the user
    confirms (writes) or automatically for reads."""
    type: str
    summary: str = ""
    params: Dict[str, Any] = {}
    result: Optional[Dict[str, Any]] = None  # set once executed (ActionResult shape)
    # Target case, resolved when the proposal is made. None on an action that needs one = "needs a case".
    case_id: Optional[int] = None
    # "cancelled" / "dismissed" by the user, or "running" while a confirmed write executes: not confirmable.
    state: Optional[str] = None
    claimed_at: Optional[str] = None  # when "running" was claimed (ISO time); see live_state
    edited: bool = False  # the analyst changed values before Confirm: params are what was written
    # Filled when a message is read (never stored): the target as it is now.
    case_title: Optional[str] = None
    case_missing: bool = False

    @model_validator(mode="after")
    def _lapse_stale_claim(self):
        # What a reader sees is the state as it counts now: a dead worker's claim stops showing "Working…".
        self.state = live_state({"state": self.state, "claimed_at": self.claimed_at})
        return self


class ActionExecuteRequest(BaseModel):
    type: str
    params: Dict[str, Any] = {}
    case_id: Optional[int] = None
    # The copilot message that proposed it: the result is stored there, so the card survives a
    # reload and a second Confirm returns the stored result instead of running again.
    message_id: Optional[int] = None
    suggestion_index: Optional[int] = None  # None = the message's main action
    # Closing a case (update_case with status closed): collected by the card, validated by case_service.
    outcome: Optional[str] = None
    closing_note: Optional[str] = Field(None, max_length=8000)
    # Values the analyst changed on the card before Confirm. Only the action's editable keys are accepted
    # (anything else is a 422) and each is validated; applied over the stored params, never instead of them.
    edits: Optional[Dict[str, Any]] = None


class ActionDismissRequest(BaseModel):
    message_id: int
    suggestion_index: Optional[int] = None
    state: Literal["dismissed", "cancelled"] = "dismissed"


class RelatedCase(BaseModel):
    case_id: int
    title: str
    shared_values: List[str]


class ActionResult(BaseModel):
    ok: bool
    message: str
    case_id: Optional[int] = None
    related: Optional[List[RelatedCase]] = None
