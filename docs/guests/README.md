# Case guests

Invite people outside the SOC (HR, Legal, another department) into **one case** to follow updates or submit
evidence, without giving them tenant membership. Guests are a separate principal: no `User` row, no
membership, and a dedicated `/api/v1/guest/*` API.

## What guests can and can't see

| Guests see | Guests never see |
|---|---|
| Case title, status, severity, created/updated | `description` |
| `guest_summary` (analyst-written) | Artifacts, IOCs, tasks, copilot |
| Timeline entries an analyst marked **Shared** | Unshared entries, `guest_invited` entries |
| Their own comments (author "You") | Other guests or their comments |
| Their own uploads | Analyst attachments or other guests' uploads |

Permissions are per grant: **viewer** (read only) or **contributor** (comment and upload evidence).

## Admin setup

In **Settings**, use the guest access section (admins only):

- **Domain allowlist.** Guests can only be invited from these email domains. An empty list disables guests for
  the tenant. Removing a domain cuts off existing guests from that domain immediately.
- **Require SSO for guests.** Guests must sign in through the tenant's SAML IdP. This option is only available
  once tenant SSO is configured. **The IdP needs no changes.** Guest sign-in uses the same ACS URL, marked with
  `RelayState=guest`, and never creates or provisions a `User`.

## Analyst workflow

Analysts and admins use the **Guests** tab on a case:

- **Invite.** Enter an email, Viewer/Contributor and an expiry (30 days by default). The guest gets an email
  with a magic link, or "sign in with your company SSO" when SSO is required. Inviting an email that is already
  on the case reactivates and updates its grant.
- **Change permission, Extend, Resend, Revoke.** Every action is audit-logged. Revoking or expiring a grant
  stops access at once.
- **Guest summary.** A short text written for guests. Guests never see `description`.
- **Share entries.** Use the eye toggle on a timeline entry. Guest-authored entries and uploads carry an amber
  **Guest** chip. Guest uploads are labelled *untrusted origin*.

## Guest sign-in

There are three ways in, and they all produce the same guest session:

1. **Magic link** from the invite or digest email. Opening it starts a session for that grant.
2. **Guest account (password + optional TOTP).** A link session can set a password. Forgot and reset work at
   `/guest/forgot`. Once an account exists, magic links refuse to sign in and send the guest to the password
   login instead.
3. **SSO.** `/guest/login?t=<tenant-slug>` shows "Sign in with SSO".

| Token (`purpose` claim) | Lifetime |
|---|---|
| `guest-link` (magic link) | 7 days |
| `guest` (session) | 12 hours |
| `guest-reset` | 30 minutes, single use (bumps `token_version`) |
| `guest-mfa` (TOTP challenge) | 5 minutes |

## Email digests

Guests get an email when an entry is shared with them or the case status changes. Updates are batched: the
first update starts a **5-minute window**, and everything queued in that window goes out as one email per
guest (Redis `SET NX` plus a Celery `countdown`). Emails contain short summaries only, never the shared
content. A grant revoked before the email goes out gets no email.

## Isolation guarantees

- Every guest token carries a `purpose` claim. `deps.get_current_user` rejects any token with `purpose`, so a
  guest token gets **401 on every analyst endpoint**.
- Every guest request re-checks the grant: not revoked, not expired, tenant active, email domain still
  allowlisted and, when SSO is required, an SSO session from that same tenant. Anything not granted returns
  **404**.
- Viewers get **403** on write routes. Uploads go through the same size and content validation as analyst
  uploads.
- Login and forgot never reveal whether an account exists, and there is no tenant or grant discovery endpoint.

Code: `backend/app/guests/`, `backend/app/api/v1/{case_guests,guest_auth,guest_portal}.py`,
`backend/app/tasks/guest_digest.py`, `frontend/src/features/{guests,guest-portal}/`.
