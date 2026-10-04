# User profile, password change, avatar and TOTP MFA — design

Status: approved in conversation on 2026-10-03.
- MFA is optional per user, and a tenant can require it (choice B).
- Lost-device recovery is by admin reset only (choice C).
- Other sessions are revoked on security changes (choice A).
- Profile fields are name, title, timezone and photo (choice B).
- Login is a two-step flow (approach 1).

## Goal

Every user gets a profile page. From it they can:
- edit their name, job title and timezone;
- upload a photo;
- change their password, with the current password required;
- turn on authenticator-app (TOTP) two-factor authentication.

Tenant admins can require MFA for their tenant and can reset a member's MFA. Security-relevant changes sign out the user's other sessions.

## Decisions

| Topic | Choice |
|---|---|
| MFA policy | Optional per user. A tenant admin can require it; members without it must enroll at their next login. |
| Recovery | A tenant admin (for their own members) or a super admin resets MFA. Every reset is audited. No backup codes. |
| Sessions | `users.token_version` is embedded in every token. Password change and MFA enable, disable and reset bump it, which kills all other sessions. Deactivation also bumps it. |
| Profile fields | `full_name`, `job_title`, `timezone` and avatar. Email stays admin-managed. |
| Login | Two steps: the password step returns an MFA challenge, and `/auth/login/mfa` exchanges the challenge plus a code for the real token. |
| SSO | SAML logins skip SOC Hub MFA, because the IdP is responsible for it. SSO-only users can't change a password here. |

## Data model (migration `e9f0a1b2c3d4`, down_revision `d8e9f0a1b2c3`)

`users`:
- `job_title` String(100), nullable.
- `timezone` String(64), nullable. It must be a valid `zoneinfo` key.
- `avatar_key` String(255), nullable. This is the storage key.
- `token_version` Integer, not null, server_default 0.
- `mfa_secret_enc` Text, nullable. This is the Fernet-encrypted base32 secret. It is set during setup, while MFA is still pending.
- `mfa_enabled_at` timestamptz, nullable. MFA counts as enabled only when this is non-null.

`tenants`:
- `require_mfa` Boolean, not null, server_default false.

## Tokens and sessions

- A single helper, `issue_access_token(user, active_tenant_id) -> str`, is used by every token issuer: login, switch-tenant, SSO ACS, the MFA step and the security changes. It adds `"tv": user.token_version` to the payload.
- `get_current_user` rejects a token when:
  - `payload.get("tv", 0) != user.token_version`, or
  - `payload.get("purpose")` is present.
- MFA challenge token: a JWT with `{"sub", "purpose": "mfa", "tv", "exp": now + 5 min}`. It is accepted only by:
  - `/auth/login/mfa`;
  - the setup and enable endpoints, while in forced-setup mode (see below).
- When the current user changes their own password or MFA state, the response includes `access_token`, a fresh token, so the current session continues. The frontend swaps it in.

## TOTP

- Implemented in `app/core/totp.py` with the stdlib only (`hmac`, `hashlib`, `struct`, `base64`, `secrets`). RFC 6238 parameters: SHA-1, 6 digits, 30 s step, ±1 step tolerance.
- The secret is 20 random bytes, base32-encoded.
- Provisioning URI: `otpauth://totp/SOC%20Hub:{email}?secret=…&issuer=SOC%20Hub`.
- Codes are compared with `hmac.compare_digest`.
- Replay protection: an accepted step is stored in Redis as `SET mfa:used:{user_id}:{step} 1 NX EX 90`. Reusing it is rejected.
- Brute force: code attempts reuse `LoginThrottle` keyed by `mfa:{user_id}` and IP. 5 failures per 15 minutes returns 429 with `Retry-After`.
- If `mfa_secret_enc` can't be decrypted, the MFA step fails with 409 "MFA unavailable — contact your admin". An admin reset is the fix.

## API

### Profile (`/api/v1/users/me`, any authenticated user)

