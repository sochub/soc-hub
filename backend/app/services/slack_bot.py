"""sochub Slack bot: /sochub command, shortcuts, modals, war rooms, responder alerts and App Home.

Request-path handlers (commands, shortcuts, view submissions, modal-opening buttons) run inline because Slack
needs the answer, or a trigger_id use, within 3 s. Everything else runs in Celery (app/tasks/slack.py).
Who may act is decided by the same rule as the existing case buttons: the Slack user's email must belong to an
active analyst/admin of the tenant the workspace is connected to.
"""
import json
import logging
import re
from datetime import datetime, timezone
from typing import List, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select

from app.db.session import AsyncSessionLocal
from app.models.case import Case, CaseSeverity, CaseStatus, TimelineEvent
from app.models.notification import CaseFollower
from app.models.slack_integration import SlackIntegration
from app.models.user import User
from app.services import slack_blocks as ui
from app.services import war_room_naming as wr_naming
from app.services.case_service import add_timeline_note, apply_case_update, create_case_record
from app.services.slack_actions import _platform_user, _slack_email
from app.services.slack_service import SlackError, load_integration, slack_call
from app.utils.crypto import decrypt
from app.workflows.templating import slack_escape

logger = logging.getLogger(__name__)
UNLINKED = "Your Slack account isn't linked to a sochub analyst in this tenant. Ask an admin to invite the email on your Slack profile."
_CASE_ID = re.compile(r"#?(\d{1,10})")
_OPEN = (CaseStatus.NEW, CaseStatus.OPEN, CaseStatus.IN_PROGRESS, CaseStatus.PENDING)


def _token(integ: SlackIntegration) -> str:
    return decrypt(integ.bot_token_enc)


async def linked_user(db, tenant_id: int, token: str, slack_user_id: str) -> Optional[User]:
    return await _platform_user(db, tenant_id, await _slack_email(token, slack_user_id))


async def _names(db, user_ids) -> dict:
    ids = {i for i in user_ids if i}
    if not ids:
        return {}
    rows = (await db.execute(select(User.id, User.full_name, User.email).where(User.id.in_(ids)))).all()
    return {i: (n or e) for i, n, e in rows}


async def _case(db, tenant_id: int, case_id: int) -> Optional[Case]:
    return (await db.execute(select(Case).where(Case.id == case_id, Case.tenant_id == tenant_id))).scalars().first()


def _ephemeral(text: str, blocks: Optional[List[dict]] = None) -> dict:
    out = {"response_type": "ephemeral", "text": text}
    if blocks:
        out["blocks"] = blocks
    return out


# --- /sochub slash command (inline) ---

async def handle_command(db, integ: SlackIntegration, form: dict) -> dict:
    token, tid = _token(integ), integ.tenant_id
    text = (form.get("text") or "").strip()
    verb, _, rest = text.partition(" ")
    verb = verb.lower()
    if verb in ("", "help"):
        return _ephemeral(ui.HELP)
    user = await linked_user(db, tid, token, form.get("user_id", ""))
    if not user:
        return _ephemeral(UNLINKED)
    if verb == "new":
        await slack_call(token, "views.open", trigger_id=form.get("trigger_id"),
                         view=ui.create_case_modal(tid, title=rest.strip()))
        return {}
    if verb == "case":
        m = _CASE_ID.fullmatch(rest.strip())
        case = await _case(db, tid, int(m.group(1))) if m else None
        if not case:
            return _ephemeral("Usage: `/sochub case <id>` (case not found in this tenant).")
        names = await _names(db, [case.owner_id])
        return _ephemeral(ui.card_text(case), ui.case_card(case, names.get(case.owner_id)))
    if verb == "mine":
        cases = (await db.execute(select(Case).where(Case.tenant_id == tid, Case.owner_id == user.id,
                                                     Case.status.in_(_OPEN)).order_by(Case.id.desc()).limit(10))).scalars().all()
        if not cases:
            return _ephemeral("No open cases assigned to you. :sparkles:")
        blocks: List[dict] = []
        for c in cases:
            blocks += ui.case_card(c, user.full_name or user.email) + [{"type": "divider"}]
        return _ephemeral(f"{len(cases)} open case(s)", blocks[:50])
    return _ephemeral(f"Unknown command `{slack_escape(verb[:20])}`.\n{ui.HELP}")


