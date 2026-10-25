"""Server-side login sessions (web and OAuth) with rotating, reuse-detecting refresh tokens.

A refresh token is ``f"{session.id}.{secret}"``. Only ``hash_secret(secret)`` (HMAC-SHA256 keyed with
SECRET_KEY) is stored. Every successful ``rotate`` issues a fresh secret; the previous hash is kept for a
short grace window so two concurrent refreshes from one client (benign race) do not kill the session.
Presenting any other secret revokes the session (presumed theft) and writes a ``session.refresh_reuse``
security audit event.

Transactions: like the other services here, the happy paths only ``flush`` and the caller commits.
The one exception is a refusal that revokes the session (reuse, password/tv change, deactivated user):
``rotate`` commits that revocation itself before raising, because callers roll back on errors and the
revocation must not be lost with them.
"""
import hashlib
import hmac
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import Request
from sqlalchemy import select, update

from app.audit.events import audit_event
from app.audit.middleware import audit_ip
from app.core.config import settings
from app.models.auth_session import AuthSession
from app.models.user import User

KINDS = ("web", "oauth")
AMRS = ("pwd", "saml", "oidc")
REFRESH_GRACE_SECONDS = 10
LIVE_BUMP_SECONDS = 60
_SID_RE = re.compile(r"^[0-9a-f]{32}$")


