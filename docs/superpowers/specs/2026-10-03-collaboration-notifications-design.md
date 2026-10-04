# Collaboration: @mentions and notifications — design

Status: approved in conversation 2026-10-03 (choices B, C, B, A, approach 1).

## Goal

Analysts can pull teammates into a case with `@mentions` in comments, and hear about activity on
cases they own or follow — in-app for everything, plus a Slack DM (or email fallback) for the
moments that need attention right away (mentions and assignments).

## Decisions

| Topic | Choice |
|---|---|
| Scope | Mentions + case activity (assignment, status/severity change, new comment, SLA breach) |
| Delivery | In-app for all; mentions and assignments also go out by Slack DM when the tenant has Slack connected, else email when SMTP is configured |
| Following | Owner + explicit followers; commenting or being mentioned auto-follows; anyone can unfollow |
| Mentions | Case comments only; autocomplete of active-tenant members; stored as user-id tokens |
| Mechanism | `notifications` table written in the same transaction; Celery task for external delivery; frontend polls every 30s |

## Data model (migration `d8e9f0a1b2c3`, down_revision `c7d8e9f0a1b2`)

`notifications`
- `id` PK; `tenant_id` FK tenants (CASCADE), `user_id` FK users (CASCADE) — recipient;
  `case_id` FK cases (CASCADE); `type` String(32) — one of `mention`, `assigned`, `comment`,
  `status_change`, `severity_change`, `sla_breach`; `actor_id` FK users (SET NULL, nullable — null for
  automations/SLA); `timeline_event_id` FK timeline_events (SET NULL, nullable);
  `summary` String(200) plain text; `read_at` timestamptz nullable; `created_at` server default now.
- Index `ix_notifications_user_unread (user_id, tenant_id, read_at)`; index on `created_at`.

`case_followers`
- `id` PK; `case_id` FK cases (CASCADE); `user_id` FK users (CASCADE); `tenant_id` FK tenants
  (CASCADE); `created_at`. Unique `uq_case_follower (case_id, user_id)`.

Tenant deletion (`app/utils/tenant_deletion.py`) must delete both tables' rows for the tenant.

## Mentions

- Token format stored in comment content: `@[Display Name](user:42)`. Regex
  `@\[([^\]\n]{1,100})\]\(user:(\d{1,10})\)`.
- On save, each id is validated: the user must have a membership in the case's tenant. Valid ids
  become recipients; invalid tokens are left in the text and render as plain text (never notified).
- Display: the timeline renders valid-looking tokens as highlighted `@Display Name` spans built as
  React text nodes (no `dangerouslySetInnerHTML`). The display name shown is the one in the token.
- Editing a comment (`PUT /cases/{id}/timeline/{event_id}`): only user ids not present in the previous
  version are notified.
- Mentions only in `event_type == "comment"` timeline events.

## Triggers

A new module `app/notifications/service.py` exposes functions called from
`app/services/case_service.py` (the single path used by the API, copilot actions and workflow
nodes) and from `app/tasks/sla.py`, inside the caller's transaction:

- `on_comment(db, case, event, actor_id, previous_content=None)`:
  - mentioned = validated new mention ids.
  - Actor and mentioned users become followers (idempotent).
  - Each mentioned user (≠ actor) gets `mention`.
  - Every other follower (≠ actor, not mentioned) gets `comment`.
- `on_case_update(db, case, changes, actor_id)` (from `apply_case_update`):
  - `status` change → `status_change` to followers; `severity` change → `severity_change` to followers.
  - `owner_id` change to a non-null user → `assigned` to the new owner, who becomes a follower.
    Followers do not get a separate notification for reassignment.
- `on_case_created(db, case, actor_id)`: owner set → owner follows; `assigned` to owner if ≠ actor.
- `on_sla_breach(db, case, breach_type)`: `sla_breach` to owner and followers (actor null). The existing
  breach email to owner and admins is unchanged.

Rules:
- The actor never receives a notification for their own action.
- A recipient must currently hold a membership in the case's tenant; others are skipped silently.
- Viewers can follow and be notified.
- Summaries are plain text built from fixed templates, the actor's display name and the case number,
  e.g. `Alice mentioned you on case #123`, `Status changed to Contained on case #123`. Comment text is
  never copied into a notification.
- Failure isolation: notification writes run inside `db.begin_nested()`; any exception rolls back
  only the savepoint and logs one line `notification write failed case=%s type=%s err=%s`
  (error class only). The triggering action still commits.

