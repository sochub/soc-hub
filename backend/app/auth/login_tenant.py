"""Login-policy orchestration: apply app.auth.login_policy to a user's memberships and tenants."""
from typing import Optional

from fastapi import HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.audit.events import audit_event
from app.auth.login_policy import (
    AMR_PWD, is_password_fallback, member_may_enter, password_required_error, sso_required_error,
)
from app.models.membership import TenantMembership
from app.models.tenant import Tenant
from app.models.user import User


def _member_role(user: User, tenant_id: int) -> Optional[str]:
    return next((m.role for m in user.memberships if m.tenant_id == tenant_id), None)


def session_may_enter(user: User, tenant_id: int, method: str) -> bool:
    """Login policy: may this request's session (its amr / sso_tid) act in `tenant_id`?"""
    return member_may_enter(
        method, amr=getattr(user, "_amr", AMR_PWD), sso_tenant_id=getattr(user, "_sso_tenant_id", None),
        tenant_id=tenant_id, role=_member_role(user, tenant_id), mfa_enabled=user.mfa_enabled_at is not None,
        is_super_admin=bool(user.is_super_admin))


def method_refusal(user: User, tenant_id: int, method: str, slug: str) -> HTTPException:
    """Why a session may not enter a tenant: an SSO session aimed at another (password) tenant needs a
    password sign-in; anything else needs the target tenant's SSO."""
    amr = getattr(user, "_amr", AMR_PWD)
    if amr != AMR_PWD and getattr(user, "_sso_tenant_id", None) != tenant_id and method == "password":
        return password_required_error()
    return sso_required_error(slug)


async def audit_if_password_fallback(request: Request, user: User, tenant_id: Optional[int],
                                     method: Optional[str], amr: str = AMR_PWD) -> None:
    """Break-glass: a password session opening an SSO-method tenant is always audited."""
    if tenant_id is not None and method and is_password_fallback(method, amr):
        await audit_event("security", "sso.password_fallback", request=request, user_id=user.id,
                          user_email=user.email, tenant_id=tenant_id, changes={"method": method})


async def password_login_tenant(db: AsyncSession, user: User, *, mfa_enabled: bool
                                ) -> tuple[Optional[int], Optional[str], Optional[str]]:
    """Default active tenant for a password sign-in: the lowest-id tenant the login policy lets a `pwd`
    session enter. Returns (tenant_id, method, None); (None, None, None) for a user with no tenant; or
    (None, None, slug of the lowest-id membership) when memberships exist but none admit a password."""
    if user.is_super_admin:
        row = (await db.execute(
            select(Tenant.id, Tenant.member_login_method).order_by(Tenant.id).limit(1))).first()
        return (row[0], row[1], None) if row else (None, None, None)
    rows = (await db.execute(
        select(TenantMembership.tenant_id, TenantMembership.role, Tenant.member_login_method, Tenant.slug)
        .join(Tenant, Tenant.id == TenantMembership.tenant_id)
        .where(TenantMembership.user_id == user.id)
        .order_by(TenantMembership.tenant_id))).all()
    for tid, role, method, _slug in rows:
        if member_may_enter(method, amr=AMR_PWD, sso_tenant_id=None, tenant_id=tid, role=role,
                            mfa_enabled=mfa_enabled, is_super_admin=False):
            return tid, method, None
    return None, None, (rows[0][3] if rows else None)
