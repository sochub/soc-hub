# Collaboration: @mentions and Notifications Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Analysts can @mention teammates in case comments and get in-app notifications for activity on cases they own or follow. Mentions and assignments also go out by Slack DM, falling back to email.

**Architecture:**
- A `notifications` table and a `case_followers` table.
- Notification rows are written inside the caller's transaction by `app/notifications/service.py`. It is called from `app/services/case_service.py`, which is the single mutation path used by the API, workflow nodes, Slack buttons and copilot actions, and from the SLA task.
- External delivery goes through a Celery task. It is enqueued by a SQLAlchemy `after_commit` hook from ids stashed on the session.
- The frontend polls the unread count every 30 seconds.

**Tech Stack:** FastAPI, async SQLAlchemy, Alembic, Celery/Redis, React 19, TanStack Query, Tailwind.

**Spec:** `docs/superpowers/specs/2026-10-03-collaboration-notifications-design.md`

## Global Constraints

**Values**
- Migration revision is `d8e9f0a1b2c3`, with `down_revision` `c7d8e9f0a1b2`.
- Notification types are exactly `mention`, `assigned`, `comment`, `status_change`, `severity_change` and `sla_breach`.
- `summary` is plain text of at most 200 characters, and it never contains comment text.
- The mention token is `@[Display Name](user:42)`. The regex is `@\[([^\]\n]{1,100})\]\(user:(\d{1,10})\)`.

**Who gets notified**
- Mentions are parsed only in human-authored comments: `event_type == "comment"` and `user_id is not None`.
- The actor never receives a notification for their own action.
- Recipients must currently hold a membership in the case's tenant. Anyone else is skipped silently.

**Failure handling and logging**
- Notification writes run in `db.begin_nested()`. On failure, log `notification write failed case=%s type=%s err=%s` with the error class name only. The triggering action must still commit.

**External delivery**
- Only `mention` and `assigned` notifications go out externally: Slack DM first, then email.
- At most one external message per `(user, case)` per 300 seconds. The Redis key is `notif:ext:{user_id}:{case_id}`.
- Transient failures retry up to 3 times with `countdown = 30 * 2**attempt`. After that, log `notification delivery failed id=%s reason=%s`.
- The case link is `{FRONTEND_URL}/cases/{case_id}`. Case titles are truncated to 120 characters.
- Logs never contain message text, comment text or email addresses.

**Retention**
- A daily beat task deletes read notifications older than 90 days, and any notification older than 180 days.

**Rules for every task**
- Every query is scoped by user and active tenant. A cross-tenant request returns 404.
- Test cleanup deletes by exact ids only. Never run a broad DELETE on the live DB.
- Backend tests run in the container: `docker exec case_management-backend-1 python -m pytest -q tests/<file>`. Use `docker cp` to copy changed files in.
- After backend changes, run `docker restart case_management-backend-1 case_management-worker-1`.
- Frontend: never use `dangerouslySetInnerHTML`, `window.confirm`, `alert` or `window.open`. Use the light "Telemetry Console" theme and `components/layout/Modal.tsx`.

## Review Focus

1. **Crafted display names.** A comment could carry `@[CEO](user:42)` so that it shows a misleading name. The server rewrites the display name of every valid token to the user's real `full_name` (or email) when saving. → Task 2 test `test_canonicalize_rewrites_display_name`.
2. **Mentions from automation text.** Workflow notes (`user_id=None`) can contain alert data, which an attacker controls. They must never mention or notify anyone. → Task 3 test `test_automation_note_never_mentions`.
3. **A failed notification write must not lose the comment.** → Task 3 test `test_write_failure_keeps_comment`.
4. **A rollback after a successful savepoint must not send DMs.** The ids are only enqueued after the outer commit, and they are cleared on rollback. → Task 4 test `test_rollback_enqueues_nothing`.
5. **Duplicate mentions, and editing a comment several times.** Each user gets exactly one notification per newly added mention. → Task 3 test `test_edit_notifies_only_new_mentions`.

---

### Task 1: Models, migration, tenant deletion

**Files:**
- Create: `backend/app/models/notification.py`
- Create: `backend/alembic/versions/d8e9f0a1b2c3_notifications.py`
- Modify:
  - `backend/app/db/base.py` (import the new models, following how the other models are registered)
  - `backend/app/utils/tenant_deletion.py` (delete `Notification` and `CaseFollower` rows for the tenant before cases)
- Test: `backend/tests/test_notifications_models.py`

**Interfaces:**
- Produces:
  - `Notification` (`__tablename__ = "notifications"`)
  - `CaseFollower` (`__tablename__ = "case_followers"`)
  - `NOTIFICATION_TYPES = ("mention", "assigned", "comment", "status_change", "severity_change", "sla_breach")`
  - `EXTERNAL_TYPES = ("mention", "assigned")`

- [ ] **Step 1: Model**

```python
# backend/app/models/notification.py
from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.sql import func

from app.db.base_class import Base

NOTIFICATION_TYPES = ("mention", "assigned", "comment", "status_change", "severity_change", "sla_breach")
EXTERNAL_TYPES = ("mention", "assigned")


class Notification(Base):
    __tablename__ = "notifications"
    __table_args__ = (Index("ix_notifications_user_unread", "user_id", "tenant_id", "read_at"),)

    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    case_id = Column(Integer, ForeignKey("cases.id", ondelete="CASCADE"), nullable=False)
    type = Column(String(32), nullable=False)
    actor_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    timeline_event_id = Column(Integer, ForeignKey("timeline_events.id", ondelete="SET NULL"), nullable=True)
    summary = Column(String(200), nullable=False)
    read_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)


class CaseFollower(Base):
    __tablename__ = "case_followers"
    __table_args__ = (UniqueConstraint("case_id", "user_id", name="uq_case_follower"),)

    id = Column(Integer, primary_key=True)
    case_id = Column(Integer, ForeignKey("cases.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
```

