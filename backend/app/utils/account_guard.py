"""Who may manage a platform account (the one place this rule lives).

Accounts are global (``is_active``, ``token_version``, MFA, sessions) while tenant admins are scoped to one
tenant. So:

- a super admin only bootstraps tenants: they may create and manage users of a tenant that has no active
  admin yet (to set up its first admin); once a tenant has an active admin, users there are that admin's to
  manage and the super admin may not create or edit any of them (they can still deactivate the tenant);
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
TENANT_HAS_ADMIN = "This organization already has an admin; its users are managed by that admin."


HOME_ORDER = (TenantMembership.created_at, TenantMembership.id)


async def home_tenant_id(db: AsyncSession, user_id: int) -> Optional[int]:
    """The user's home tenant: their oldest membership (lowest id breaks a created_at tie)."""
    return (await db.execute(select(TenantMembership.tenant_id).where(TenantMembership.user_id == user_id)
                             .order_by(*HOME_ORDER).limit(1))).scalars().first()


async def tenant_has_admin(db: AsyncSession, tenant_id: Optional[int]) -> bool:
    """True when the tenant has an active, non-super-admin admin member (a super admin's hands-off point)."""
    if tenant_id is None:
        return False
    return (await db.execute(select(TenantMembership.id).join(User, User.id == TenantMembership.user_id).where(
        TenantMembership.tenant_id == tenant_id, TenantMembership.role == "admin",
        User.is_active.is_(True), User.is_super_admin.is_(False)).limit(1))).first() is not None


async def any_tenant_has_admin(db: AsyncSession, user_id: int) -> bool:
    """True when any tenant the user belongs to has an active admin (used for account-wide actions)."""
    tids = (await db.execute(select(TenantMembership.tenant_id).where(
        TenantMembership.user_id == user_id))).scalars().all()
    for tid in tids:
        if await tenant_has_admin(db, tid):
            return True
    return False


async def refusal(db: AsyncSession, actor: User, target: User, tenant_id: Optional[int], scope: str) -> Optional[str]:
    """The refusal message for ``actor`` acting on ``target`` from ``tenant_id``, or None when allowed."""
    if actor.is_super_admin:
        if target.id == actor.id:
            return None
        return TENANT_HAS_ADMIN if await tenant_has_admin(db, tenant_id) else None
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


async def assert_super_admin_may_bootstrap(db: AsyncSession, actor: User, tenant_id: Optional[int], *,
                                           action: str, role: Optional[str] = None,
                                           request: Optional[Request] = None) -> None:
    """A super admin may add users to a tenant only while it has no active admin, and only as its admin."""
    if not actor.is_super_admin:
        return
    if await tenant_has_admin(db, tenant_id):
        await deny(request, actor, tenant_id, action, target_id=None, detail=TENANT_HAS_ADMIN)
    if role is not None and role != "admin":
        raise HTTPException(status_code=422, detail="Set up the organization's admin first; they invite everyone else.")


async def assert_can_manage_account(db: AsyncSession, actor: User, target: User, tenant_id: Optional[int], *,
                                    action: str, scope: str = GLOBAL, request: Optional[Request] = None) -> None:
    """Raise 403 (and audit the attempt) unless ``actor`` may perform ``action`` on ``target``."""
    msg = await refusal(db, actor, target, tenant_id, scope)
    if msg is not None:
        await deny(request, actor, tenant_id, action, target_id=target.id, detail=msg)
