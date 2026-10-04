# User Profile, Password Change, Avatar and TOTP MFA Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A profile page where users can edit their name, title, timezone and photo, change their password, and set up authenticator-app MFA. Tenants can require MFA. Admins can reset it. Security changes revoke the user's other sessions.

**Architecture:**
- `users.token_version` is embedded in every JWT through one `issue_access_token` helper. `get_current_user` rejects tokens whose version doesn't match.
- TOTP uses only the standard library (`app/core/totp.py`).
- Login becomes two-step. The first step returns a 5-minute challenge JWT (`purpose: "mfa"`) that only `/auth/login/mfa` and the MFA setup/enable endpoints accept.
- Avatars are re-encoded by Pillow and stored in the existing storage backend.

**Tech Stack:** FastAPI, async SQLAlchemy, Alembic, PyJWT, Pillow (already installed, 12.3), Redis, React 19, TanStack Query, `qrcode` npm package (new).

**Spec:** `docs/superpowers/specs/2026-10-03-user-profile-mfa-design.md`

## Global Constraints

**Database and tokens**
- Migration revision `e9f0a1b2c3d4`, down_revision `d8e9f0a1b2c3`.
- The JWT claim for the token version is `"tv"`. The challenge token has `"purpose": "mfa"` and expires after 5 minutes.
- Every access token is created through `app.core.security.issue_access_token(user, active_tenant_id)`. `create_access_token` is never called directly outside `security.py`.

**TOTP and throttling**
- TOTP settings: SHA-1, 6 digits, 30 s step, ±1 step window, 20-byte secret encoded as base32.
- Replay key: `mfa:used:{user_id}:{step}`, set with `NX EX 90`.
- MFA code attempts use `LoginThrottle.reserve(f"mfa:{user.id}", ip)`. With the defaults that allows 5 per (key, IP) per 900 s. On success call `throttle.reset(f"mfa:{user.id}", ip)`.

**Avatars**
- Maximum upload is 2 MB. `Image.MAX_IMAGE_PIXELS = 40_000_000`. Accepted formats are `PNG`, `JPEG` and `WEBP`. The output is a 256×256 WEBP with no metadata.
- Storage key: `avatars/{user_id}/{secrets.token_hex(8)}.webp`.

**Exact error and response texts**
- `"Current password is incorrect"` (400)
- `"Invalid or expired code"` (400)
- `"MFA unavailable — contact your admin"` (409)
- `"Image must be PNG, JPEG or WebP up to 2 MB"` (422)
- Tenant switch blocked: `{"detail": "mfa_setup_required"}` (403)

**Audit actions** (entity_type `user` or `tenant`): `password_changed`, `mfa_enabled`, `mfa_disabled`, `mfa_reset`, `avatar_changed`, and `require_mfa_changed` (entity_type `tenant`).

**Logging and storage hygiene**
- Logs carry only user ids and outcomes. Never log a code, secret, password, otpauth URI or email.
- The frontend never writes the secret or codes to localStorage or the console.
- Test cleanup is by exact ids only. Never run a broad DELETE on the live DB.
- Backend tests run in the container: `docker compose exec -T backend python -m pytest -q tests/<file>`.
- After backend changes, restart with `docker restart case_management-backend-1 case_management-worker-1`.

**Frontend**
- Never use `dangerouslySetInnerHTML`, `window.confirm`, `alert` or `window.open`.
- Use the light theme and `components/layout/Modal.tsx`.
- Rebuild with `docker compose up -d --no-deps --build frontend`.

## Review Focus

1. **An old token stops working right after a password change or admin MFA reset, but the token returned to the acting user still works.** Owned by Task 3 test `test_password_change_revokes_old_token` and Task 5 test `test_admin_reset_revokes_target`.
2. **A challenge token can't be used as an access token, and an access token can't be used as a challenge.** Task 1 test `test_purpose_token_rejected`, and Task 6 test `test_access_token_not_a_challenge`.
3. **The same code twice within its window is rejected (replay).** Task 6 test `test_code_replay_rejected`.
4. **A file renamed to `.png`, or a decompression bomb, returns 422 and never a 500.** Task 4 test `test_avatar_rejects_fake_and_bomb`.
5. **A user who belongs only to a tenant requiring MFA can't get a full token without enrolling; switching into such a tenant is blocked.** Task 6 tests `test_setup_required_branch` and `test_switch_tenant_requires_mfa`.