Verify the timeline table name with `grep -n __tablename__ backend/app/models/case.py`, and use whatever name it has.

- [ ] **Step 2: Migration**

Write `revision = "d8e9f0a1b2c3"` and `down_revision = "c7d8e9f0a1b2"`.
- `upgrade()` creates both tables with the columns, FKs (including `ondelete`), indexes and unique constraint exactly as in the model.
- `downgrade()` drops `case_followers`, then `notifications`.

Then:
1. Run it: `docker exec case_management-backend-1 alembic upgrade head`.
2. Check there is a single head: `alembic heads` must show only `d8e9f0a1b2c3`.

- [ ] **Step 3: Tenant deletion**

In `tenant_deletion.py`, before the `delete(TimelineEvent)` / `delete(Case)` lines, add:

```python
await db.execute(delete(Notification).where(Notification.tenant_id == tenant.id))
await db.execute(delete(CaseFollower).where(CaseFollower.tenant_id == tenant.id))
```

- [ ] **Step 4: Test** (`test_notifications_models.py`)
  1. Create a temporary tenant, a user, a membership and a case, each with a unique slug or email based on `secrets.token_hex(3)` and the slug prefix `wf-test-`.
  2. Insert one `Notification` and one `CaseFollower`.
  3. Inserting a duplicate `CaseFollower` raises `IntegrityError`.
  4. Deleting the case cascades to both rows.
  5. Clean up by exact ids, then assert that 0 `wf-test-%` tenants are left.

- [ ] **Step 5: Run the test file, then commit** as `feat(notifications): notifications and case_followers tables`.

---

### Task 2: Mention parsing and validation

**Files:**
- Create: `backend/app/notifications/__init__.py` (empty) and `backend/app/notifications/mentions.py`
- Test: `backend/tests/test_mentions.py`

**Interfaces:**
- Produces:
  - `MENTION_RE`
  - `parse_mention_ids(text: str) -> list[int]`: unique ids, in order of first appearance.
  - `async def canonicalize_mentions(db, tenant_id: int, text: str) -> tuple[str, set[int]]`: returns `(rewritten_text, valid_ids)`.
- `canonicalize_mentions` rules:
  - A token whose id belongs to a member of `tenant_id` is rewritten to `@[<full_name or email>](user:<id>)`.
  - Any other token is left exactly as written. Its id is not included in `valid_ids`.
  - Display names are sanitised: `]`, `[` and newlines are stripped, and the name is truncated to 100 characters.
  - It runs one query for all ids.

- [ ] **Step 1: Failing tests**

```python
from app.notifications.mentions import parse_mention_ids, MENTION_RE

def test_parse_ids_unique_in_order():
    assert parse_mention_ids("hi @[A](user:3) and @[B](user:1) @[A](user:3)") == [3, 1]

def test_parse_ignores_malformed():
    assert parse_mention_ids("@[A](user:x) @A @[](user:2) @[A]\n(user:4) @[x](user:12345678901)") == []
```

DB tests run via `asyncio.run`, following `tests/test_wf_secret_nonce.py`:
- Tenant A has user U1 with `full_name` "Real Name". Tenant B has user U2.
- `test_canonicalize_rewrites_display_name`: `"ping @[CEO](user:U1)"` becomes `("ping @[Real Name](user:U1)", {U1})`.
- `test_canonicalize_foreign_and_unknown_untouched`: a U2 token and the token `user:999999999` are left verbatim, and `valid_ids` is the empty set.
- A user with no `full_name` gets their email as the display name.
- Clean up by ids.

- [ ] **Step 2: Implement**

```python
# backend/app/notifications/mentions.py
"""@mention tokens in case comments: `@[Display Name](user:42)`."""
import re
from typing import List, Set, Tuple

from sqlalchemy import select

from app.models.membership import TenantMembership
from app.models.user import User

MENTION_RE = re.compile(r"@\[([^\]\n]{1,100})\]\(user:(\d{1,10})\)")


def parse_mention_ids(text: str) -> List[int]:
    seen: List[int] = []
    for m in MENTION_RE.finditer(text or ""):
        uid = int(m.group(2))
        if uid not in seen:
            seen.append(uid)
    return seen


def _display(user: User) -> str:
    name = (user.full_name or user.email or "").replace("[", "").replace("]", "").replace("\n", " ")
    return name[:100] or "user"


async def canonicalize_mentions(db, tenant_id: int, text: str) -> Tuple[str, Set[int]]:
    ids = parse_mention_ids(text)
    if not ids:
        return text, set()
    rows = (await db.execute(
        select(User).join(TenantMembership, TenantMembership.user_id == User.id)
        .where(TenantMembership.tenant_id == tenant_id, User.id.in_(ids))
    )).scalars().all()
    members = {u.id: u for u in rows}

    def sub(m: "re.Match") -> str:
        u = members.get(int(m.group(2)))
        return f"@[{_display(u)}](user:{u.id})" if u else m.group(0)

    return MENTION_RE.sub(sub, text), set(members)
```

- [ ] **Step 3: Run the tests, then commit** as `feat(notifications): mention token parsing and validation`.

---

### Task 3: Notification service and triggers