# --- shortcuts and modal-opening buttons (inline: they need the trigger_id within 3 s) ---

async def open_modal_for(db, integ: SlackIntegration, payload: dict) -> bool:
    """Open a modal for shortcuts and for the note/severity/new-case buttons. True when handled here."""
    token, tid = _token(integ), integ.tenant_id
    trigger = payload.get("trigger_id")
    kind = payload.get("type")
    if kind in ("shortcut", "message_action"):
        if not await linked_user(db, tid, token, payload["user"]["id"]):
            await _dm(token, payload["user"]["id"], UNLINKED)
            return True
        title, desc, src = "", "", None
        if kind == "message_action":
            msg = payload.get("message") or {}
            text = (msg.get("text") or "").strip()
            title = text.splitlines()[0][:120] if text else ""
            try:
                src = (await slack_call(token, "chat.getPermalink", channel=(payload.get("channel") or {}).get("id"),
                                        message_ts=msg.get("ts")))["permalink"]
            except SlackError:
                src = None
            desc = text
        await slack_call(token, "views.open", trigger_id=trigger,
                         view=ui.create_case_modal(tid, title=title, description=desc, source=src))
        return True
    if kind != "block_actions":
        return False
    action = (payload.get("actions") or [{}])[0]
    if action.get("action_id") == "home_new_case":
        if await linked_user(db, tid, token, payload["user"]["id"]):
            await slack_call(token, "views.open", trigger_id=trigger, view=ui.create_case_modal(tid))
        return True
    value = action.get("value") or ""
    parts = value.split(":")
    if len(parts) == 4 and parts[0] == "case" and parts[3] in ("note", "severity"):
        if parts[1] != str(tid) or not await linked_user(db, tid, token, payload["user"]["id"]):
            return True
        case = await _case(db, tid, int(parts[2]))
        if case:
            view = ui.note_modal(tid, case) if parts[3] == "note" else ui.severity_modal(tid, case)
            await slack_call(token, "views.open", trigger_id=trigger, view=view)
        return True
    return False


# --- view submissions (inline: the response decides whether the modal closes) ---

def _input(payload: dict, block: str) -> dict:
    return ((payload.get("view") or {}).get("state", {}).get("values", {}).get(block) or {}).get("v") or {}