class RefreshError(Exception):
    """Refresh refused. ``code`` is one of: invalid, expired, reuse, revoked."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def hash_secret(secret: str) -> str:
    return hmac.new(settings.SECRET_KEY.encode(), secret.encode(), hashlib.sha256).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _new_secret() -> str:
    return secrets.token_urlsafe(32)


async def create_session(db, user: User, *, kind: str, amr: str, sso_tenant_id: Optional[int] = None,
                         tenant_id: Optional[int] = None, client_id: Optional[str] = None,
                         resource: Optional[str] = None, scope: Optional[str] = None,
                         request: Optional[Request] = None) -> tuple[AuthSession, str]:
    """Create a session row and return ``(row, refresh_token)``. Flushes; the caller commits."""
    if kind not in KINDS:
        raise ValueError(f"invalid session kind: {kind!r}")
    if amr not in AMRS:
        raise ValueError(f"invalid session amr: {amr!r}")
    secret = _new_secret()
    now = _now()
    ua = ((request.headers.get("user-agent") or "")[:300] or None) if request is not None else None
    row = AuthSession(user_id=user.id, kind=kind, tenant_id=tenant_id, client_id=client_id, resource=resource,
                      scope=scope, amr=amr, sso_tenant_id=sso_tenant_id, tv=int(user.token_version or 0),
                      refresh_hash=hash_secret(secret), created_at=now, last_used_at=now, user_agent=ua,
                      ip=audit_ip(request) if request is not None else None)
    db.add(row)
    await db.flush()
    return row, f"{row.id}.{secret}"


async def _revoke_and_raise(db, row: AuthSession, code: str) -> None:
    row.revoked_at = _now()
    await db.commit()
    raise RefreshError(code)


async def rotate(db, refresh_token: str, *, kind: str) -> tuple[AuthSession, User, Optional[str]]:
    """Validate a refresh token and rotate its secret. Returns ``(row, user, new_refresh_token)``.

    ``new_refresh_token`` is None for the benign race: the presented secret is the one a concurrent refresh
    rotated less than REFRESH_GRACE_SECONDS ago. The row's hashes and ``rotated_at`` are left untouched (only
    ``last_used_at`` is bumped); the caller re-issues the access token only and must NOT replace the
    client's refresh token, so the client keeps the winner's secret and no false reuse alarm follows.

    The session row is locked (SELECT ... FOR UPDATE) so concurrent rotations serialize. On success it
    flushes and the caller commits (which also releases the lock).

    Refusals that revoke the session (reuse, token_version change, inactive/deleted user) COMMIT the
    caller's session before raising, so the revocation survives the caller's rollback. Any other pending
    work on ``db`` is committed with it: call ``rotate`` before making other changes in the same session.
    """
    sid, _, secret = (refresh_token or "").partition(".")
    if not _SID_RE.match(sid) or not secret:
        raise RefreshError("invalid")
    row = (await db.execute(
        select(AuthSession).where(AuthSession.id == sid).with_for_update()
        .execution_options(populate_existing=True))).scalar_one_or_none()
    if row is None or row.kind != kind:
        raise RefreshError("invalid")
    if row.revoked_at is not None:
        raise RefreshError("revoked")

    now = _now()
    presented = hash_secret(secret)
    if hmac.compare_digest(presented, row.refresh_hash):
        grace = False
    elif (row.prev_refresh_hash and row.rotated_at is not None
          and hmac.compare_digest(presented, row.prev_refresh_hash)
          and now - row.rotated_at <= timedelta(seconds=REFRESH_GRACE_SECONDS)):
        grace = True  # benign race: a concurrent refresh already rotated this secret
    else:
        row.revoked_at = now
        user_id, tenant_id = row.user_id, row.tenant_id
        await db.commit()
        await audit_event("security", "session.refresh_reuse", user_id=user_id, tenant_id=tenant_id,
                          outcome="denied", changes={"session_id": sid})
        raise RefreshError("reuse")

    if now - row.last_used_at > timedelta(days=settings.REFRESH_IDLE_DAYS):
        raise RefreshError("expired")
    user = (await db.execute(select(User).where(User.id == row.user_id))).scalar_one_or_none()
    if user is None or not user.is_active or int(user.token_version or 0) != row.tv:
        await _revoke_and_raise(db, row, "revoked")

    row.last_used_at = now
    if grace:
        # No new secret: the winner's secret stays current, and the window (bounded from the first
        # rotation) cannot be extended by replaying the old secret.
        await db.flush()
        return row, user, None
    new_secret = _new_secret()
    row.prev_refresh_hash = row.refresh_hash
    row.rotated_at = now
    row.refresh_hash = hash_secret(new_secret)
    await db.flush()
    return row, user, f"{row.id}.{new_secret}"


def rekey(row: AuthSession) -> str:
    """Replace the session's refresh secret outright and return the new refresh token. Used after a
    credential change (password, MFA): unlike ``rotate`` the old secret gets no grace window, so any
    other copy of it is reuse from now on. The caller flushes/commits."""
    secret = _new_secret()
    row.refresh_hash = hash_secret(secret)
    row.prev_refresh_hash = None
    row.rotated_at = _now()
    return f"{row.id}.{secret}"


async def revoke(db, session_id: str, *, user_id: Optional[int] = None, tenant_id: Optional[int] = None) -> bool:
    """Revoke one live session. Scope: ``user_id`` → only that user's own session; ``tenant_id`` → only an
    oauth session in that tenant (tenant-admin revocation). Neither → unscoped (internal callers only).
    Returns True if a live session was revoked. Flushes via the UPDATE; the caller commits."""
    stmt = update(AuthSession).where(AuthSession.id == session_id, AuthSession.revoked_at.is_(None))
    if user_id is not None:
        stmt = stmt.where(AuthSession.user_id == user_id)
    if tenant_id is not None:
        stmt = stmt.where(AuthSession.tenant_id == tenant_id, AuthSession.kind == "oauth")
    res = await db.execute(stmt.values(revoked_at=_now()).execution_options(synchronize_session="fetch"))
    return (res.rowcount or 0) > 0


async def revoke_all_for_user(db, user_id: int) -> None:
    """Revoke every live session (web and OAuth) of a user. Pair it with ``bump_token_version`` when an
    admin action signs the user out everywhere, so the session list does not show dead sessions as live.
    Flushes via the UPDATE; the caller commits."""
    await db.execute(update(AuthSession).where(AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None))
                     .values(revoked_at=_now()).execution_options(synchronize_session=False))


async def is_live(db, session_id: str) -> Optional[AuthSession]:
    """Return the session if it exists and is not revoked, else None.

    Bumps ``last_used_at`` at most once per ``LIVE_BUMP_SECONDS``. It only flushes, never commits: the
    per-request caller owns the transaction (consistent with the other services), so the bump persists
    when the request's session commits and is simply dropped on a rollback (harmless)."""
    row = (await db.execute(select(AuthSession).where(
        AuthSession.id == session_id, AuthSession.revoked_at.is_(None)))).scalar_one_or_none()
    if row is None:
        return None
    now = _now()
    if row.last_used_at is None or now - row.last_used_at >= timedelta(seconds=LIVE_BUMP_SECONDS):
        row.last_used_at = now
        await db.flush()
    return row