**Files:**
- Create: `backend/app/notifications/service.py`
- Modify:
  - `backend/app/services/case_service.py`: call the hooks from `create_case_record`, `apply_case_update` and `add_timeline_note`, and add `edit_timeline_note`.
  - `backend/app/api/v1/cases.py`: `update_timeline_event` uses `edit_timeline_note`.
  - `backend/app/api/v1/copilot.py`: the `add_timeline_note` and `update_case` actions (about lines 648–690) go through `case_service.add_timeline_note` / `apply_case_update` instead of writing `TimelineEvent` or case fields directly. Keep the response shapes unchanged.
  - `backend/app/tasks/sla.py`: call `on_sla_breach` next to `_notify_recipients`.
- Test: `backend/tests/test_notification_triggers.py`

**Interfaces:**
- Consumes: `canonicalize_mentions`, `parse_mention_ids` (Task 2); `Notification`, `CaseFollower`, `EXTERNAL_TYPES` (Task 1).
- Produces, all `async`, all flush-only (they never commit):
  - `follow(db, case, user_id)`: idempotent.
  - `unfollow(db, case, user_id)`: idempotent.
  - `is_following(db, case_id, user_id) -> bool`
  - `on_comment(db, case, event, actor_id, previous_content: str | None = None)`
  - `on_case_update(db, case, changes: dict, actor_id)`
  - `on_case_created(db, case, actor_id)`
  - `on_sla_breach(db, case, breach_type: str)`
- Session stash: ids of external-type notifications created in a successful savepoint are appended to `db.info.setdefault("notif_external", [])`. Task 4 consumes it.
- Comment canonicalisation: `add_timeline_note` canonicalises the content with `canonicalize_mentions` before saving, but only for human comments.

- [ ] **Step 1: Failing tests** (`test_notification_triggers.py`)

These are integration tests that go through `case_service` against the real DB. Fixtures:
- Tenant T has an actor A (analyst), users B and C, and a viewer V, each with a membership.
- Tenant X has user Z.
- Case K belongs to T and is owned by A.

Tests (each named exactly as below):
- `test_case_created_owner_follows_and_assigned`:
  - `create_case_record(... data={"title": "t", "owner_id": B}, user_id=A)`: B follows, and B has 1 `assigned`.
  - With `owner_id` omitted, A becomes the owner and follows, with no notification because A is the actor.
- `test_comment_mentions_and_followers`:
  - B follows K. A comments `"see @[x](user:C) @[y](user:Z)"`.
  - C gets `mention` and now follows K.
  - B gets `comment`.
  - Z gets nothing, because Z is in another tenant.
  - A gets nothing, and A now follows K.
  - The stored comment content contains `@[<C full_name>](user:C)` and the Z token unchanged.
- `test_mentioned_follower_gets_only_mention`: C already follows and is mentioned, so C has exactly 1 notification, of type `mention`.
- `test_status_and_severity_changes`: followers B and V get `status_change` and `severity_change`. Their summaries contain the new value and `#<case id>`, and never the comment text.
- `test_assignment_by_workflow`: `apply_case_update(update_data={"owner_id": C}, user_id=None)` gives C an `assigned` with `actor_id` None, and C follows.
- `test_automation_note_never_mentions`: `add_timeline_note(content="[automation] @[x](user:C)", user_id=None)` creates no mention. Followers get `comment` with actor None, and the content is not rewritten.
- `test_removed_member_skipped`: B follows. Delete B's membership by its id, then comment. B gets nothing.
- `test_edit_notifies_only_new_mentions`:
  - Comment mentioning C, then `edit_timeline_note` to mention C and B: only B gets a new `mention`.
  - Editing again with the same content creates nothing.
- `test_write_failure_keeps_comment`: monkeypatch `service._members` to raise `RuntimeError`.
  - `add_timeline_note` plus commit succeeds and the comment exists.
  - No notifications are created.
  - caplog has exactly one `notification write failed` line containing `RuntimeError` and no comment text.
- `test_sla_breach_notifies_owner_and_followers`: call `on_sla_breach(db, case, "response")`. The owner (if not the actor) and followers get `sla_breach`.
- `test_external_ids_stashed`: after a mention, `db.info["notif_external"]` contains exactly the mention's id. `comment` and `status_change` ids are never stashed.

All test data is cleaned up by id.

- [ ] **Step 2: Implement `service.py`**

