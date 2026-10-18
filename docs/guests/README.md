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
- **Reset 2FA (admins).** Shown for guests with an account. It clears the guest's TOTP and signs out all their
  sessions; it is audit-logged as `guest_mfa_reset`. If the account also has a grant in another organization,
  the reset is refused (409, "This guest account is shared with another organization"): one tenant's admin
  can't weaken another tenant's guest login.

A guest shows as inactive (revoked / expired / blocked) when the grant is revoked or expired, the email domain
is no longer allowlisted, or the organization is inactive. The Guests tab is hidden for viewers.

## Guest sign-in

There are three ways in, and they all produce the same guest session:

1. **Magic link** from the invite or digest email. Opening it starts a session for that organization's cases.
   Link sessions are per organization: the session carries the tenant of the grant that minted it
   (`link_tenant_id`), so a forwarded invite never opens the same email's cases in another organization.
2. **Guest account (password + optional TOTP).** Account creation needs email confirmation. From a link
   session, "Set password" (`POST /guest/auth/register`) only emails the session's address a confirmation link
   (`/guest/register#<token>`); it always returns 202 and sends nothing if an account already exists. The
   password is chosen on that page (`POST /guest/auth/register/confirm`), which creates the account, links every
   grant for that email and signs in. A forwarded invite therefore can't claim the email's guest identity. Forgot and reset work at `/guest/forgot`. Once an account exists, magic links refuse to sign in
   and send the guest to the password login instead. Password and MFA sign-ins are audit-logged
   (`guest_password_login`, `guest_mfa_login`).
   - **Security panel.** Password sessions see a *Security* panel in the portal: set up TOTP (QR code or key),
     then turn it on with the current password and a code. Turning it off also needs password and code. Both
     sign out other sessions. Requiring the password on enable means a stolen session can't lock the owner out.
3. **SSO.** `/guest/login?t=<tenant-slug>` shows "Sign in with SSO". Emails to account holders only add
   `?t=<slug>` when the tenant's SSO is configured and enabled; otherwise they link to plain `/guest/login`.

| Token (`purpose` claim) | Lifetime |
|---|---|
| `guest-link` (magic link) | 7 days |
| `guest` (session) | 12 hours |
| `guest-reset` | 30 minutes, single use (bumps `token_version`) |
| `guest-register` (confirm email, create account) | 30 minutes, single use (refused once the account exists) |
| `guest-mfa` (TOTP challenge) | 5 minutes |

Emailed single-use tokens travel in the URL fragment (`/guest/link#<token>`, `/guest/reset#<token>`,
`/guest/register#<token>`), so they never reach the server or its access logs. The page reads the token and
clears it from the address bar with `history.replaceState` before calling the API. Older `/guest/link/<token>`
and `/guest/reset/<token>` links still work.

**SAML assertions are single-use.** After a valid response, the ACS records the assertion ID in Redis
(`SET saml_aid:{tenant_id}:{id} NX`, expiring at the assertion's `NotOnOrAfter` + the SAML library's 300s clock-drift allowance + 60s, minimum 6 minutes, maximum 24h (24h when the assertion has no `NotOnOrAfter`),
10 minutes if absent). A replayed assertion, one without an ID, or Redis being unavailable fails with the
generic SSO error. The check runs before the guest/analyst split, so it applies to analyst SSO too.

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
  uploads, with the same `MAX_UPLOAD_MB` size cap (nginx and backend).
- **Upload limit:** 50 uploads per guest grant per 24 hours (Redis counter `gup:{grant_id}`, fixed window from
  the first upload; failed attempts count). Past that, uploads return **429** "Upload limit reached. Try again
  later." Like the login throttle, it fails open if Redis is down. Upload audit rows carry
  `actor: guest:<email>`.
- Login and forgot never reveal whether an account exists, and there is no tenant or grant discovery endpoint.

Code: `backend/app/guests/`, `backend/app/api/v1/{case_guests,guest_auth,guest_portal}.py`,
`backend/app/tasks/guest_digest.py`, `frontend/src/features/{guests,guest-portal}/`.
