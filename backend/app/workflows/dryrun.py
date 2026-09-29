"""Pure dry-run decisions: which nodes really execute and what simulated ones return."""

_READ_ONLY = {"trigger", "condition", "for_each", "case_search"}

_STUBS = {
    "http_request": {"status": 200, "headers": {}, "body": {}},
    "alert_promote": {"case_id": None, "created": True},
}


def dry_run_behaviour(node: dict) -> str:
    if node["type"] in _READ_ONLY:
        return "execute"
    if node["type"] == "http_request" and node.get("execute_in_dry_run"):
        return "execute"
    return "simulate"


def simulated_output(node: dict, rendered_input: dict, dry_run_mocks: dict) -> dict:
    if node["type"] == "slack_ask_user":
        from app.services.slack_service import parse_button_labels
        try:
            labels = parse_button_labels(rendered_input.get("buttons"))
        except ValueError:
            labels = []  # bad config: dry run stays usable, falls back to Yes/No
        answer = ((dry_run_mocks or {}).get("ask_user_answers") or {}).get(node["id"])
        if answer == "timeout":
            out = {"response": None, "timed_out": True}
        else:
            out = {"response": answer or (labels or ["Yes", "No"])[0], "timed_out": False}
        return {**out, "responder_slack_id": None, "responder_email": None, "simulated": True, "would_do": rendered_input}
    mocks = (dry_run_mocks or {}).get("mocks") or {}
    if node["id"] in mocks:
        base = mocks[node["id"]]
    elif node.get("mock_output") is not None:
        base = node["mock_output"]
    else:
        base = _STUBS.get(node["type"], {})
    return {**base, "simulated": True, "would_do": rendered_input}