```python
# backend/app/notifications/service.py
"""Write in-app notifications and follower rows for case activity. Flush-only; the caller commits.

Every public hook runs in a SAVEPOINT: a failure is logged and swallowed so the triggering
action still commits. Ids of externally delivered notifications are stashed on
db.info["notif_external"] for the after-commit hook (app/notifications/delivery.py).
"""
import logging
from typing import Iterable, Optional, Set

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.models.case import Case
from app.models.membership import TenantMembership
from app.models.notification import EXTERNAL_TYPES, CaseFollower, Notification
from app.models.user import User
from app.notifications.mentions import canonicalize_mentions, parse_mention_ids

logger = logging.getLogger(__name__)


async def _members(db, tenant_id: int, user_ids: Iterable[int]) -> Set[int]:
    ids = {i for i in user_ids if i}
    if not ids:
        return set()
    rows = await db.execute(select(TenantMembership.user_id).where(
        TenantMembership.tenant_id == tenant_id, TenantMembership.user_id.in_(ids)))
    return set(rows.scalars().all())


async def _followers(db, case_id: int) -> Set[int]:
    return set((await db.execute(select(CaseFollower.user_id).where(CaseFollower.case_id == case_id))).scalars().all())


async def _actor_name(db, actor_id: Optional[int]) -> str:
    if not actor_id:
        return "Automation"
    u = (await db.execute(select(User).where(User.id == actor_id))).scalars().first()
    return ((u.full_name or u.email) if u else "Someone")[:60]


async def _follow(db, case: Case, user_id: int) -> None:
    await db.execute(pg_insert(CaseFollower).values(case_id=case.id, user_id=user_id, tenant_id=case.tenant_id)
                     .on_conflict_do_nothing(constraint="uq_case_follower"))


def _add(db, case: Case, user_id: int, type_: str, summary: str, actor_id, event_id=None) -> Notification:
    n = Notification(tenant_id=case.tenant_id, user_id=user_id, case_id=case.id, type=type_,
                     actor_id=actor_id, timeline_event_id=event_id, summary=summary[:200])
    db.add(n)
    return n


async def _guarded(db, case: Case, type_: str, body) -> None:
    created = []
    try:
        async with db.begin_nested():
            created = await body()
            await db.flush()
    except Exception as e:  # never block the triggering action
        logger.warning("notification write failed case=%s type=%s err=%s", case.id, type_, type(e).__name__)
        return
    ext = [n.id for n in created if n.type in EXTERNAL_TYPES]
    if ext:
        db.info.setdefault("notif_external", []).extend(ext)


async def follow(db, case: Case, user_id: int) -> None:
    await _follow(db, case, user_id)


async def unfollow(db, case: Case, user_id: int) -> None:
    await db.execute(delete(CaseFollower).where(CaseFollower.case_id == case.id, CaseFollower.user_id == user_id))


async def is_following(db, case_id: int, user_id: int) -> bool:
    return (await db.execute(select(CaseFollower.id).where(
        CaseFollower.case_id == case_id, CaseFollower.user_id == user_id))).first() is not None


async def on_comment(db, case: Case, event, actor_id: Optional[int], previous_content: Optional[str] = None) -> None:
    async def body():
        mentioned = set()
        if actor_id is not None and event.event_type == "comment":
            new_ids = set(parse_mention_ids(event.content)) - set(parse_mention_ids(previous_content or ""))
            mentioned = await _members(db, case.tenant_id, new_ids) - {actor_id}
            await _follow(db, case, actor_id)
            for uid in mentioned:
                await _follow(db, case, uid)
        if previous_content is not None:  # edits only notify newly mentioned users
            others = set()
        else:
            others = await _members(db, case.tenant_id, await _followers(db, case.id)) - mentioned - {actor_id}
        name = await _actor_name(db, actor_id)
        out = [_add(db, case, u, "mention", f"{name} mentioned you on case #{case.id}", actor_id, event.id) for u in mentioned]
        out += [_add(db, case, u, "comment", f"{name} commented on case #{case.id}", actor_id, event.id) for u in others]
        return out
    await _guarded(db, case, "comment", body)


async def on_case_update(db, case: Case, changes: dict, actor_id: Optional[int]) -> None:
    async def body():
        out = []
        name = await _actor_name(db, actor_id)
        new_owner = changes.get("owner_id", {}).get("to")
        if new_owner and new_owner != actor_id and await _members(db, case.tenant_id, [new_owner]):
            await _follow(db, case, new_owner)
            out.append(_add(db, case, new_owner, "assigned", f"{name} assigned you case #{case.id}", actor_id))
        elif new_owner:
            await _follow(db, case, new_owner)
        followers = await _members(db, case.tenant_id, await _followers(db, case.id)) - {actor_id}
        for field, type_ in (("status", "status_change"), ("severity", "severity_change")):
            if field in changes:
                label = "Status" if field == "status" else "Severity"
                summary = f"{label} changed to {changes[field]['to']} on case #{case.id}"
                out += [_add(db, case, u, type_, summary, actor_id) for u in followers]
        return out
    await _guarded(db, case, "case_update", body)


async def on_case_created(db, case: Case, actor_id: Optional[int]) -> None:
    if not case.owner_id:
        return
    await on_case_update(db, case, {"owner_id": {"from": None, "to": case.owner_id}}, actor_id)


async def on_sla_breach(db, case: Case, breach_type: str) -> None:
    async def body():
        users = await _members(db, case.tenant_id, (await _followers(db, case.id)) | {case.owner_id})
        label = "response" if breach_type == "response" else "resolution"
        return [_add(db, case, u, "sla_breach", f"Case #{case.id} breached its {label} SLA", None) for u in users]
    await _guarded(db, case, "sla_breach", body)
```

The failure-isolation test monkeypatches `service._members`.

- [ ] **Step 3: Wire it into `case_service.py`**

```python
from app.notifications import service as notify
from app.notifications.mentions import canonicalize_mentions

# create_case_record: after the audit log, before the triage flush
    await notify.on_case_created(db, case, user_id)

# apply_case_update: after the audit-log block, before the final flush
    if changes:
        await notify.on_case_update(db, case, changes, user_id)

async def add_timeline_note(db, *, case, content, user_id, event_type="comment") -> TimelineEvent:
    if event_type == "comment" and user_id is not None:
        content, _ = await canonicalize_mentions(db, case.tenant_id, content)
    ev = TimelineEvent(case_id=case.id, user_id=user_id, event_type=event_type, content=content)
    db.add(ev)
    await db.flush()
    if event_type == "comment":
        await notify.on_comment(db, case, ev, user_id)
    return ev

async def edit_timeline_note(db, *, case, event, update_data: dict, user_id) -> TimelineEvent:
    previous = event.content
    new_type = update_data.get("event_type", event.event_type)
    content = update_data.get("content", event.content)
    if new_type == "comment" and user_id is not None:
        content, _ = await canonicalize_mentions(db, case.tenant_id, content)
    event.event_type, event.content = new_type, content
    await db.flush()
    if new_type == "comment" and content != previous:
        await notify.on_comment(db, case, event, user_id, previous_content=previous or "")
    return event
```

