"""Registry of workflow node types. Later phases add for_each and slack_* entries."""

TRIGGER_TYPES = ("case.created", "case.updated", "alert.ingested", "manual")

NODE_TYPES = {
    "trigger":             {"required": [], "raw": [], "alert_only": False},
    "condition":           {"required": ["expression"], "raw": ["expression"], "alert_only": False},
    "http_request":        {"required": ["method", "url"], "raw": [], "alert_only": False},
    "case_update":         {"required": [], "raw": [], "alert_only": False},
    "case_add_note":       {"required": ["content"], "raw": [], "alert_only": False},
    "case_add_artifact":   {"required": ["artifact_type", "value"], "raw": [], "alert_only": False},
    "case_apply_playbook": {"required": ["template_id"], "raw": [], "alert_only": False},
    "case_search":         {"required": [], "raw": [], "alert_only": False},
    "alert_promote":       {"required": ["mode"], "raw": [], "alert_only": True},
    "for_each":            {"required": ["items"], "raw": ["items"], "alert_only": False},
    "slack_ask_user":      {"required": ["email", "message"], "raw": [], "alert_only": False},
    "slack_post_message":  {"required": ["text"], "raw": [], "alert_only": False},
    "alert_dismiss":       {"required": [], "raw": [], "alert_only": True},
}

MAX_NODES = 50

# Config keys sent to Slack as mrkdwn: template values rendered into them are escaped (render_slack).
SLACK_MRKDWN_KEYS = {"slack_post_message": {"text"}, "slack_ask_user": {"message"}}
