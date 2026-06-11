from typing import Any, List
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.api import deps
from app.core import security
from app.models.membership import TenantMembership
from app.models.tenant import Tenant
from app.models.user import User, UserRole
from app.schemas.membership import MembershipOut
from app.schemas.user import User as UserSchema, UserCreate, UserUpdate, UserRoleUpdate, UserMe
from app.utils.roles import resolve_active_role

router = APIRouter()


@router.get("/", response_model=List[UserSchema])
async def read_users(
    db: AsyncSession = Depends(deps.get_db),
    skip: int = 0,
    limit: int = 100,
    current_user: User = Depends(deps.require_admin),
    tenant_id: int = Depends(deps.get_effective_tenant_id),
) -> Any:
    """List users who are members of the active tenant, with their tenant role."""
    rows = await db.execute(
        select(User, TenantMembership.role)
        .join(TenantMembership, TenantMembership.user_id == User.id)
        .where(TenantMembership.tenant_id == tenant_id)
        .offset(skip).limit(limit)
    )
    return [
        UserSchema(
            id=u.id, email=u.email, full_name=u.full_name,
            is_active=u.is_active, is_super_admin=u.is_super_admin, role=role,
        )
        for u, role in rows.all()
    ]


@router.post("/", response_model=UserSchema)
async def create_user(
    *,
    db: AsyncSession = Depends(deps.get_db),
    user_in: UserCreate,
    current_user: User = Depends(deps.require_admin),
    tenant_id: int = Depends(deps.get_effective_tenant_id),
) -> Any:
    """Create a new user and add them as a member of the active tenant."""
    if user_in.role == UserRole.SUPER_ADMIN:
        raise HTTPException(status_code=400, detail="Cannot create super admin users through this endpoint.")

    result = await db.execute(select(User).where(User.email == user_in.email))
    if result.scalars().first():
        raise HTTPException(status_code=409, detail="A user with this email already exists.")

    user = User(
        email=user_in.email,
        hashed_password=security.get_password_hash(user_in.password),
        full_name=user_in.full_name,
        is_active=user_in.is_active,
        is_super_admin=False,
    )
    db.add(user)
    await db.flush()
    db.add(TenantMembership(user_id=user.id, tenant_id=tenant_id, role=user_in.role.value))
    await db.commit()
    await db.refresh(user)
    return UserSchema(
        id=user.id, email=user.email, full_name=user.full_name,
        is_active=user.is_active, is_super_admin=user.is_super_admin, role=user_in.role.value,
    )


@router.get("/me", response_model=UserMe)
async def read_user_me(
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
) -> Any:
    """Current user with active-tenant context and all tenant memberships."""
    active = getattr(current_user, "_active_tenant_id", None)

    rows = await db.execute(
        select(TenantMembership, Tenant)
        .join(Tenant, Tenant.id == TenantMembership.tenant_id)
        .where(TenantMembership.user_id == current_user.id)
        .order_by(Tenant.id)
    )
    memberships = [
        MembershipOut(tenant_id=t.id, tenant_name=t.name, tenant_slug=t.slug, role=m.role)
        for m, t in rows.all()
    ]
    role = resolve_active_role(current_user.is_super_admin, active, current_user.memberships)

    return UserMe(
        id=current_user.id,
        email=current_user.email,
        full_name=current_user.full_name,
        is_active=current_user.is_active,
        is_super_admin=current_user.is_super_admin,
        role=role,
        active_tenant_id=active,
        memberships=memberships,
    )


@router.put("/me", response_model=UserMe)
async def update_user_me(
    *,
    db: AsyncSession = Depends(deps.get_db),
    user_in: UserUpdate,
    current_user: User = Depends(deps.get_current_active_user),
) -> Any:
    """Update own profile (name / password)."""
    if user_in.full_name is not None:
        current_user.full_name = user_in.full_name
    if user_in.password is not None:
        current_user.hashed_password = security.get_password_hash(user_in.password)
    await db.commit()
    # Reuse the /me builder for a consistent response.
    return await read_user_me(db=db, current_user=current_user)


