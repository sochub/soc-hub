"""Block Kit builders for the sochub Slack bot: case card, modals and the App Home tab.

Every user-controlled string goes through slack_escape (no mentions, links or formatting injection).
"""
import json
from typing import Iterable, List, Optional

from app.core.config import settings
from app.models.case import CaseSeverity, CaseStatus
from app.workflows.templating import slack_escape

SEVERITY_ORDER = ["info", "low", "medium", "high", "critical"]
_SEV_ICON = {"critical": ":red_circle:", "high": ":large_orange_circle:", "medium": ":large_yellow_circle:",
             "low": ":large_blue_circle:", "info": ":white_circle:"}
_DONE = {CaseStatus.RESOLVED, CaseStatus.CLOSED}

CREATE_CASE = "sochub_create_case"
ADD_NOTE = "sochub_add_note"
SET_SEVERITY = "sochub_set_severity"


def _val(x) -> str:
    return getattr(x, "value", x) or ""


def severity_rank(sev) -> int:
    s = _val(sev).lower()
    return SEVERITY_ORDER.index(s) if s in SEVERITY_ORDER else -1


def case_url(case_id: int) -> str:
    base = (settings.FRONTEND_URL or "").rstrip("/")
    return f"{base}/cases/{case_id}" if base else ""


def _btn(text: str, tenant_id: int, case_id: int, action: str, style: Optional[str] = None) -> dict:
    b = {"type": "button", "text": {"type": "plain_text", "text": text}, "action_id": f"case_{action}",
         "value": f"case:{tenant_id}:{case_id}:{action}"}
    if style:
        b["style"] = style
    return b


def case_summary_line(case, owner: Optional[str]) -> str:
    sev = _val(case.severity).lower()
    status = _val(case.status).replace("_", " ")
    return f"{_SEV_ICON.get(sev, '')} *{sev.upper()}*  ·  {status}  ·  owner: {slack_escape(owner) if owner else '_unassigned_'}"


def case_card(case, owner: Optional[str] = None, *, note: Optional[str] = None, actions: bool = True) -> List[dict]:
    """The one case card used for channel alerts, DMs, /sochub case and the war room."""
    url = case_url(case.id)
    title = slack_escape((case.title or "")[:150])
    head = f"*<{url}|#{case.id:04d} {title}>*" if url else f"*#{case.id:04d} {title}*"
    blocks: List[dict] = []
    if note:
        blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": slack_escape(note)}]})
    blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": f"{head}\n{case_summary_line(case, owner)}"}})
    if case.description:
        blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": slack_escape(case.description[:280])}]})
    if not actions:
        return blocks
    t, c = case.tenant_id, case.id
    els = []
    if case.status not in _DONE:
        if case.status in (CaseStatus.NEW, CaseStatus.OPEN):
            els.append(_btn("Acknowledge", t, c, "ack", "primary"))
        els += [_btn("Assign to me", t, c, "assign"), _btn("Add note", t, c, "note"), _btn("Severity", t, c, "severity")]
        if not case.slack_channel_id:
            els.append(_btn("War room", t, c, "warroom"))
        els.append(_btn("Close", t, c, "close", "danger"))
    if url:
        els.append({"type": "button", "text": {"type": "plain_text", "text": "Open in sochub"}, "url": url,
                    "action_id": "case_open"})
    if els:
        blocks.append({"type": "actions", "elements": els[:25]})
    return blocks


def card_text(case) -> str:
    """Notification fallback text (push notifications, screen readers)."""
    # Slack parses `text` too: escape it like the blocks, or a title like "<!channel>" pings everyone.
    return f"#{case.id:04d} [{_val(case.severity).upper()}] {slack_escape((case.title or '')[:150])}"


def _severity_options() -> List[dict]:
    return [{"text": {"type": "plain_text", "text": s.value.capitalize()}, "value": s.value} for s in
            (CaseSeverity.CRITICAL, CaseSeverity.HIGH, CaseSeverity.MEDIUM, CaseSeverity.LOW, CaseSeverity.INFO)]


def _option(sev: str) -> dict:
    return {"text": {"type": "plain_text", "text": sev.capitalize()}, "value": sev}


def create_case_modal(tenant_id: int, *, title: str = "", description: str = "", source: Optional[str] = None) -> dict:
    return {
        "type": "modal", "callback_id": CREATE_CASE,
        "private_metadata": json.dumps({"t": tenant_id, "src": source}),
        "title": {"type": "plain_text", "text": "New sochub case"},
        "submit": {"type": "plain_text", "text": "Create case"},
        "close": {"type": "plain_text", "text": "Cancel"},
        "blocks": [
            {"type": "input", "block_id": "title", "label": {"type": "plain_text", "text": "Title"},
             "element": {"type": "plain_text_input", "action_id": "v", "max_length": 200, "initial_value": title[:200]}},
            {"type": "input", "block_id": "severity", "label": {"type": "plain_text", "text": "Severity"},
             "element": {"type": "static_select", "action_id": "v", "options": _severity_options(),
                         "initial_option": _option("medium")}},
            {"type": "input", "block_id": "description", "optional": True,
             "label": {"type": "plain_text", "text": "What happened?"},
             "element": {"type": "plain_text_input", "action_id": "v", "multiline": True, "max_length": 3000,
                         "initial_value": description[:3000]}},
            {"type": "input", "block_id": "assign", "optional": True,
             "label": {"type": "plain_text", "text": "Assignment"},
             "element": {"type": "checkboxes", "action_id": "v", "initial_options": [
                 {"text": {"type": "plain_text", "text": "Assign to me"}, "value": "me"}],
                 "options": [{"text": {"type": "plain_text", "text": "Assign to me"}, "value": "me"}]}},
        ],
    }