## External delivery

- Only `mention` and `assigned` notifications are delivered externally.
- After commit, the request (or task) enqueues `deliver_notifications_task(notification_ids)` (Celery;
  ids only).
- Per notification:
  1. Rate limit: Redis `SET notif:ext:{user_id}:{case_id} 1 NX EX 300`; if the key exists, skip.
  2. If the tenant has an active Slack integration: `users.lookupByEmail` with the recipient's email,
     then `chat.postMessage` to the user's DM channel. Text is the summary plus the case title
     (truncated to 120 chars, Slack-escaped with the existing `slack_escape`) plus a link
     `{FRONTEND_URL}/cases/{case_id}` when `FRONTEND_URL` is set.
  3. Otherwise, or if the Slack lookup or post fails with a non-retryable error such as
     `users_not_found`: email via `email_service` if SMTP is configured (subject = summary, body = summary +
     title + link). No comment text is ever sent.
- Retries: transient errors (network, Slack rate limit, 5xx) retry up to 3 times with exponential
  backoff (`countdown = 30 * 2**attempt`); then log `notification delivery failed id=%s reason=%s`.
- Logs never contain message text, comment text or email addresses.

## Retention

Daily beat task `prune_notifications`: delete read notifications older than 90 days and any
notification older than 180 days (batched by id).

## API

`/api/v1/notifications` (any authenticated member; always `user_id = current user` and
`tenant_id = active tenant`):
- `GET /?unread_only=false&limit=50&before_id=` → `[{id, type, summary, case_id, case_title,
  actor_name, timeline_event_id, read_at, created_at}]` newest first; `limit` ≤ 100.
- `GET /unread-count` → `{count}`.
- `POST /{id}/read` → 204; another user's or tenant's notification → 404.
- `POST /read-all` → `{updated}`.

Cases router:
- `GET /cases/{id}/follow` → `{following: bool}`.
- `PUT /cases/{id}/follow` → 204, idempotent.
- `DELETE /cases/{id}/follow` → 204, idempotent.
- All three require tenant read access; a cross-tenant case returns 404.

Users router:
- `GET /users/mentionable?q=` → up to 10 `{id, name, email}` of active-tenant members whose name or
  email starts with `q` (case-insensitive). Analyst or above.

## Frontend

- Top-bar bell: unread badge (`/unread-count`, React Query `refetchInterval: 30000`, refetch on
  tenant switch and window focus). Clicking it opens a panel listing the latest 20, each with a type
  icon, summary, case number and relative time. Unread items show a dot.
  - Clicking an item marks it read and navigates to `/cases/{id}`, with `#event-{timeline_event_id}`
    when set; the timeline scrolls to that event.
  - The panel has a "Mark all read" button and a "View all" link.
- `/notifications` page: All / Unread tabs, "Load more" via `before_id`.
- Case header: Follow / Following toggle.
- Comment composer: typing `@` opens an autocomplete (`/users/mentionable`), navigable with arrows,
  Enter and Esc. A selected user shows as `@Name` in the textarea and is serialised to the token on
  submit; free-typed `@text` stays plain.
- Timeline renders mention tokens as highlighted spans.
- Light "Telemetry Console" theme. No `dangerouslySetInnerHTML`, no `window.confirm`/`alert`.

## Error handling summary

- Notification failure never blocks the triggering action (savepoint plus log).
- External delivery is asynchronous, retried, and rate-limited.
- Invalid or foreign mentions are inert text.
- Every query is scoped by user and active tenant; cross-tenant access returns 404.

## Testing

- **Unit:** mention parse/validate (valid, foreign tenant, malformed, duplicate, edited-new-only);
  recipient computation table (actor excluded, mention/comment dedupe, automation actor null,
  removed member skipped).
- **Service integration via `case_service`:** comment, status change, severity change,
  assignment by API and by workflow node, case created with owner, SLA breach.
- **API:** list/paging, unread count, read, read-all, follow/unfollow, mentionable, cross-tenant 404,
  viewer can follow.
- **Delivery task (Slack and SMTP mocked):** Slack first; email fallback on `users_not_found`; no
  Slack → email; neither → no-op; rate limit; retry on transient; payloads never contain comment text.
- **Failure isolation:** a forced exception in notification writes still commits the comment.
- **Hygiene:** cleanup by exact ids only.

## Out of scope

Real-time push (SSE/WebSocket), per-user channel preferences, digests, mentions outside comments,
notifications for tasks, artifacts, evidence or reports.