async def _membership_in_tenant(db: AsyncSession, user_id: int, tenant_id: int) -> TenantMembership:
    res = await db.execute(
        select(TenantMembership).where(
            TenantMembership.user_id == user_id,
            TenantMembership.tenant_id == tenant_id,
        )
    )
    membership = res.scalars().first()
    if not membership:
        raise HTTPException(status_code=404, detail="User is not a member of this tenant.")
    return membership


@router.put("/{user_id}/role", response_model=UserSchema)
async def update_user_role(
    *,
    db: AsyncSession = Depends(deps.get_db),
    user_id: int,
    role_in: UserRoleUpdate,
    current_user: User = Depends(deps.require_admin),
    tenant_id: int = Depends(deps.get_effective_tenant_id),
) -> Any:
    """Change a user's role within the active tenant. Cannot set super_admin."""
    if role_in.role == UserRole.SUPER_ADMIN:
        raise HTTPException(status_code=400, detail="Cannot assign super_admin role.")

    membership = await _membership_in_tenant(db, user_id, tenant_id)
    membership.role = role_in.role.value
    await db.commit()

    user = (await db.execute(select(User).where(User.id == user_id))).scalars().first()
    return UserSchema(
        id=user.id, email=user.email, full_name=user.full_name,
        is_active=user.is_active, is_super_admin=user.is_super_admin, role=membership.role,
    )


@router.delete("/{user_id}/membership", status_code=204)
async def remove_member(
    *,
    db: AsyncSession = Depends(deps.get_db),
    user_id: int,
    current_user: User = Depends(deps.require_admin),
    tenant_id: int = Depends(deps.get_effective_tenant_id),
) -> None:
    """Remove a user from the active tenant (deletes the membership only)."""
    if user_id == current_user.id:
        raise HTTPException(status_code=400, detail="You cannot remove yourself.")
    membership = await _membership_in_tenant(db, user_id, tenant_id)
    if membership.role == UserRole.ADMIN.value:
        count = (await db.execute(
            select(func.count()).select_from(TenantMembership).where(
                TenantMembership.tenant_id == tenant_id,
                TenantMembership.role == UserRole.ADMIN.value,
            )
        )).scalar()
        if count <= 1:
            raise HTTPException(status_code=400, detail="Cannot remove the last admin of this tenant.")
    await db.delete(membership)
    await db.commit()


@router.put("/{user_id}/deactivate", response_model=UserSchema)
async def deactivate_user(
    *,
    db: AsyncSession = Depends(deps.get_db),
    user_id: int,
    current_user: User = Depends(deps.require_admin),
    tenant_id: int = Depends(deps.get_effective_tenant_id),
) -> Any:
    """Deactivate a user's account. Admin of the user's tenant only."""
    membership = await _membership_in_tenant(db, user_id, tenant_id)
    if user_id == current_user.id:
        raise HTTPException(status_code=400, detail="Cannot deactivate yourself.")
    user = (await db.execute(select(User).where(User.id == user_id))).scalars().first()
    user.is_active = False
    await db.commit()
    return UserSchema(
        id=user.id, email=user.email, full_name=user.full_name,
        is_active=user.is_active, is_super_admin=user.is_super_admin, role=membership.role,
    )


@router.put("/{user_id}/activate", response_model=UserSchema)
async def activate_user(
    *,
    db: AsyncSession = Depends(deps.get_db),
    user_id: int,
    current_user: User = Depends(deps.require_admin),
    tenant_id: int = Depends(deps.get_effective_tenant_id),
) -> Any:
    """Reactivate a user's account. Admin of the user's tenant only."""
    membership = await _membership_in_tenant(db, user_id, tenant_id)
    user = (await db.execute(select(User).where(User.id == user_id))).scalars().first()
    user.is_active = True
    await db.commit()
    return UserSchema(
        id=user.id, email=user.email, full_name=user.full_name,
        is_active=user.is_active, is_super_admin=user.is_super_admin, role=membership.role,
    )