async def handle_submission(db, integ: SlackIntegration, payload: dict) -> Optional[dict]:
    """Returns the Slack response body for a view_submission (None = close the modal)."""
    from app.tasks.triage import run_case_triage_task
    from app.workflows.events import emit_event

    token, tid = _token(integ), integ.tenant_id
    view = payload.get("view") or {}
    try:
        meta = json.loads(view.get("private_metadata") or "{}")
    except ValueError:
        meta = {}
    if meta.get("t") != tid:
        return {"response_action": "errors", "errors": {}}  # modal opened for another tenant: refuse
    user = await linked_user(db, tid, token, payload["user"]["id"])
    if not user:
        return {"response_action": "errors", "errors": {"title" if view.get("callback_id") == ui.CREATE_CASE else
                                                        ("note" if view.get("callback_id") == ui.ADD_NOTE else "severity"): UNLINKED}}
    cb = view.get("callback_id")

    if cb == ui.CREATE_CASE:
        title = (_input(payload, "title").get("value") or "").strip()
        if not title:
            return {"response_action": "errors", "errors": {"title": "A title is required."}}
        sev = (_input(payload, "severity").get("selected_option") or {}).get("value", "medium")
        desc = (_input(payload, "description").get("value") or "").strip()
        if meta.get("src"):
            desc = f"{desc}\n\nReported from Slack: {meta['src']}".strip()
        mine = any(o.get("value") == "me" for o in _input(payload, "assign").get("selected_options") or [])
        data = {"title": title[:200], "description": desc or "Reported from Slack.",
                "severity": CaseSeverity(sev) if sev in CaseSeverity._value2member_map_ else CaseSeverity.MEDIUM,
                "source": "slack"}
        if not mine:
            data["owner_id"] = None
        case, triage_id = await create_case_record(db, tenant_id=tid, data=data, user_id=user.id)
        if not mine:
            case.owner_id = None  # create_case_record defaults the owner to the creator
        await db.commit()
        run_case_triage_task.delay(triage_id)
        emit_event(tid, "case.created", case_id=case.id)
        url = ui.case_url(case.id)
        link = f"<{url}|#{case.id:04d}>" if url else f"#{case.id:04d}"
        return {"response_action": "update", "view": {
            "type": "modal", "title": {"type": "plain_text", "text": "Case created"},
            "close": {"type": "plain_text", "text": "Done"},
            "blocks": [{"type": "section", "text": {"type": "mrkdwn", "text": f":white_check_mark: Created case {link}."}}]}}

    case = await _case(db, tid, int(meta.get("c") or 0))
    if not case:
        return {"response_action": "errors", "errors": {"note" if cb == ui.ADD_NOTE else "severity": "Case not found."}}
    if cb == ui.ADD_NOTE:
        note = (_input(payload, "note").get("value") or "").strip()
        if not note:
            return {"response_action": "errors", "errors": {"note": "Write something first."}}
        await add_timeline_note(db, case=case, content=note, user_id=user.id)
        await db.commit()
        return None
    if cb == ui.SET_SEVERITY:
        sev = (_input(payload, "severity").get("selected_option") or {}).get("value")
        if sev not in CaseSeverity._value2member_map_:
            return {"response_action": "errors", "errors": {"severity": "Pick a severity."}}
        changes = await apply_case_update(db, case=case, update_data={"severity": CaseSeverity(sev)}, user_id=user.id)
        await db.commit()
        if changes:
            emit_event(tid, "case.updated", case_id=case.id, changes=changes)
        return None
    return None


async def _dm(token: str, slack_user_id: str, text: str, blocks: Optional[List[dict]] = None) -> None:
    try:
        channel = (await slack_call(token, "conversations.open", users=slack_user_id))["channel"]["id"]
        await slack_call(token, "chat.postMessage", channel=channel, text=text, blocks=blocks)
    except SlackError:
        logger.warning("Slack DM failed", exc_info=True)


async def _slack_id(token: str, email: Optional[str]) -> Optional[str]:
    if not email:
        return None
    try:
        return (await slack_call(token, "users.lookupByEmail", email=email))["user"]["id"]
    except SlackError:
        return None


# --- war room (Celery) ---