- `PUT /users/me {full_name?, job_title?, timezone?}`: the `password` field is removed. An invalid timezone returns 422. `UserMe` gains `job_title`, `timezone`, `has_avatar`, `mfa_enabled` and `has_password`.
- `POST /users/me/password {current_password, new_password}`:
  - The current password must verify, otherwise 400 "Current password is incorrect".
  - The new password goes through `validate_password_strength`.
  - It is throttled per user.
  - It bumps `token_version` and returns `{access_token}`.
  - It writes an audit entry `user.password_changed`.
- `PUT /users/me/avatar` (multipart `file`, max 2 MB):
  - Pillow opens the image with `Image.MAX_IMAGE_PIXELS = 40_000_000`. The format must be one of PNG, JPEG or WEBP.
  - The image is transposed according to EXIF, center-cropped to a square, resized to 256×256 and saved as WEBP with no metadata.
  - It is stored at key `avatars/{user_id}/{token_hex(8)}.webp`. The previous key is deleted.
  - Any failure returns 422 "Image must be PNG, JPEG or WebP up to 2 MB".
- `DELETE /users/me/avatar` → 204.
- `GET /users/{id}/avatar`:
  - Allowed when the requester shares at least one tenant membership with that user, or is a super admin. Otherwise 404. If there is no avatar, 404.
  - Response: `image/webp`, `Cache-Control: private, max-age=300`, `X-Content-Type-Options: nosniff`.

### MFA

- `POST /users/me/mfa/setup`: creates a new secret (overwriting any pending one) and returns `{otpauth_uri, secret}`. It returns 409 if MFA is already enabled.
- `POST /users/me/mfa/enable {code}`:
  - The code is verified against the pending secret.
  - On success it sets `mfa_enabled_at`, bumps `token_version`, writes the audit entry `user.mfa_enabled` and returns `{access_token}`.
- `POST /users/me/mfa/disable {current_password, code}`:
  - Both must verify.
  - It returns 403 if any tenant the user belongs to has `require_mfa`.
  - On success it clears the secret and `enabled_at`, bumps `token_version`, writes the audit entry and returns `{access_token}`.
- `POST /users/{id}/mfa/reset`:
  - Allowed for an admin of the active tenant where the target is a member of that tenant, or for a super admin. Otherwise 404.
  - It clears MFA, bumps the target's `token_version` and writes the audit entry `user.mfa_reset` with the actor.
- `PUT /tenants/current/security {require_mfa}`: tenant admin only, audited. It returns `{require_mfa, members_without_mfa}`. `GET` on the same path returns the same shape.
- Forced setup: `setup` and `enable` also accept the MFA challenge token, as a Bearer token, when the user has no MFA enabled. In that mode, `enable` returns the real login token for the default tenant.

### Login

`POST /auth/login/access-token` runs the password check (unchanged), then:
- If MFA is enabled → `{mfa_required: true, mfa_token}`.
- Else, if any of the user's tenants has `require_mfa` (super admins with no memberships are exempt) → `{mfa_setup_required: true, mfa_token}`.
- Else → the normal `{access_token, token_type}`.

The `Token` response schema becomes a union-compatible model with optional fields.

`POST /auth/login/mfa {mfa_token, code}`:
- It validates the challenge (purpose, expiry, `tv`, user active, MFA enabled).
- It throttles and verifies the code.
- It returns `{access_token, token_type}` for the default active tenant.
- A bad or expired code returns 400 "Invalid or expired code". A bad or expired challenge returns 401.

