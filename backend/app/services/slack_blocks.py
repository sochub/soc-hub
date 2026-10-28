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
ASSIGN_CASE = "sochub_assign_case"


def _val(x) -> str:
    return getattr(x, "value", x) or ""


def severity_rank(sev) -> int:
    s = _val(sev).lower()
    return SEVERITY_ORDER.index(s) if s in SEVERITY_ORDER else -1


def case_url(case_id: int) -> str:
    base = (settings.FRONTEND_URL or "").rstrip("/")
    return f"{base}/cases/{case_id}" if base else ""


def run_url(run_id: int) -> str:
    base = (settings.FRONTEND_URL or "").rstrip("/")
    return f"{base}/workflows/runs/{run_id}" if base else ""


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
        els += [_btn("Assign to me", t, c, "assign"), _btn("Assign…", t, c, "assignto"), _btn("Add note", t, c, "note"), _btn("Severity", t, c, "severity")]
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


def assign_modal(tenant_id: int, case, members: List[tuple]) -> dict:
    """members: (user_id, display name) of the tenant's analysts and admins; Slack caps a select at 100 options."""
    opts = [{"text": {"type": "plain_text", "text": slack_escape(name)[:75]}, "value": str(uid)} for uid, name in members[:100]]
    el = {"type": "static_select", "action_id": "v", "options": opts}
    current = next((o for o in opts if o["value"] == str(case.owner_id)), None)
    if current:
        el["initial_option"] = current
    return {
        "type": "modal", "callback_id": ASSIGN_CASE, "private_metadata": json.dumps({"t": tenant_id, "c": case.id}),
        "title": {"type": "plain_text", "text": f"Assign #{case.id:04d}"},
        "submit": {"type": "plain_text", "text": "Assign"},
        "blocks": [{"type": "input", "block_id": "owner", "label": {"type": "plain_text", "text": "Owner"}, "element": el}],
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

SOC_BOT_QUIPS = (
    ":satellite: _I sense a disturbance in the logs\u2026_",
    ":robot_face: _These IPs look suspicious. Not the droids you're looking for, though._",
    ":milky_way: _A long time ago, in a subnet far, far away\u2026_",
    ":crossed_swords: _Your containment skills are strong, padawan._",
    ":shield: _Shields up. Ready to add it to the case?_",
    ":dizzy: _The Force is strong with this indicator._",
    ":rocket: _Making the Kessel Run through your artifacts in under 12 parsecs._",
    ":new_moon: _Even the Death Star had an exposed port. Let's log this one._",
)

# artifact type -> (emoji, noun)
_ART_STYLE = {"ip": (":globe_with_meridians:", "IP"), "domain": (":link:", "domain"), "url": (":link:", "URL"),
              "file_hash": (":dna:", "file hash"), "email": (":email:", "email address"),
              "hostname": (":desktop_computer:", "host"), "username": (":bust_in_silhouette:", "user")}
# milestone key -> (emoji, phrase)
_MS_STYLE = {"first_seen": (":eyes:", "the first-seen time"), "detected": (":rotating_light:", "the detection time"),
             "contained": (":shield:", "the containment time"), "recovered": (":white_check_mark:", "the recovery time")}


def _at_label(iso: str) -> str:
    from datetime import datetime
    try:
        dt = datetime.fromisoformat(iso)
    except (TypeError, ValueError):
        return str(iso)
    return f"{dt.strftime('%a %d %b %H:%M')} {dt.tzname() or ''}".strip()


def _art_parts(p: dict):
    t = str(p.get("type", ""))
    emoji, noun = _ART_STYLE.get(t, (":mag:", slack_escape(t.title())))
    value = slack_escape(str(p.get("value", "")).replace("`", "\u02cb"))  # no backtick may close the code span
    return emoji, noun, value


def _ms_title(p: dict) -> str:
    return slack_escape(str(p.get("key", "")).replace("_", " ").title())


def _suggestion_text(s) -> str:
    p = s.payload or {}
    if s.kind == "milestone":
        emoji, phrase = _MS_STYLE.get(p.get("key"), (":mag:", "a milestone time"))
        return (f"{emoji} This sounds like {phrase}. Should I set the case's *{_ms_title(p)}* milestone "
                f"to {slack_escape(_at_label(p.get('at')))}?")
    emoji, noun, value = _art_parts(p)
    return f"{emoji} Should I add the {noun} `{value}` to the case artifacts? It isn't on the case yet."


def _resolved_line(s, names: dict) -> str:
    p = s.payload or {}
    who = names.get(s.resolved_by_user_id)
    who = slack_escape(who) if who else None
    if s.status == "accepted":
        if s.kind == "milestone":
            return f":white_check_mark: {who + ' set' if who else 'Set'} *{_ms_title(p)}* to {slack_escape(_at_label(p.get('at')))}."
        _, noun, value = _art_parts(p)
        return f":white_check_mark: {who + ' added' if who else 'Added'} the {noun} `{value}` to the case."
    if s.status == "conflict":
        return (f":warning: Not set: that time would put *{_ms_title(p)}* out of order with the other milestones. "
                "Adjust it on the case.")
    by = f" by {who}" if who else ""
    return f":no_entry_sign: Dismissed{by} \u2014 it won't be suggested again for this case."


def suggestions_reply(suggestions: list, tenant_id: int, names: Optional[dict] = None) -> List[dict]:
    """Thread reply listing SOC-bot suggestions; pending ones get Add/Set + Dismiss buttons.
    names maps resolved_by_user_id to a display name."""
    names = names or {}
    blocks: List[dict] = []
    if suggestions:
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text":
                       f":robot_face: *SOC-bot spotted something new for case #{suggestions[0].case_id}:*"}})
        if any(s.status == "pending" for s in suggestions):
            quip = SOC_BOT_QUIPS[min(s.id for s in suggestions) % len(SOC_BOT_QUIPS)]
            blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": quip}]})
    for s in suggestions:
        if s.status != "pending":
            blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": _resolved_line(s, names)}]})
            continue
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": _suggestion_text(s)}})
        blocks.append({"type": "actions", "block_id": f"wrs_{s.id}", "elements": [
            {"type": "button", "action_id": "wrs_accept", "style": "primary", "value": f"wrs:{tenant_id}:{s.id}:a",
             "text": {"type": "plain_text", "text": "Set milestone" if s.kind == "milestone" else "Add to case"}},
            {"type": "button", "action_id": "wrs_dismiss", "value": f"wrs:{tenant_id}:{s.id}:d",
             "text": {"type": "plain_text", "text": "Dismiss"}}]})
    return blocks