async def open_war_room(db, tenant_id: int, case: Case, user: User, clicker_slack_id: Optional[str] = None, *,
                        name: Optional[str] = None, listen: bool = False) -> str:
    """Create (or reuse) the case's private channel, invite responders, post the card. Returns the channel id."""
    integ, token = await load_integration(db, tenant_id)
    if case.slack_channel_id:
        return case.slack_channel_id
    now = datetime.now(timezone.utc)
    try:
        created = now.astimezone(ZoneInfo(user.timezone)) if user.timezone else now
    except (ZoneInfoNotFoundError, ValueError):
        created = now
    if name is not None and name.strip():
        name = wr_naming.sanitise(name)[: wr_naming.MAX_LEN].strip("-")
        if not name:
            raise SlackError("The war room name has no usable characters; use letters, numbers, hyphens or underscores")
    else:
        name = wr_naming.render_channel_name(integ.war_room_name_template, case_id=case.id, title=case.title, created=created)
    base, attempt = name, 1
    while True:
        try:
            channel = (await slack_call(token, "conversations.create", name=name, is_private=True))["channel"]["id"]
            break
        except SlackError as e:
            attempt += 1
            if e.code != "name_taken" or attempt > 9:
                raise
            name = wr_naming.collision_name(base, attempt)
    followers = (await db.execute(select(User.email).join(CaseFollower, CaseFollower.user_id == User.id)
                                  .where(CaseFollower.case_id == case.id, User.is_active.is_(True)))).scalars().all()
    owner = await db.get(User, case.owner_id) if case.owner_id else None
    ids = {clicker_slack_id} if clicker_slack_id else set()
    if not ids:  # no Slack click to identify the opener (opened from the case page): look them up
        opener = await _slack_id(token, user.email)
        if opener:
            ids.add(opener)
    for email in set(followers) | ({owner.email} if owner else set()):
        sid = await _slack_id(token, email)
        if sid:
            ids.add(sid)
    try:
        if ids:
            await slack_call(token, "conversations.invite", channel=channel, users=",".join(sorted(ids)))
    except SlackError as e:
        if e.code not in ("already_in_channel", "cant_invite_self"):
            logger.warning("war room invite failed for case %s: %s", case.id, e.code)
    case.slack_channel_id = channel
    case.slack_channel_name = name
    case.slack_channel_created_at = now
    case.slack_listen = bool(listen)
    db.add(TimelineEvent(case_id=case.id, user_id=user.id, event_type="other",
                         content=f"Slack war room opened: #{name}"))
    await db.commit()
    names = await _names(db, [case.owner_id])
    # the room exists and is committed: the intro card is best-effort
    await _safe(slack_call(token, "chat.postMessage", channel=channel, text=ui.card_text(case),
                           blocks=ui.case_card(case, names.get(case.owner_id), note=f"War room for case #{case.id:04d}")))
    return channel


# --- responder alerts and live cards (Celery) ---

def should_alert(min_severity: Optional[str], severity, *, previous=None) -> bool:
    """Alert when the case is at/above the threshold and (for updates) just crossed it."""
    if not min_severity:
        return False
    floor, now = ui.severity_rank(min_severity), ui.severity_rank(severity)
    if now < floor or floor < 0:
        return False
    return previous is None or ui.severity_rank(previous) < floor


async def _rerender_suggestions(db, token: str, tenant_id: int, changes: dict) -> None:
    """Redraw a SOC-bot suggestions thread reply from the current DB state."""
    from app.models.war_room_suggestion import WarRoomSuggestion
    channel, ts = changes.get("channel"), changes.get("reply_ts")
    if not channel or not ts:
        return
    rows = (await db.execute(select(WarRoomSuggestion).where(
        WarRoomSuggestion.tenant_id == tenant_id, WarRoomSuggestion.slack_channel == channel,
        WarRoomSuggestion.reply_ts == ts).order_by(WarRoomSuggestion.id))).scalars().all()
    if not rows:
        return
    names = await _names(db, [r.resolved_by_user_id for r in rows])
    await _safe(slack_call(token, "chat.update", channel=channel, ts=ts, text="SOC-bot suggestions",
                           blocks=ui.suggestions_reply(rows, tenant_id, names)))