---

### Task 1: Columns, token versioning, single token issuer

**Files:**
- Create: `backend/alembic/versions/e9f0a1b2c3d4_user_profile_mfa.py`
- Modify:
  - `backend/app/models/user.py`
  - `backend/app/models/tenant.py`
  - `backend/app/core/security.py`
  - `backend/app/api/deps.py`
  - `backend/app/api/v1/auth.py` (lines ~91 and ~131)
  - `backend/app/api/v1/sso.py` (line ~173)
  - `backend/app/api/v1/users.py` (`deactivate_user`)
- Test: `backend/tests/test_token_version.py`

**Interfaces:**
- Produces:
  - `User` gets `job_title`, `timezone`, `avatar_key`, `token_version`, `mfa_secret_enc`, `mfa_enabled_at`; `Tenant` gets `require_mfa`.
  - `issue_access_token(user, active_tenant_id) -> str`
  - `issue_mfa_challenge(user) -> str`
  - `decode_mfa_challenge(token) -> dict` (raises `jwt.PyJWTError`)
  - `bump_token_version(user) -> None`

- [ ] **Step 1: Migration and model columns.** All new columns, exactly as listed in the spec "Data model" section.
  - `users.token_version` uses `server_default="0"`, nullable=False.
  - `tenants.require_mfa` uses `server_default=sa.false()`, nullable=False.
  - `downgrade()` drops them.

  Run `docker compose exec -T backend alembic upgrade head` and check that `alembic heads` returns `e9f0a1b2c3d4`.

- [ ] **Step 2: Failing tests** (`test_token_version.py`, via `httpx.ASGITransport` against `app` with no dependency overrides for auth; create a temporary tenant, user and membership and clean them up by id):
  - `test_issued_token_works`: `issue_access_token(user, tid)` → `GET /api/v1/users/me` returns 200.
  - `test_version_mismatch_rejected`: bump `user.token_version` in the DB, then the old token gets 401.
  - `test_purpose_token_rejected`: `issue_mfa_challenge(user)` used as a Bearer token on `/users/me` gets 401.
  - `test_tokens_carry_tv`: decoding `issue_access_token`'s output shows `tv == user.token_version`.
  - `test_deactivate_bumps_version`: an admin deactivates the user through the API, and the user's old token gets 401.

- [ ] **Step 3: Implement**

```python
# backend/app/core/security.py (additions)
MFA_CHALLENGE_MINUTES = 5


def issue_access_token(user, active_tenant_id) -> str:
    return create_access_token({"sub": user.email, "active_tenant_id": active_tenant_id,
                                "tv": user.token_version or 0})


def issue_mfa_challenge(user) -> str:
    return create_access_token({"sub": user.email, "purpose": "mfa", "tv": user.token_version or 0},
                               expires_delta=timedelta(minutes=MFA_CHALLENGE_MINUTES))


def decode_mfa_challenge(token: str) -> dict:
    payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
    if payload.get("purpose") != "mfa":
        raise jwt.InvalidTokenError("not an mfa challenge")
    return payload


def bump_token_version(user) -> None:
    user.token_version = (user.token_version or 0) + 1
```

In `deps.get_current_user`:
- After decode, raise `credentials_exception` if `payload.get("purpose")` is set.
- After loading the user, raise it if `payload.get("tv", 0) != (user.token_version or 0)`.

Replace the three `create_access_token(...)` call sites with `issue_access_token(user, <tenant id>)`. `deactivate_user` calls `bump_token_version(target)` before commit.

- [ ] **Step 4: Run** the new tests, then the full suite (existing tests mint tokens with the old helper or `dependency_overrides`; fix any tests that build tokens manually by switching them to `issue_access_token`). Restart backend and worker.

- [ ] **Step 5: Commit** `feat(auth): token versioning and a single access-token issuer`.

---

### Task 2: TOTP core

**Files:**
- Create: `backend/app/core/totp.py`
- Test: `backend/tests/test_totp.py`

**Interfaces:**
- Produces:
  - `new_secret() -> str`
  - `totp_at(secret_b32: str, for_time: int, digits: int = 6) -> str`
  - `matching_step(secret_b32, code, now=None) -> int | None`: returns the matched time step or None, using a ±1 window and constant-time comparison.
  - `provisioning_uri(secret_b32, email) -> str`
  - `async def consume_step(redis, user_id, step) -> bool`: returns True the first time a step is used and False on replay.