`POST /auth/switch-tenant`: when the target tenant has `require_mfa` and the user has no MFA (and isn't a super admin), it returns 403 `{detail: "mfa_setup_required"}`.

SSO ACS: unchanged, except that it uses `issue_access_token`.

`users.deactivate` bumps `token_version`.

## Frontend

- `/profile` page, reached from the sidebar user block (name and avatar). It has three cards:
  - **Profile:** avatar with upload, preview and remove; name; job title; a searchable timezone select built from `Intl.supportedValuesOf('timeZone')`; email shown read-only.
  - **Password:** current, new and confirm fields; policy hints; the new token is swapped in on success; the note "Other sessions were signed out". If `has_password` is false, it shows an SSO note instead.
  - **Two-factor:** shows the status. "Turn on" opens a Modal containing:
    - a QR code rendered client-side with the `qrcode` npm package to a canvas or data URL;
    - the secret in a mono font, for manual entry;
    - a 6-digit input and a Confirm button.

    "Turn off" asks for the password and a code. It is hidden, with an explanation, when the tenant requires MFA.
- Login:
  - After the password step, a `mfa_required` response switches to a code step: `inputMode="numeric"`, `autoComplete="one-time-code"`, 6 digits, plus Back.
  - `mfa_setup_required` shows the same setup UI as the profile page and then logs in.
  - An expired challenge returns the user to the password step with a message.
- Users admin list: an MFA badge per member, and a "Reset MFA" action behind a Modal confirm (admins only).
- Settings: a "Require two-factor authentication" toggle (tenant admin), showing `members_without_mfa`.
- `<Avatar user size>`:
  - Fetches `/users/{id}/avatar` with auth via the axios client into a blob object URL, caches it in React Query keyed by user id and `has_avatar`, and revokes the URL on unmount.
  - Falls back to initials.
  - Used in the sidebar, timeline entries, mention autocomplete and the notification panel.
- `formatDateTime(date)` uses the profile timezone or the browser default. It is applied to the profile page, notifications and timeline. Converting the rest of the app's dates is out of scope.
- No `confirm`, `alert` or `window.open`. The secret and codes are never logged or written to localStorage.

## Security notes

- Logs carry user ids and outcomes only. They never include codes, secrets, passwords or the otpauth URI.
- The secret is returned only by `setup`, before MFA is enabled. Nothing ever returns `mfa_secret_enc`.
- Avatar re-encoding drops all metadata, and the original upload is never stored.
- `token_version` checks add no extra query, because the user row is already loaded in `get_current_user`.

## Testing

- **TOTP unit tests:** RFC 6238 Appendix B SHA-1 vectors, using the generator's `digits=8` parameter. Also cover the ±1 window and replay rejection.
- **Tokens:**
  - A `tv` mismatch → 401.
  - A purpose token is rejected by normal endpoints.
  - All issuers include `tv`.
- **Profile:**
  - Profile update and timezone validation.
  - A password change requires the current password, the old token stops working, and the new token works.
  - SSO-only users get 400 on the password endpoint.
- **MFA:**
  - setup → enable with a valid code;
  - enable with a wrong code → 400;
  - disable requires password and code, and is blocked when the tenant requires MFA;
  - admin reset: same-tenant admin is OK, a cross-tenant admin gets 404, an analyst gets 403 or 404, a super admin is OK, and the target's old token is revoked.
- **Login:**
  - Each branch: normal, MFA required, setup required.
  - `/login/mfa` success.
  - Replay of the same code → 400.
  - Throttle → 429.
  - Expired challenge → 401.
  - Forced setup issues the real token after enable.
  - Switch-tenant into a tenant requiring MFA without MFA enabled → 403.
- **Avatar:**
  - Valid PNG and JPEG are re-encoded to 256×256 WEBP without EXIF.
  - A non-image renamed `.png` → 422.
  - More than 2 MB → 422.
  - A decompression bomb → 422.
  - Fetching from another tenant → 404.
  - Replacing deletes the old key.
- **Frontend:** tsc, build, eslint, container rebuild.
- **End-to-end in the container:** enable MFA, run the two-step login, admin reset, and confirm the old token fails. Cleanup by exact ids.

## Out of scope

Backup codes, WebAuthn and passkeys, self-service email change, a session list UI, converting all app dates to the profile timezone, and enforcing MFA for SSO logins.