async def on_case_event(tenant_id: int, event_type: str, case_id: int, changes: dict) -> None:
    async with AsyncSessionLocal() as db:
        try:
            integ, token = await load_integration(db, tenant_id)
        except SlackError:
            return  # Slack not configured for this tenant
        if event_type == "war_room.rerender":
            await _rerender_suggestions(db, token, tenant_id, changes or {})
            return
        case = await _case(db, tenant_id, case_id)
        if not case:
            return
        names = await _names(db, [case.owner_id])
        owner = names.get(case.owner_id)
        alert_channel = integ.alert_channel or integ.default_channel
        sev_change = (changes or {}).get("severity") or {}

        note = None
        if event_type == "case.created" and should_alert(integ.alert_min_severity, case.severity):
            note = ":rotating_light: New case"
        elif event_type == "case.updated" and sev_change and should_alert(
                integ.alert_min_severity, case.severity, previous=sev_change.get("from")):
            note = f":arrow_up: Severity raised from {sev_change.get('from')} to {sev_change.get('to')}"
        elif event_type == "sla_breach" and should_alert(integ.alert_min_severity, case.severity):
            note = f":alarm_clock: {changes.get('label', 'SLA')} breached"

        # 1. Responder channel: post the card once, then keep it current and thread later alerts under it.
        if note and alert_channel:
            if case.slack_card_ts:
                await _safe(slack_call(token, "chat.postMessage", channel=case.slack_card_channel,
                                       thread_ts=case.slack_card_ts, text=note, reply_broadcast=True))
            else:
                msg = await _safe(slack_call(token, "chat.postMessage", channel=alert_channel, text=ui.card_text(case),
                                             blocks=ui.case_card(case, owner, note=note)))
                if msg:
                    case.slack_card_channel, case.slack_card_ts = msg["channel"], msg["ts"]
                    await db.commit()
        if case.slack_card_ts and event_type != "case.created":
            await _safe(slack_call(token, "chat.update", channel=case.slack_card_channel, ts=case.slack_card_ts,
                                   text=ui.card_text(case), blocks=ui.case_card(case, owner)))

        # 2. War room: one card per meaningful change.
        if case.slack_channel_id and event_type in ("case.updated", "sla_breach"):
            cards = []
            if event_type == "sla_breach":
                cards.append(("sla.breached", {"label": (changes or {}).get("label", "SLA")}, None))
            else:
                for key, ev in (("status", "status.changed"), ("severity", "severity.changed"),
                                ("owner_id", "owner.changed")):
                    ch = (changes or {}).get(key)
                    if isinstance(ch, dict):
                        cards.append((ev, dict(ch), ch.get("by")))
            if cards:
                ids = [c[2] for c in cards] + [c[1].get(k) for c in cards if c[0] == "owner.changed" for k in ("from", "to")]
                who = await _names(db, [i for i in ids if isinstance(i, int)])
                for ev, payload, by in cards:
                    if ev == "owner.changed":
                        payload = {"from": who.get(payload.get("from")), "to": who.get(payload.get("to"))}
                    msg = ui.war_room_card(ev, case, payload, actor=who.get(by))
                    await _safe(slack_call(token, "chat.postMessage", channel=case.slack_channel_id,
                                           text=msg["text"], attachments=msg["attachments"]))

        if case.slack_channel_id and event_type in ("task.completed", "workflow.finished", "containment.confirmed"):
            payload = dict(changes or {})
            if event_type == "containment.confirmed":
                payload["after_workflow"] = await _recent_workflow_name(db, tenant_id, case_id, payload.get("at"))
            msg = ui.war_room_card(event_type, case, payload, actor=(await _names(db, [payload.get("user_id")])).get(payload.get("user_id")))
            await _safe(slack_call(token, "chat.postMessage", channel=case.slack_channel_id,
                                   text=msg["text"], attachments=msg["attachments"]))

        # 3. Owner DM on escalation (assignment and SLA breach DMs come from notifications delivery).
        if event_type == "case.updated" and sev_change and ui.severity_rank(sev_change.get("to")) > ui.severity_rank(sev_change.get("from")):
            owner_user = await db.get(User, case.owner_id) if case.owner_id else None
            sid = await _slack_id(token, owner_user.email if owner_user else None)
            if sid:
                await _dm(token, sid, ui.card_text(case), ui.case_card(
                    case, owner, note=f"Severity raised on your case: {sev_change.get('from')} → {sev_change.get('to')}"))


