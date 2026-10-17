"""Guest JWTs. Every guest token carries `purpose`, which deps.get_current_user rejects outright."""
import secrets
from datetime import timedelta
from typing import Optional

import jwt

from app.core.config import settings
from app.core.security import create_access_token

LINK, SESSION, RESET, MFA = "guest-link", "guest", "guest-reset", "guest-mfa"
LINK_TTL = timedelta(days=7)
SESSION_TTL = timedelta(hours=12)
RESET_TTL = timedelta(minutes=30)
MFA_TTL = timedelta(minutes=5)


def issue_link(grant_id: int) -> str:
    return create_access_token({"sub": str(grant_id), "purpose": LINK}, expires_delta=LINK_TTL)


def issue_session(email: str, amr: str, *, sso_tenant_id: Optional[int] = None, gav: Optional[int] = None,
                  link_tenant_id: Optional[int] = None) -> str:
    """`link_tenant_id`: a magic-link session only opens the cases of the tenant whose grant minted it."""
    return create_access_token({"sub": f"guest:{email}", "email": email, "purpose": SESSION, "amr": amr,
                                "sso_tenant_id": sso_tenant_id, "link_tenant_id": link_tenant_id, "gav": gav},
                               expires_delta=SESSION_TTL)


def issue_reset(account) -> str:
    return create_access_token({"sub": str(account.id), "purpose": RESET, "tv": account.token_version or 0},
                               expires_delta=RESET_TTL)


def issue_mfa_challenge(account) -> str:
    return create_access_token({"sub": str(account.id), "purpose": MFA, "tv": account.token_version or 0,
                                "jti": secrets.token_urlsafe(16)}, expires_delta=MFA_TTL)


def decode(token: str, purpose: str) -> dict:
    payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
    if payload.get("purpose") != purpose:
        raise jwt.InvalidTokenError("wrong token purpose")
    return payload