In `api/v1/cases.py` `update_timeline_event`, replace the `setattr` loop with this:

```python
    case = ...  # keep the Case row loaded by the existing tenant check, instead of discarding it
    await edit_timeline_note(db, case=case, event=event, update_data=event_in.model_dump(exclude_unset=True),
                             user_id=current_user.id)
```

- [ ] **Step 4: Copilot**

In `api/v1/copilot.py`, the `add_timeline_note` action calls `case_service.add_timeline_note(db, case=<loaded case>, content=content, user_id=current_user.id)`. The `update_case` action builds an `update_data` dict (status/severity enums) and calls `apply_case_update(db, case=case, update_data=..., user_id=current_user.id)`.

Keep the existing response JSON. Run `tests/ -k copilot` and fix any test that asserted the old direct writes, but only where the behaviour change is the intended one (a timeline event or audit row now exists).

- [ ] **Step 5: SLA**

In `tasks/sla.py` `_check_tenant`, call `await notify.on_sla_breach(db, case, "response")` (or `"resolution"`) right after each `_notify_recipients(...)` call.

- [ ] **Step 6: Run the tests**
  1. The new test file.
  2. `tests/ -k "case or copilot or wf or sla"`.
  3. The full suite.
  4. Restart the backend and worker.

- [ ] **Step 7: Commit** as `feat(notifications): mention, follow and activity notifications from case_service`.

---

### Task 4: External delivery, after-commit enqueue, retention

**Files:**
- Create:
  - `backend/app/notifications/delivery.py` (the after-commit hook plus the message-building code)
  - `backend/app/tasks/notifications.py` (Celery tasks)
- Modify:
  - `backend/app/worker.py`: `import app.tasks.notifications`, and add a beat entry `"prune-notifications": {"task": "app.tasks.notifications.prune_notifications_task", "schedule": 86400.0}`.
  - `backend/app/db/session.py`, or wherever `AsyncSessionLocal` is defined: register the hook once at import.
- Test: `backend/tests/test_notification_delivery.py`

**Interfaces:**
- Consumes: `db.info["notif_external"]` (Task 3); `slack_call`, `load_integration`, `SlackError` (`app/services/slack_service.py`); `slack_escape` (`app/workflows/templating.py`); `_send_email` and `_smtp_configured` (`app/services/email_service.py`); `settings.FRONTEND_URL`.
- Produces:
  - `register_after_commit_hook()`
  - `deliver_notifications_task(ids: list[int])`
  - `prune_notifications_task()`
  - `async def deliver_one(db, notification_id: int, redis) -> str`, which returns one of `"slack"`, `"email"`, `"skipped"`, `"rate_limited"`, `"none"`.
  - `class TransientDeliveryError(Exception)`

- [ ] **Step 1: Failing tests**

All with Slack and SMTP mocked by monkeypatching `delivery.slack_call`, `delivery.load_integration`, `delivery._send_email` and `delivery._smtp_configured`. Use a fake redis object implementing `set(key, val, nx=True, ex=300)`.
- `test_slack_first`: with an integration present, `deliver_one` returns `"slack"`. The calls are `users.lookupByEmail`, then `conversations.open`, then `chat.postMessage`. The posted text contains the summary, the escaped title and `/cases/<id>`, and never the comment text (seed a comment containing a sentinel string).
- `test_email_fallback_on_users_not_found`: `users.lookupByEmail` raises `SlackError("users.lookupByEmail: users_not_found")`, so the email path is used and the function returns `"email"`.
- `test_no_slack_email`: `load_integration` raises `SlackError("Slack is not configured…")`, so the result is `"email"`.
- `test_neither`: no Slack and no SMTP gives `"none"`.
- `test_rate_limited`: the second call for the same user and case within 300s returns `"rate_limited"`, and Slack is called once.
- `test_transient_retries`: `chat.postMessage` raises `SlackError("chat.postMessage: ratelimited")`, so `deliver_one` raises `TransientDeliveryError`. Network-type errors (`SlackError` whose message has no Slack error code, e.g. from `httpx`) are transient too.
- `test_non_external_type_skipped`: a `comment` notification gives `"skipped"`.
- `test_rollback_enqueues_nothing`:
  1. Monkeypatch `deliver_notifications_task.delay` to record its calls.
  2. Create a mention through `add_timeline_note`, then `await db.rollback()`. Nothing is recorded.
  3. Repeat with `await db.commit()`. It is recorded exactly once with that id.
- `test_prune`: insert a read notification aged 91 days, an unread one aged 181 days, and a fresh unread one, by setting `created_at`/`read_at` explicitly. After running prune, only the fresh one is left. Its query is scoped to the test ids when asserting.
- caplog: no record contains the email address, the summary text or the comment sentinel.

- [ ] **Step 2: Implement `delivery.py`**