async def _recent_workflow_name(db, tenant_id: int, case_id: int, at) -> Optional[str]:
    """Name of a workflow that succeeded on the case at most 10 minutes before `at` (containment time)."""
    from datetime import datetime, timedelta
    from app.models.workflow import Workflow, WorkflowRun
    try:
        when = datetime.fromisoformat(str(at))
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    row = (await db.execute(
        select(Workflow.name).join(WorkflowRun, WorkflowRun.workflow_id == Workflow.id).where(
            WorkflowRun.tenant_id == tenant_id, WorkflowRun.case_id == case_id, WorkflowRun.status == "succeeded",
            WorkflowRun.is_dry_run.is_(False), WorkflowRun.finished_at <= when,
            WorkflowRun.finished_at >= when - timedelta(minutes=10)).order_by(WorkflowRun.finished_at.desc()).limit(1))).first()
    return row[0] if row else None


async def _safe(coro):
    try:
        return await coro
    except SlackError as e:
        logger.warning("Slack call failed: %s", e.code or e)
        return None


# --- App Home (Celery) ---

async def publish_home(tenant_id: int, slack_user_id: str) -> None:
    async with AsyncSessionLocal() as db:
        try:
            _, token = await load_integration(db, tenant_id)
        except SlackError:
            return
        user = await linked_user(db, tenant_id, token, slack_user_id)
        cases = []
        if user:
            cases = (await db.execute(select(Case).where(Case.tenant_id == tenant_id, Case.owner_id == user.id,
                                                         Case.status.in_(_OPEN)).order_by(Case.id.desc()).limit(15))).scalars().all()
        names = {user.id: user.full_name or user.email} if user else {}
        await _safe(slack_call(token, "views.publish", user_id=slack_user_id,
                               view=ui.home_view(cases, names, linked=bool(user))))


# --- transport-independent dispatch (HTTP request URLs and Socket Mode share these) ---

async def dispatch_interactive(db, integ: SlackIntegration, payload: dict) -> Optional[dict]:
    """Handle one verified interactivity payload; returns the response body Slack expects (or None)."""
    from app.tasks.workflows import handle_slack_interaction_task

    kind = payload.get("type")
    try:
        if kind == "view_submission":
            return await handle_submission(db, integ, payload)
        if kind in ("shortcut", "message_action", "block_actions") and await open_modal_for(db, integ, payload):
            return None
    except SlackError:
        logger.exception("Slack interaction failed for tenant %s", integ.tenant_id)
        return None
    if kind == "block_actions":
        handle_slack_interaction_task.delay(integ.tenant_id, payload)  # slower work: Celery
    return None


async def dispatch_command(db, integ: SlackIntegration, form: dict) -> dict:
    try:
        return await handle_command(db, integ, form)
    except SlackError:
        logger.exception("Slack command failed for tenant %s", integ.tenant_id)
        return _ephemeral("Something went wrong talking to Slack. Try again.")


def _is_listenable_message(event: dict) -> bool:
    """A human-authored plain message (or thread broadcast); edits, joins and bot posts never count."""
    return (event.get("type") == "message" and not event.get("bot_id")
            and event.get("subtype") in (None, "thread_broadcast")
            and bool(event.get("user") and event.get("channel") and event.get("ts") and event.get("text")))


def dispatch_event(integ: SlackIntegration, payload: dict) -> None:
    from app.tasks.slack import slack_home_task

    event = payload.get("event") or {}
    if event.get("type") == "app_home_opened" and event.get("tab") == "home" and event.get("user"):
        slack_home_task.delay(integ.tenant_id, event["user"])
    elif _is_listenable_message(event):
        from app.tasks.war_room import war_room_message_task
        war_room_message_task.delay(integ.tenant_id, {k: event.get(k) for k in ("channel", "ts", "thread_ts", "user", "text")})