- [ ] **Step 1: Failing tests.**
  - RFC 6238 Appendix B SHA-1 vectors: the secret is ASCII `"12345678901234567890"` (base32 `GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ`) with `digits=8`:
    - T=59 → `94287082`
    - T=1111111109 → `07081804`
    - T=1111111111 → `14050471`
    - T=1234567890 → `89005924`
    - T=2000000000 → `69279037`
  - Window: a code for `now-30` and one for `now+30` match; `now±60` does not.
  - Non-digit or wrong-length codes return None.
  - `provisioning_uri` starts with `otpauth://totp/SOC%20Hub:` and contains `issuer=SOC%20Hub`.
  - `consume_step` against a fake redis whose `set(key, v, nx, ex)` returns True then None: the result is True, then False.

- [ ] **Step 2: Implement**

```python
# backend/app/core/totp.py
"""RFC 6238 TOTP (SHA-1, 30 s, ±1 step) with replay protection. Stdlib only."""
import base64
import hashlib
import hmac
import secrets
import struct
import time
from typing import Optional
from urllib.parse import quote

STEP, DIGITS, WINDOW = 30, 6, 1


def new_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _key(secret_b32: str) -> bytes:
    s = secret_b32.upper()
    return base64.b32decode(s + "=" * (-len(s) % 8))


def _hotp(key: bytes, counter: int, digits: int) -> str:
    h = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    o = h[-1] & 0x0F
    code = (struct.unpack(">I", h[o:o + 4])[0] & 0x7FFFFFFF) % (10 ** digits)
    return str(code).zfill(digits)


def totp_at(secret_b32: str, for_time: int, digits: int = DIGITS) -> str:
    return _hotp(_key(secret_b32), int(for_time) // STEP, digits)


def matching_step(secret_b32: str, code: str, now: Optional[float] = None) -> Optional[int]:
    code = (code or "").strip()
    if len(code) != DIGITS or not code.isdigit():
        return None
    key, step = _key(secret_b32), int((now if now is not None else time.time()) // STEP)
    hit = None
    for s in range(step - WINDOW, step + WINDOW + 1):
        if hmac.compare_digest(_hotp(key, s, DIGITS), code):
            hit = s  # no early return: same work for every code
    return hit


def provisioning_uri(secret_b32: str, email: str) -> str:
    return f"otpauth://totp/SOC%20Hub:{quote(email)}?secret={secret_b32}&issuer=SOC%20Hub"


async def consume_step(redis, user_id: int, step: int) -> bool:
    return bool(await redis.set(f"mfa:used:{user_id}:{step}", "1", nx=True, ex=90))
```

- [ ] **Step 3: Run the tests, then commit** `feat(auth): stdlib TOTP with replay protection`.

---

### Task 3: Profile and password API

**Files:**
- Modify:
  - `backend/app/schemas/user.py`: `UserUpdate` loses `password` and gains `job_title`/`timezone`; add `PasswordChange` and `TokenOnly`; `UserMe` gains `job_title`, `timezone`, `has_avatar`, `mfa_enabled`, `has_password`.
  - `backend/app/api/v1/users.py`
- Test: `backend/tests/test_profile_api.py`

**Interfaces:**
- Consumes: `issue_access_token`, `bump_token_version` (Task 1).
- Produces:
  - `PUT /users/me {full_name?, job_title?, timezone?}` → `UserMe`
  - `POST /users/me/password {current_password, new_password}` → `{access_token, token_type}`
  - The `UserMe` fields listed above.

**Rules**
- `timezone` must satisfy `zoneinfo.available_timezones()`, otherwise 422.
- `job_title` is at most 100 characters. `full_name` is 1–200 characters after strip.
- `has_password`: SSO users are created with a random unusable password. Look at how `sso.py` `_resolve_sso_user` creates users. If it marks them in an identifiable way, use that. If it does not, add `users.password_login_enabled` Boolean, default true, set false for SSO-created users. Report which option you chose.
- **Password change:**
  - Throttle with `LoginThrottle.reserve(f"pwd:{user.id}", ip)`.
  - On a bad current password return 400 `"Current password is incorrect"`.
  - On success: hash the new password (validated by `validate_password_strength`), call `bump_token_version`, write the audit log `password_changed`, commit, and return `issue_access_token(user, active)`.