# --- war-room cards (colour-barred attachments) ---

RED, GREEN, BLUE, YELLOW = "#e01e5a", "#2eb67d", "#36c5f0", "#ecb22e"


def _pretty(v) -> str:
    return str(_val(v) or "none").replace("_", " ").capitalize()


def war_room_card(event_type: str, case, payload: dict, *, actor: Optional[str]) -> dict:
    """One colour-barred war-room card. Returns {"text": fallback, "attachments": [{"color", "blocks"}]}."""
    payload = payload or {}
    color, title, detail = YELLOW, event_type, None
    if event_type == "status.changed":
        title = f"Status: {_pretty(payload.get('from'))} → {_pretty(payload.get('to'))}"
    elif event_type == "severity.changed":
        title = f"Severity: {_pretty(payload.get('from'))} → {_pretty(payload.get('to'))}"
        if severity_rank(payload.get("to")) > severity_rank(payload.get("from")):
            color = RED
    elif event_type == "owner.changed":
        title = f"Owner: {payload.get('from') or 'unassigned'} → {payload.get('to') or 'unassigned'}"
    elif event_type == "sla.breached":
        color, title = RED, f"SLA breached: {payload.get('label') or 'SLA'}"
    elif event_type == "task.completed":
        color, title = BLUE, f"Task completed: {payload.get('title') or ''}".rstrip(": ")
        detail = f"{payload.get('done', 0)} of {payload.get('total', 0)} done"
        if payload.get("phase"):
            detail += f"  ·  {_pretty(payload.get('phase'))} phase"
    elif event_type == "workflow.finished":
        ok = payload.get("status") == "succeeded"
        color = BLUE if ok else RED
        title = f"Workflow {'succeeded' if ok else _pretty(payload.get('status')).lower()}: {payload.get('workflow_name') or ''}".rstrip(": ")
        lines = []
        for st in (payload.get("steps") or [])[:20]:
            http = f" (HTTP {st['http_status']})" if st.get("http_status") else ""
            lines.append(f"• {slack_escape(str(st.get('node_id')))}: {slack_escape(str(st.get('status')))}{http}")
        if len(payload.get("steps") or []) > 20:
            lines.append(f"…and {len(payload['steps']) - 20} more")
        if payload.get("error"):
            lines.append(f"Error: {slack_escape(str(payload['error'])[:300])}")
        if payload.get("run_id") and run_url(payload["run_id"]):
            lines.append(f"<{run_url(payload['run_id'])}|View run>")
        detail = "\n".join(lines) or None
    elif event_type == "containment.confirmed":
        color, title = GREEN, "Containment confirmed"
        if payload.get("after_workflow"):
            detail = f"after workflow *{slack_escape(str(payload['after_workflow']))}* succeeded"
    reason = payload.get("reason")
    url = case_url(case.id)
    ref = f"#{case.id:04d} {slack_escape((case.title or '')[:150])}"
    link = f"<{url}|{ref}>" if url else ref
    blocks: List[dict] = [{"type": "section", "text": {"type": "mrkdwn", "text": f"*{slack_escape(title)}*"}}]
    if detail:
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": detail}})
    if reason:
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": f"“{slack_escape(str(reason))}”"}})
    ctx = f"{link}" + (f"  ·  by {slack_escape(actor)}" if actor else "")
    blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": ctx}]})
    fallback = f"{title} — #{case.id:04d} {(case.title or '')[:150]}"
    return {"text": slack_escape(fallback), "attachments": [{"color": color, "blocks": blocks}]}
