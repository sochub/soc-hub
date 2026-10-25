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


async def rotate(db, refresh_token: str, *, kind: str) -> tuple[AuthSession, User, str]:
    """Validate a refresh token and rotate its secret. Returns ``(row, user, new_refresh_token)``.

    The session row is locked (SELECT ... FOR UPDATE) so concurrent rotations serialize. On success it
    flushes and the caller commits (which also releases the lock). Refusals that revoke commit first.
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

    new_secret = _new_secret()
    if not grace:
        row.prev_refresh_hash = row.refresh_hash
        row.rotated_at = now
    # In the grace path prev_refresh_hash/rotated_at stay put: the window is bounded from the first
    # rotation and cannot be extended by replaying the old secret.
    row.refresh_hash = hash_secret(new_secret)
    row.last_used_at = now
    await db.flush()
    return row, user, f"{row.id}.{new_secret}"


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