- [ ] **Step 1: Failing tests** (real auth: use `issue_access_token`; no `get_current_user` override):
  - `test_update_profile_fields`: name, title and timezone are saved; `"Mars/Base"` gets 422.
  - `test_password_change_requires_current`: a wrong current password gets 400; a weak new password gets 422.
  - `test_password_change_revokes_old_token`:
    - The change succeeds.
    - The old token on `/users/me` gets 401.
    - The returned token gets 200.
    - The new password verifies.
    - The audit row exists, and its changes contain no password text.
  - `test_put_me_ignores_password`: sending `password` to `PUT /users/me` does not change the hash (the field is dropped or gets 422).
  - `test_sso_only_user_cannot_change_password`: 400.

- [ ] **Step 2: Implement. Step 3: Run and commit** `feat(profile): profile fields and current-password-checked password change`.

---

### Task 4: Avatar upload, serve, delete

**Files:**
- Create: `backend/app/services/avatars.py`
- Modify: `backend/app/api/v1/users.py`
- Test: `backend/tests/test_avatars.py`

**Interfaces:**
- Consumes: `app.storage.get_storage()`, which provides `put(key, chunks) -> int`, `open(key)` and `delete(key)`.
- Produces:
  - `reencode_avatar(data: bytes) -> bytes` (raises `ValueError`)
  - `PUT /users/me/avatar` (multipart `file`) → `UserMe`
  - `DELETE /users/me/avatar` → 204
  - `GET /users/{id}/avatar` → `image/webp`

```python
# backend/app/services/avatars.py
from io import BytesIO

from PIL import Image, ImageOps

MAX_BYTES = 2 * 1024 * 1024
Image.MAX_IMAGE_PIXELS = 40_000_000
_ALLOWED = {"PNG", "JPEG", "WEBP"}


def reencode_avatar(data: bytes) -> bytes:
    if not data or len(data) > MAX_BYTES:
        raise ValueError("size")
    try:
        with Image.open(BytesIO(data)) as im:
            if im.format not in _ALLOWED:
                raise ValueError("format")
            im = ImageOps.exif_transpose(im)
            im = ImageOps.fit(im.convert("RGB"), (256, 256), Image.LANCZOS)
            out = BytesIO()
            im.save(out, "WEBP", quality=85)  # fresh image: no EXIF/ICC carried over
            return out.getvalue()
    except (Image.DecompressionBombError, Image.DecompressionBombWarning, OSError, SyntaxError) as e:
        raise ValueError("decode") from e
```

**Endpoint rules**
- **Upload:**
  - Read at most `MAX_BYTES + 1` bytes from the upload, then re-encode in `asyncio.to_thread`.
  - Any `ValueError` returns 422 with the exact message.
  - Store at the new key, delete the old key (ignore errors), save `avatar_key`, audit `avatar_changed`, commit.
- **GET:**
  - The requester must be a super admin, or the requester and target must share a tenant membership (one query joining `TenantMembership` twice).
  - Otherwise, or when there is no avatar, return 404.
  - Stream the response with `Cache-Control: private, max-age=300` and `X-Content-Type-Options: nosniff`.
  - Make `Image.DecompressionBombWarning` raise: wrap the call in `warnings.catch_warnings()` with `simplefilter("error", Image.DecompressionBombWarning)`.

- [ ] **Step 1: Failing tests:**
  - `test_avatar_reencoded`: a 600×400 PNG and a JPEG carrying EXIF are both returned as WEBP 256×256 (check with `Image.open`), with no EXIF.
  - `test_avatar_rejects_fake_and_bomb`: these all get 422:
    - `b"not an image"` named `x.png`;
    - a GIF;
    - a 2 MB + 1 byte payload;
    - a bomb, built in-test as a PNG of 9000×9000 in mode "1" (1-bit, so about 10 MB in memory; compresses small; exceeds 80 MP, which makes Pillow raise `DecompressionBombError`). Also test an image of about 50 MP, which only triggers a warning that must be turned into a 422.
  - `test_avatar_access_scoped`: a same-tenant member gets 200; a user from another tenant gets 404; a super admin gets 200; a user with no avatar gets 404.
  - `test_avatar_replace_deletes_old`: monkeypatch the storage object's `delete` to record keys, upload twice, and check the first key was deleted.
  - `test_avatar_delete`: 204, then GET gets 404.

  Clean up storage keys and DB rows created by the test, by key and id.