```python
# backend/app/notifications/delivery.py
"""External delivery (Slack DM, then email) for mention/assigned notifications."""
import html
import logging
import re

from sqlalchemy import event, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.case import Case
from app.models.notification import EXTERNAL_TYPES, Notification
from app.models.user import User
from app.services.email_service import _send_email, _smtp_configured
from app.services.slack_service import SlackError, load_integration, slack_call
from app.workflows.templating import slack_escape

logger = logging.getLogger(__name__)
_SLACK_CODE = re.compile(r":\s*([a-z_]+)$")
_TRANSIENT_CODES = {"ratelimited", "rate_limited", "internal_error", "fatal_error", "service_unavailable", "request_timeout"}


class TransientDeliveryError(Exception):
    pass


def _after_commit(session) -> None:
    ids = session.info.pop("notif_external", None)
    if ids:
        from app.tasks.notifications import deliver_notifications_task
        deliver_notifications_task.delay(list(ids))


def _after_soft_rollback(session, previous_transaction) -> None:
    # Only an outermost rollback discards the stash; a failed SAVEPOINT must not drop ids
    # stashed by earlier successful hooks in the same transaction.
    if not previous_transaction.nested and previous_transaction.parent is None:
        session.info.pop("notif_external", None)


def register_after_commit_hook() -> None:
    if not event.contains(Session, "after_commit", _after_commit):
        event.listen(Session, "after_commit", _after_commit)
        event.listen(Session, "after_soft_rollback", _after_soft_rollback)


def _is_transient(e: SlackError) -> bool:
    m = _SLACK_CODE.search(str(e))
    return m is None or m.group(1) in _TRANSIENT_CODES  # no code => transport error


def _link(case_id: int) -> str:
    base = (settings.FRONTEND_URL or "").rstrip("/")
    return f"{base}/cases/{case_id}" if base else ""


async def deliver_one(db, notification_id: int, redis) -> str:
    n = (await db.execute(select(Notification).where(Notification.id == notification_id))).scalars().first()
    if n is None or n.type not in EXTERNAL_TYPES:
        return "skipped"
    if not await redis.set(f"notif:ext:{n.user_id}:{n.case_id}", "1", nx=True, ex=300):
        return "rate_limited"
    user = (await db.execute(select(User).where(User.id == n.user_id))).scalars().first()
    case = (await db.execute(select(Case).where(Case.id == n.case_id))).scalars().first()
    if not user or not case or not user.is_active:
        return "skipped"
    title, link = (case.title or "")[:120], _link(case.id)
    try:
        _, token = await load_integration(db, n.tenant_id)
    except SlackError:
        token = None
    if token:
        try:
            uid = (await slack_call(token, "users.lookupByEmail", email=user.email))["user"]["id"]
            channel = (await slack_call(token, "conversations.open", users=uid))["channel"]["id"]
            text = f"{slack_escape(n.summary)}: {slack_escape(title)}" + (f"\n<{link}|Open case>" if link else "")
            await slack_call(token, "chat.postMessage", channel=channel, text=text)
            return "slack"
        except SlackError as e:
            if _is_transient(e):
                await redis.delete(f"notif:ext:{n.user_id}:{n.case_id}")  # let the retry through
                raise TransientDeliveryError(type(e).__name__) from None
            # users_not_found etc.: fall through to email
    if _smtp_configured():
        body = f"<p>{html.escape(n.summary)}: <strong>{html.escape(title)}</strong></p>"
        if link:
            body += f'<p><a href="{html.escape(link)}">Open case</a></p>'
        if _send_email(user.email, n.summary, body):
            return "email"
    return "none"
```

A transient failure deletes the rate-limit key before re-raising, so the retry isn't rate-limited by its own first attempt. Add a test assertion for this. Also add `test_failed_savepoint_keeps_earlier_stash`: one successful mention is stashed, then a later hook in the same transaction fails, and the stash still holds the first id after commit.

- [ ] **Step 3: Implement the tasks** (`backend/app/tasks/notifications.py`), following the `tasks/sla.py` pattern:

```python
import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, or_

from app.db.session import AsyncSessionLocal, engine
from app.models.notification import Notification
from app.notifications.delivery import TransientDeliveryError, deliver_one
from app.worker import celery_app

logger = logging.getLogger(__name__)


async def _deliver(ids):
    import redis.asyncio as aioredis
    from app.core.config import settings
    r = aioredis.from_url(settings.REDIS_URL)
    retry = []
    try:
        async with AsyncSessionLocal() as db:
            for nid in ids:
                try:
                    await deliver_one(db, nid, r)
                except TransientDeliveryError as e:
                    retry.append((nid, str(e)))
                except Exception as e:  # never let one bad row stop the batch
                    logger.warning("notification delivery failed id=%s reason=%s", nid, type(e).__name__)
    finally:
        await r.aclose()
        await engine.dispose()
    return retry


@celery_app.task(bind=True, acks_late=True, max_retries=3)
def deliver_notifications_task(self, ids):
    retry = asyncio.run(_deliver(ids))
    if retry:
        if self.request.retries >= self.max_retries:
            for nid, reason in retry:
                logger.warning("notification delivery failed id=%s reason=%s", nid, reason)
            return
        raise self.retry(args=[[nid for nid, _ in retry]], countdown=30 * 2 ** self.request.retries)


async def _prune():
    now = datetime.now(timezone.utc)
    try:
        async with AsyncSessionLocal() as db:
            await db.execute(delete(Notification).where(or_(
                Notification.created_at < now - timedelta(days=180),
                (Notification.read_at.isnot(None)) & (Notification.created_at < now - timedelta(days=90)))))
            await db.commit()
    finally:
        await engine.dispose()


@celery_app.task(acks_late=True)
def prune_notifications_task():
    asyncio.run(_prune())
```

The test calls `_prune()` directly, with data aged as described. `_prune()` is the only allowed bulk delete, and it is age-bounded by design.

- [ ] **Step 4: Register the hook.** Call `register_after_commit_hook()` once at module import in `app/db/session.py`, below the `AsyncSessionLocal` definition. That way the API and the worker both get it.