def note_modal(tenant_id: int, case) -> dict:
    return {
        "type": "modal", "callback_id": ADD_NOTE, "private_metadata": json.dumps({"t": tenant_id, "c": case.id}),
        "title": {"type": "plain_text", "text": f"Note on #{case.id:04d}"},
        "submit": {"type": "plain_text", "text": "Add note"},
        "blocks": [{"type": "input", "block_id": "note", "label": {"type": "plain_text", "text": "Note (Markdown)"},
                    "element": {"type": "plain_text_input", "action_id": "v", "multiline": True, "max_length": 3000}}],
    }


def severity_modal(tenant_id: int, case) -> dict:
    return {
        "type": "modal", "callback_id": SET_SEVERITY, "private_metadata": json.dumps({"t": tenant_id, "c": case.id}),
        "title": {"type": "plain_text", "text": f"Severity of #{case.id:04d}"},
        "submit": {"type": "plain_text", "text": "Save"},
        "blocks": [{"type": "input", "block_id": "severity", "label": {"type": "plain_text", "text": "Severity"},
                    "element": {"type": "static_select", "action_id": "v", "options": _severity_options(),
                                "initial_option": _option(_val(case.severity).lower() or "medium")}}],
    }


def home_view(cases: Iterable, owner_names: dict, *, linked: bool) -> dict:
    blocks: List[dict] = [
        {"type": "header", "text": {"type": "plain_text", "text": "sochub · My queue"}},
        {"type": "actions", "elements": [{"type": "button", "text": {"type": "plain_text", "text": "New case"},
                                          "style": "primary", "action_id": "home_new_case", "value": "home:new"}]},
    ]
    if not linked:
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text":
                       "Your Slack account isn't linked to a sochub analyst in this workspace's tenant. "
                       "Ask an admin to invite the email address on your Slack profile."}})
        return {"type": "home", "blocks": blocks}
    cases = list(cases)
    if not cases:
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": ":sparkles: Nothing assigned to you. Enjoy it."}})
    for case in cases:
        blocks.append({"type": "divider"})
        blocks += case_card(case, owner_names.get(case.owner_id))
    blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text":
                   "Tip: `/sochub new` creates a case from anywhere · `/sochub case 42` shows a case"}]})
    return {"type": "home", "blocks": blocks[:100]}


HELP = ("*sochub commands*\n"
        "• `/sochub new [title]`: open the new-case form\n"
        "• `/sochub case <id>`: show a case with actions\n"
        "• `/sochub mine`: your open cases\n"
        "• Message menu (⋯) → *Create sochub case*: turn any message into a case\n"
        "• The *Home* tab of this app lists your queue")


# ---- SOC-bot war-room suggestions ---------------------------------------------

_ART_LABEL = {"ip": "IP", "hostname": "Host", "username": "User", "domain": "Domain", "url": "URL",
              "file_hash": "Hash", "email": "Email"}


def _at_label(iso: str) -> str:
    from datetime import datetime
    try:
        dt = datetime.fromisoformat(iso)
    except (TypeError, ValueError):
        return str(iso)
    return f"{dt.strftime('%a %d %b %H:%M')} {dt.tzname() or ''}".strip()


def _suggestion_text(s) -> str:
    p = s.payload or {}
    if s.kind == "milestone":
        return f"Set *{slack_escape(str(p.get('key', '')).replace('_', ' ').title())}* to {slack_escape(_at_label(p.get('at')))}?"
    label = _ART_LABEL.get(p.get("type"), slack_escape(str(p.get("type", "")).title()))
    value = slack_escape(str(p.get('value', '')).replace('`', '\u02cb'))  # no backtick may close the code span
    return f"{label} `{value}`"


def _resolved_line(s, names: dict) -> str:
    if s.status == "accepted":
        who = names.get(s.resolved_by_user_id)
        verb = "set" if s.kind == "milestone" else "added"
        return f"✓ {verb} by {slack_escape(who)}" if who else f"✓ {verb}"
    if s.status == "conflict":
        return "⚠ conflicts with Detected"
    return "dismissed"


def suggestions_reply(suggestions: list, tenant_id: int, names: Optional[dict] = None) -> List[dict]:
    """Thread reply listing SOC-bot suggestions; pending ones get Add/Set + Dismiss buttons.
    names maps resolved_by_user_id to a display name."""
    names = names or {}
    blocks: List[dict] = []
    for s in suggestions:
        text = _suggestion_text(s)
        if s.status != "pending":
            blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": f"{text}  ·  {_resolved_line(s, names)}"}]})
            continue
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": text}})
        blocks.append({"type": "actions", "block_id": f"wrs_{s.id}", "elements": [
            {"type": "button", "action_id": "wrs_accept", "style": "primary", "value": f"wrs:{tenant_id}:{s.id}:a",
             "text": {"type": "plain_text", "text": "Set" if s.kind == "milestone" else "Add"}},
            {"type": "button", "action_id": "wrs_dismiss", "value": f"wrs:{tenant_id}:{s.id}:d",
             "text": {"type": "plain_text", "text": "Dismiss"}}]})
    return blocks
