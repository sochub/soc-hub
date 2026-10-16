"""Who a guest is and which grants they may use right now. Re-checked on every guest request."""
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jwt import PyJWTError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.guests import tokens
from app.models.case_guest import CaseGuest, GuestAccount
from app.models.tenant import Tenant

guest_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/guest/auth/login")
NOT_FOUND = HTTPException(status_code=404, detail="Case not found")


@dataclass
class GuestSession:
    email: str
    amr: str
    sso_tenant_id: Optional[int]
    account_id: Optional[int]


def ACTIVE():
    return [CaseGuest.revoked_at.is_(None), CaseGuest.expires_at > func.now(), Tenant.is_active.is_(True)]


def domain_allowed(tenant: Tenant, email: str) -> bool:
    domain = email.rsplit("@", 1)[-1].lower()
    return domain in {d.lower() for d in (tenant.guest_domain_allowlist or [])}


def session_allowed(tenant: Tenant, s: GuestSession) -> bool:
    if tenant.guest_require_sso:
        return s.amr == "sso" and s.sso_tenant_id == tenant.id
    return True


async def get_current_guest(db: AsyncSession = Depends(get_db), token: str = Depends(guest_scheme)) -> GuestSession:
    bad = HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Could not validate credentials",
                        headers={"WWW-Authenticate": "Bearer"})
    try:
        p = tokens.decode(token, tokens.SESSION)
    except PyJWTError:
        raise bad
    email = p.get("email")
    if not isinstance(email, str) or "@" not in email:
        raise bad
    account_id = None
    if p.get("gav") is not None:  # password sessions die when the account's token_version moves
        acct = (await db.execute(select(GuestAccount).where(GuestAccount.email == email))).scalars().first()
        if acct is None or (acct.token_version or 0) != p["gav"]:
            raise bad
        account_id = acct.id
    return GuestSession(email=email, amr=str(p.get("amr")), sso_tenant_id=p.get("sso_tenant_id"),
                        account_id=account_id)


async def visible_grants(db: AsyncSession, s: GuestSession) -> list:
    rows = (await db.execute(
        select(CaseGuest, Tenant).join(Tenant, Tenant.id == CaseGuest.tenant_id)
        .where(CaseGuest.email == s.email, *ACTIVE()).order_by(CaseGuest.id)
    )).all()
    return [(g, t) for g, t in rows if domain_allowed(t, s.email) and session_allowed(t, s)]


async def get_guest_grant(case_id: int, db: AsyncSession = Depends(get_db),
                          guest: GuestSession = Depends(get_current_guest)) -> CaseGuest:
    row = (await db.execute(
        select(CaseGuest, Tenant).join(Tenant, Tenant.id == CaseGuest.tenant_id)
        .where(CaseGuest.case_id == case_id, CaseGuest.email == guest.email, *ACTIVE())
    )).first()
    if row is None or not domain_allowed(row[1], guest.email) or not session_allowed(row[1], guest):
        raise NOT_FOUND
    grant = row[0]
    grant.last_seen_at = datetime.now(timezone.utc)
    await db.commit()
    return grant


def require_contributor(grant: CaseGuest = Depends(get_guest_grant)) -> CaseGuest:
    if grant.permission != "contributor":
        raise HTTPException(status_code=403, detail="Your access to this case is read-only")
    return grant