- [ ] **Step 5: Run** the new file, `tests/ -k "notif or case or wf"` and then the full suite. Restart the backend and worker. **Commit** as `feat(notifications): Slack/email delivery after commit, retries, retention`.

---

### Task 5: Notifications, follow and mentionable API

**Files:**
- Create:
  - `backend/app/api/v1/notifications.py`
  - `backend/app/schemas/notification.py`
- Modify:
  - `backend/app/api/api.py`: `include_router(notifications.router, prefix="/notifications", tags=["notifications"])`
  - `backend/app/api/v1/cases.py`: follow endpoints
  - `backend/app/api/v1/users.py`: `GET /users/mentionable`, declared BEFORE any `/{user_id}` routes
- Test: `backend/tests/test_notifications_api.py`

**Interfaces:**
- Consumes: `follow`, `unfollow`, `is_following` (Task 3).
- Produces these HTTP routes:
  - `GET /api/v1/notifications/?unread_only=false&limit=50&before_id=` returns `[NotificationOut]`.
    - `NotificationOut` is `{id, type, summary, case_id, case_title, actor_name, timeline_event_id, read_at, created_at}`.
    - `actor_name` is `null` for automation.
    - `limit` is between 1 and 100.
  - `GET /api/v1/notifications/unread-count` returns `{count: int}`.
  - `POST /api/v1/notifications/{id}/read` returns 204, or 404 if the notification is not the current user's in the active tenant.
  - `POST /api/v1/notifications/read-all` returns `{updated: int}`.
  - `GET|PUT|DELETE /api/v1/cases/{id}/follow`:
    - GET returns `{following: bool}`; PUT and DELETE return 204.
    - The case must be in the active tenant, otherwise 404.
    - Dependency: `get_current_active_user`. Viewers are allowed.
  - `GET /api/v1/users/mentionable?q=` returns up to 10 `{id, name, email}`.
    - Matches active-tenant members whose `full_name` or `email` starts with `q`, case-insensitively, with `%` and `_` escaped.
    - Requires `require_analyst_or_above`.

- [ ] **Step 1: Failing tests** (pattern: `tests/test_secrets_api.py`, i.e. `dependency_overrides` for `get_current_active_user`, `get_effective_tenant_id` and `require_analyst_or_above`, with an `httpx.ASGITransport` client):
  - **(a) List:** seed 3 notifications for U in T, 1 for U in tenant X, and 1 for user W in T. The list returns only the 3, newest first.
    - `before_id` pages correctly.
    - `unread_only` filters.
    - `case_title` and `actor_name` are filled in.
  - **(b) Unread count and read:** `unread-count` is 3. `POST /{id}/read` makes it 2. Reading W's id or the tenant-X id returns 404.
  - **(c) Read all:** `read-all` returns `{updated: 2}`, and the count is then 0.
  - **(d) Follow:**
    - GET returns false, then PUT gives 204, then GET returns true.
    - PUT again gives 204, still followed once (row count is 1).
    - DELETE gives 204 and GET returns false.
    - A tenant-X case returns 404 on all three methods.
    - A viewer can PUT.
  - **(e) Mentionable:**
    - `q="al"` returns members of T only (a tenant-X user with the same prefix is absent).
    - The limit is 10.
    - `q="%"` matches nothing special: it is escaped.
    - A viewer gets 403.
  - Clean up by ids.

- [ ] **Step 2: Implement.**
  - Notifications router: every query filters `Notification.user_id == current_user.id, Notification.tenant_id == tenant_id`.
  - List: join `Case` for the title and outer-join `User` (on `actor_id`) for the name, as `full_name` or `email`.
  - `read-all`: `update(...).where(user, tenant, read_at.is_(None)).values(read_at=now)`, returning rowcount.
  - Follow endpoints: load the case with `Case.tenant_id == tenant_id` (404 if missing), call the service, then `await db.commit()`.

- [ ] **Step 3: Run** the new file and the full suite, restart, then **commit** as `feat(notifications): notifications, follow and mentionable API`.

---

### Task 6: Frontend — bell, panel, notifications page

**Files:**
- Create:
  - `frontend/src/features/notifications/NotificationBell.tsx`
  - `frontend/src/features/notifications/NotificationsPage.tsx`
  - `frontend/src/features/notifications/api.ts`
- Modify:
  - `frontend/src/components/layout/Layout.tsx`: render `<NotificationBell/>` in the `<header>` (about line 185), right-aligned.
  - The router file where `/cases/:id` is declared (`grep -rn "cases/:id" frontend/src`): add `/notifications`.
  - `frontend/src/types/index.ts`: add the `AppNotification` type.

**Interfaces:**
- Consumes: the Task 5 routes.
- Produces:
  - `useUnreadCount()`
  - Query keys `['notifications','unread']` and `['notifications','list',{unreadOnly}]`
  - The `#event-<id>` URL hash convention, which Task 7's timeline consumes.

Behaviour:
- **Unread count:** `useQuery(['notifications','unread'], …, { refetchInterval: 30000, refetchOnWindowFocus: true })`. The `activeTenantId` from `/users/me` is part of the key, so a tenant switch refetches.
- **Bell:** a lucide `Bell` icon with a mono badge showing the count, or `99+` above 99. The badge is hidden at 0.
- **Panel:**
  - A dropdown anchored to the bell that closes on outside click or Esc. It is not a Modal.
  - It shows the latest 20 notifications (`limit=20`).
  - Each row has a type icon (`AtSign` mention, `UserPlus` assigned, `MessageSquare` comment, `Activity` status, `AlertTriangle` severity, `Clock` sla), the summary, `#case_id · case_title` truncated, a relative time, and an unread dot.
  - **On row click:**
    1. `POST /{id}/read`.
    2. Invalidate both query keys.
    3. Navigate to `/cases/{case_id}`, adding `#event-{timeline_event_id}` when it is set.
  - **Header:** "Mark all read" (calls `read-all`, then invalidates) and a "View all" link to `/notifications`.
  - **Empty state:** "You're all caught up".
