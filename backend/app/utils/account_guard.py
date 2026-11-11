"""Who may manage a platform account (the one place this rule lives).

Accounts are global (``is_active``, ``token_version``, MFA, sessions) while tenant admins are scoped to one
tenant. So:

- a super admin may act on anyone;
- nobody else may act on a super admin (break-glass accounts must never be locked out by a tenant);
- a tenant admin may change GLOBAL account state only for a user whose HOME tenant (oldest membership) is the
  admin's active tenant; any other tenant manages that user's membership in its own tenant only. Memberships
  of existing accounts are only ever added by the user accepting an invitation (or into a tenant with no members
  yet), so another tenant can't make itself someone's home.

Every refusal writes a ``security`` audit row (``user.manage_denied``)."""
from typing import Optional

from fastapi import HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.events import audit_event
from app.models.membership import TenantMembership
from app.models.user import User

GLOBAL = "global"          # account-wide state: active flag, sessions, MFA, password, email
MEMBERSHIP = "membership"  # the user's membership in the actor's tenant: role, removal

NOT_ALLOWED = "You can't manage this account."
OTHER_TENANTS = "This account belongs to other tenants; ask a platform admin."


HOME_ORDER = (TenantMembership.created_at, TenantMembership.id)


async def home_tenant_id(db: AsyncSession, user_id: int) -> Optional[int]:
    """The user's home tenant: their oldest membership (lowest id breaks a created_at tie)."""
    return (await db.execute(select(TenantMembership.tenant_id).where(TenantMembership.user_id == user_id)
                             .order_by(*HOME_ORDER).limit(1))).scalars().first()


async def refusal(db: AsyncSession, actor: User, target: User, tenant_id: Optional[int], scope: str) -> Optional[str]:
    """The refusal message for ``actor`` acting on ``target`` from ``tenant_id``, or None when allowed."""
    if actor.is_super_admin:
        return None
    if target.is_super_admin:
        return NOT_ALLOWED
    if scope == GLOBAL and await home_tenant_id(db, target.id) != tenant_id:
        return OTHER_TENANTS
    return None


async def deny(request: Optional[Request], actor: User, tenant_id: Optional[int], action: str, *,
               target_id: Optional[int], detail: str, status_code: int = 403) -> None:
    """Audit a refused account action and raise."""
    await audit_event("security", "user.manage_denied", request=request, outcome="denied", tenant_id=tenant_id,
                      user_id=actor.id, user_email=actor.email, entity_type="user", entity_id=target_id,
                      changes={"attempted": action})
    raise HTTPException(status_code=status_code, detail=detail)


async def assert_can_manage_account(db: AsyncSession, actor: User, target: User, tenant_id: Optional[int], *,
                                    action: str, scope: str = GLOBAL, request: Optional[Request] = None) -> None:
    """Raise 403 (and audit the attempt) unless ``actor`` may perform ``action`` on ``target``."""
    msg = await refusal(db, actor, target, tenant_id, scope)
    if msg is not None:
        await deny(request, actor, tenant_id, action, target_id=target.id, detail=msg)