- [ ] **Step 2: Implement. Step 3: Run and commit** `feat(profile): avatar upload with Pillow re-encode`.

---

### Task 5: MFA management API

**Files:**
- Create: `backend/app/api/v1/mfa.py` (routes are mounted under `/users`; register it in `api/api.py` BEFORE the users router so `/me/mfa/*` paths aren't shadowed)
- Modify:
  - `backend/app/api/v1/tenants.py` (or `sso.py`'s `sso_admin_router`, following the same pattern used for `/tenants/sso-config`): `GET`/`PUT /tenants/current/security`
  - `backend/app/api/deps.py`: add `get_user_for_mfa_setup`
- Test: `backend/tests/test_mfa_api.py`

**Interfaces:**
- Consumes: `totp.*` (Task 2); `issue_access_token`, `decode_mfa_challenge`, `bump_token_version` (Task 1); `encrypt`/`decrypt` from `app.utils.crypto`.
- Produces:
  - `POST /users/me/mfa/setup` → `{otpauth_uri, secret}`
  - `POST /users/me/mfa/enable {code}` → `{access_token, token_type}`
  - `POST /users/me/mfa/disable {current_password, code}` → `{access_token, token_type}`
  - `POST /users/{id}/mfa/reset` → 204
  - `GET`/`PUT /tenants/current/security` → `{require_mfa, members_without_mfa}`
  - `async def verify_user_code(db, redis, throttle, user, code, ip) -> None` (raises HTTPException 400, 409 or 429). Task 6 reuses it.
  - `deps.get_user_for_mfa_setup`: accepts EITHER a normal access token (through `get_current_active_user`'s logic) OR a challenge token for a user without MFA enabled. It returns `(user, via_challenge: bool)`.

**Rules**
- **setup:** 409 if MFA is already enabled. Store `encrypt(secret)` in `mfa_secret_enc`, keeping `mfa_enabled_at` None. Return the URI and the secret.
- **enable:**
  1. Load the pending secret (400 if there is none).
  2. `verify_user_code`.
  3. Set `mfa_enabled_at = now`, call `bump_token_version`, audit `mfa_enabled`, commit.
  4. Return a token for the active tenant. When `via_challenge`, use the default tenant from `auth._default_active_tenant_id`.
- **disable:**
  - 403 `"Your organization requires two-factor authentication"` if any membership tenant has `require_mfa`.
  - Verify the password (400 `"Current password is incorrect"`), then `verify_user_code`.
  - Clear both fields, bump, audit, commit, and return a new token.
- **reset:**
  - The actor must be a super admin, or an admin of the active tenant where the target has a membership in that tenant. Otherwise 404.
  - Clear both fields, bump the TARGET's version, audit `mfa_reset` (`user_id` = actor, `entity_id` = target), commit.
- **tenant security:**
  - Admin of the active tenant.
  - PUT sets `require_mfa` and audits `require_mfa_changed` on the tenant.
  - `members_without_mfa` = count of active members with `mfa_enabled_at IS NULL`.
- **`verify_user_code`:**
  1. Throttle `reserve(f"mfa:{user.id}", ip)` → 429 with `Retry-After`.
  2. Decrypt the secret; on failure return 409 `"MFA unavailable — contact your admin"`.
  3. `matching_step`; None → 400 `"Invalid or expired code"`.
  4. `consume_step`; False → 400 with the same text.
  5. On success, `throttle.reset(f"mfa:{user.id}", ip)`.
- Redis: use the same client factory the login throttle uses (`redis.asyncio.from_url(settings.REDIS_URL)`), exposed as a dependency so tests can override it with a fake.

- [ ] **Step 1: Failing tests** (fake redis and fake throttle via `dependency_overrides`; real tokens):
  - `test_setup_enable_flow`: setup returns a secret; enable with `totp_at(secret, time.time())` returns 200 and a token; the old token gets 401; the new token's `/users/me` reports `mfa_enabled: true`.
  - `test_enable_wrong_code`: 400 with the exact text.
  - `test_setup_when_enabled_409`.
  - `test_disable_requires_password_and_code`, and `test_disable_blocked_when_tenant_requires`: 403.
  - `test_admin_reset_revokes_target`: a same-tenant admin gets 204, the target's old token gets 401 and the target's MFA is cleared; a cross-tenant admin gets 404; an analyst gets 404; a super admin gets 204.
  - `test_tenant_security_toggle`: an admin can toggle it and sees the counts; an analyst gets 403.
  - `test_undecryptable_secret_409`: set `mfa_secret_enc` to `"garbage"` by id, then enable gets 409.
  - The response from setup is the only one containing the secret. Assert no other response text contains it.

- [ ] **Step 2: Implement. Step 3: Run, run the full suite, restart, then commit** `feat(auth): MFA setup, enable, disable, admin reset and tenant requirement`.

---

### Task 6: Two-step login and the tenant-switch guard

**Files:**
- Modify:
  - `backend/app/api/v1/auth.py`
  - `backend/app/schemas/user.py`: `Token` fields become optional; add `mfa_required: bool = False`, `mfa_setup_required: bool = False`, `mfa_token: Optional[str] = None`.
- Test: `backend/tests/test_login_mfa.py`

**Interfaces:**
- Consumes: `verify_user_code`, the redis dependency (Task 5); `issue_mfa_challenge`, `decode_mfa_challenge`, `issue_access_token` (Task 1).
- Produces: `POST /auth/login/mfa {mfa_token, code}` → `{access_token, token_type}`.

**Rules**
- **Password step, after `throttle.reset`:**
  - If `user.mfa_enabled_at` is set → `{"mfa_required": True, "mfa_token": issue_mfa_challenge(user)}`.
  - Else, if the user is not a super admin with zero memberships and any membership tenant has `require_mfa` → `{"mfa_setup_required": True, "mfa_token": ...}`.
  - Else → the normal token.
- **`/login/mfa`:**
  - `decode_mfa_challenge`. On any JWT error, or a `tv` mismatch, or a user who is missing or inactive, or MFA not enabled → 401 `"Login expired — sign in again"`.
  - Otherwise `verify_user_code`, then return the token for the default tenant.
- **switch-tenant:** if the target tenant has `require_mfa`, and the user has no MFA and isn't a super admin → 403 `{"detail": "mfa_setup_required"}`.

- [ ] **Step 1: Failing tests:**
  - `test_login_normal`
  - `test_login_mfa_required_then_code`
  - `test_code_replay_rejected`: the same code twice; the second gets 400.
  - `test_mfa_throttle_429`: a fake throttle returns 30 → 429 with `Retry-After`.
  - `test_expired_challenge_401`: build a challenge with `exp` in the past via `create_access_token(..., expires_delta=timedelta(seconds=-1))`.
  - `test_access_token_not_a_challenge`: posting a normal access token as `mfa_token` gets 401.
  - `test_setup_required_branch`: user without MFA whose tenant requires it gets `mfa_setup_required`; `GET /users/me` with that challenge gets 401; setup and enable with that challenge as Bearer return a full token that works.
  - `test_switch_tenant_requires_mfa`
  - `test_super_admin_without_membership_exempt`
  - SSO is untouched: the existing SSO tests still pass.

- [ ] **Step 2: Implement. Step 3: Run the full suite, restart, then commit** `feat(auth): two-step MFA login and tenant-switch guard`.

---

### Task 7: Frontend — profile page, Avatar, dates

**Files:**
- Create:
  - `frontend/src/features/profile/ProfilePage.tsx`
  - `frontend/src/features/profile/MfaSetupDialog.tsx` (also reused by login in Task 8)
  - `frontend/src/components/Avatar.tsx`
  - `frontend/src/utils/datetime.ts`
- Modify:
  - `frontend/package.json` (add `qrcode` and `@types/qrcode`)
  - the router (`App.tsx`): `/profile`
  - `frontend/src/components/layout/Layout.tsx`: the sidebar user block links to `/profile` and shows `<Avatar>`
  - `frontend/src/types/index.ts`

**Interfaces:**
- Consumes: the Task 3–5 endpoints. The `UserMe` fields are `job_title`, `timezone`, `has_avatar`, `mfa_enabled` and `has_password`.
- Produces:
  - `<Avatar userId name hasAvatar size?>`: initials fallback; blob URL fetched via `api.get(..., {responseType: 'blob'})`; React Query key `['avatar', userId, hasAvatar]`; `URL.revokeObjectURL` in cleanup.
  - `formatDateTime(value, tz?)`
  - `<MfaSetupDialog open onClose onEnabled(token) authToken?>`: when `authToken` is passed, it is sent as the Bearer header instead of the stored token. This is how forced setup works.
  - `setSessionToken(token)`: writes `localStorage.token` and invalidates all queries.

**Behaviour:** as described in the spec's Frontend section.
- **Profile card:** previews a chosen file with `URL.createObjectURL`, then uploads.
- **Timezone select:** built from `Intl.supportedValuesOf('timeZone')` with a filter input.
- **Password card:** after success, `setSessionToken(resp.access_token)` and a success note.
- **MFA dialog:** setup call, then the QR (from `QRCode.toDataURL(otpauth_uri)` into an `<img alt="Scan with your authenticator app">`), the secret in mono font with a copy button, a 6-digit input, then enable and `setSessionToken`.
- **Turn off:** a Modal with password and code fields.
- **Hidden controls:** when any membership tenant requires MFA, the backend returns 403 and the error is shown. Hide the turn-off button when `/users/me` reports `mfa_required_by_tenant`; if that field isn't exposed, add it to `UserMe` in this task's backend touch (a one-line computed field) and note it.
- **Secret handling:** never log or persist the secret or codes. Clear the dialog state on close.

- [ ] **Step 1: Implement. Step 2: Verify:** `npm install`, then `npx tsc --noEmit && npm run build`, eslint on touched files, rebuild the frontend, `/` → 200.
- [ ] **Step 3: Commit** `feat(profile): profile page, avatar component and MFA setup dialog`.

---

### Task 8: Frontend — login steps, admin MFA controls, avatars across the app

**Files:**
- Modify:
  - `frontend/src/features/auth/Login.tsx`
  - `frontend/src/features/admin/UserManagement.tsx`
  - `frontend/src/features/settings/Settings.tsx`
  - `frontend/src/features/cases/CaseDetail.tsx` (timeline author avatar)
  - `frontend/src/features/cases/MentionTextarea.tsx` (option avatar)
  - `frontend/src/features/notifications/NotificationBell.tsx` (actor avatar where an actor id is available; if the notification payload has no `actor_id`, skip it and note that)
  - the tenant switcher (handle a 403 `mfa_setup_required` with a clear message linking to `/profile`)

**Behaviour**
- **Login:**
  1. After the password POST, branch on `mfa_required` / `mfa_setup_required`.
  2. The code step uses `inputMode="numeric"`, `autoComplete="one-time-code"`, `maxLength=6`, and Back.
  3. `POST /auth/login/mfa`, then store the token as today.
  4. A 401 on this step returns to the password step with "Your sign-in expired — please sign in again".
  5. Setup-required renders `<MfaSetupDialog authToken={mfa_token}>`. Its `onEnabled(token)` stores the token and navigates as on a normal login.
  6. The challenge token is held in component state only, never in localStorage.
- **Users admin:** an "MFA" badge (on/off) per user, if the users list exposes it. Add `mfa_enabled` to the admin users schema if missing (backend one-liner). "Reset MFA" (admins only, hidden for yourself) opens the shared Modal confirm, then `POST /users/{id}/mfa/reset` and a toast.
- **Settings:** a "Require two-factor authentication" toggle backed by `GET`/`PUT /tenants/current/security`. It shows "N members don't have MFA yet".

- [ ] **Step 1: Implement. Step 2: Verify** as in Task 7, plus the full backend suite if the backend was touched. **Step 3: Commit** `feat(auth): MFA login steps, admin reset and tenant requirement UI`.

---

### Task 9: Docs and end-to-end check

**Docs**
- Add a "Profile and two-factor authentication" section to `docs/configuration.md` covering:
  - profile fields;
  - avatar limits;
  - password change revoking other sessions;
  - MFA enrollment, the tenant requirement, admin reset (recovery, no backup codes), SSO exemption, and a super admin locked out → another super admin resets them;
  - `token_version` semantics.
- Add a short entry to `docs/features.md`.

**End-to-end check in the container** (a one-off script under `/tmp`, deleted afterwards; use a fake redis in-process, or the real local Redis with test-unique keys; never print secrets or codes):
1. Create a temporary tenant, an admin user and an analyst user.
2. The analyst sets up MFA and enables it with a generated code.
3. The password login returns a challenge; the challenge plus a code returns a token; `/users/me` returns 200.
4. The admin resets the analyst's MFA, and the analyst's token gets 401.
5. Clean up by ids, then confirm 0 temporary tenants remain.

Record the results in the report. **Commit** `docs(profile): profile and two-factor authentication`.