- **`/notifications` page:**
  - Uses `PageContainer`, with All and Unread tabs.
  - Has the same rows plus a "Load more" button that passes `before_id` = the last id, and hides when a page comes back with fewer than 50.
- **Accessibility:**
  - The bell button has `aria-label="Notifications, N unread"`.
  - The panel has `role="menu"` and the rows have `role="menuitem"`.
  - Keyboard: Tab, Enter and Esc.

- [ ] **Step 1: Implement.**
- [ ] **Step 2: Verify.**
  - `cd frontend && npx tsc --noEmit && npm run build`, and eslint on the touched folders.
  - Rebuild: `docker compose build frontend && docker compose up -d --no-deps frontend`.
  - Check that `curl -s -o /dev/null -w '%{http_code}' http://localhost/` returns 200.
- [ ] **Step 3: Commit** as `feat(notifications): bell, panel and notifications page`.

---

### Task 7: Frontend — follow toggle, mention autocomplete, mention rendering

**Files:**
- Create:
  - `frontend/src/features/cases/MentionTextarea.tsx`
  - `frontend/src/features/cases/MentionText.tsx`
  - `frontend/src/features/cases/FollowButton.tsx`
- Modify: `frontend/src/features/cases/CaseDetail.tsx`:
  - Put `FollowButton` in the case header.
  - Use `MentionTextarea` for the new-comment and edit-comment textareas when `event_type === 'comment'`.
  - Use `MentionText` to render comment content.
  - Scroll to `#event-<id>` on load.

**Interfaces:**
- Consumes: `GET|PUT|DELETE /cases/{id}/follow`, `GET /users/mentionable?q=`, and the `#event-<id>` hash.
- Produces:
  - `MentionTextarea` with props `{value, onChange, disabled?, placeholder?}`. `onChange(serialized)` always reports the serialised text, with tokens.
  - `MentionText` with props `{content: string}`.

**MentionTextarea:**
- **Display:** the visible textarea shows `@Name`. Selected mentions are tracked internally as `[{name, id}]`.
- **Serialising** happens on every change. Each tracked `@Name` that is still present is replaced, left to right, by `@[Name](user:id)`. Untracked `@text` stays plain.
- **Loading:** when `value` already contains tokens (e.g. editing an existing comment), parse them with the same regex as the backend: `/@\[([^\]\n]{1,100})\]\(user:(\d{1,10})\)/g`. Show `@Name` and seed the tracked list.
- **Autocomplete:**
  - It opens when the caret follows `@` plus 0–30 characters with no whitespace.
  - Fetch: `GET /users/mentionable?q=`, debounced 150ms and enabled only for analysts and above.
  - Keys: Up, Down, Enter and Tab to pick; Esc to close.
  - The listbox uses `role="listbox"` and `aria-activedescendant`.
  - Picking replaces `@<query>` with `@Name ` and tracks it.
- **Viewers** can't comment, so the textarea is disabled as it is today.

**MentionText:**
- Split `content` by the regex.
- Render plain text segments as-is, and render each token as `<span className="text-accent font-medium bg-accent/10 px-0.5">@{name}</span>`.
- Build only React text nodes. Never use `dangerouslySetInnerHTML`.

**FollowButton:**
- Uses query key `['case-follow', id]`.
- Shows "Follow" with an outline style, or "Following" filled, with a hover label "Unfollow".
- PUT or DELETE, then invalidate.

**Scroll:** each timeline item gets `id={"event-" + event.id}`. After the case data loads, if `location.hash` matches, call `document.getElementById(...)?.scrollIntoView({block:'center'})` and add a 2s highlight ring.

**Existing `confirm(...)`:** `CaseDetail.tsx` already has a `confirm('Delete this timeline event?')` at about line 435. Leave it unless you touch that block. If you do touch it, replace it with the shared Modal.

- [ ] **Step 1: Implement.**
- [ ] **Step 2: Verify** the same way as Task 6 (tsc, build, eslint, rebuild, `/` returns 200).
- [ ] **Step 3: Commit** as `feat(notifications): follow toggle, @mention autocomplete and rendering`.

---

### Task 8: Docs and end-to-end check

**Docs**
- Add a "Notifications and @mentions" section to `docs/configuration.md` covering:
  - which events notify whom;
  - delivery rules: Slack DM first, then email; `FRONTEND_URL` for links; the 5-minute per-case limit; no comment text sent;
  - following rules;
  - retention (90 and 180 days);
  - the fact that the existing SLA breach email continues to be sent.
- Add a short entry to `docs/features.md`.

**End-to-end check in the container**
Run it as a one-off script under `/tmp` in the container, deleted afterwards and not committed.
1. Create a temporary tenant (`wf-test-` slug) with users A, B and C and their memberships, and a case owned by A.
2. A comments mentioning B, then commit.
3. Show that B has a `mention` notification and follows the case. Show that `deliver_notifications_task.delay` was called, by monkeypatching it in-process to record the call.
4. Change the status as A. B gets `status_change`.
5. Assign the case to C through `apply_case_update(user_id=None)`. C gets `assigned`.
6. Clean up by exact ids and confirm the count of leftover tenants is 0.
7. Record the results in the report.

**Commit** as `docs(notifications): notifications and mentions configuration`.
