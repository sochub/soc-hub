import asyncio
import logging
import secrets
from typing import Any, List

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, Response, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import aliased
from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.api import deps
from app.core import security
from app.models.membership import TenantMembership
from app.models.tenant import Tenant
from app.models.user import User, UserRole
from app.schemas.membership import MembershipOut
from app.schemas.user import User as UserSchema, UserCreate, UserUpdate, UserRoleUpdate, UserMe, PasswordChange, TokenOnly
from app.core.login_throttle import LoginThrottle, client_ip, get_login_throttle
from app.services.avatars import MAX_BYTES, reencode_avatar
from app.storage import StorageError, get_storage
from app.utils.audit import create_audit_log
from app.schemas.notification import MentionableUser
from app.utils.roles import resolve_active_role

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/", response_model=List[UserSchema])
async def read_users(
    db: AsyncSession = Depends(deps.get_db),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
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


@router.get("/mentionable", response_model=List[MentionableUser])
async def mentionable_users(
    q: str = Query("", max_length=100),
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.require_analyst_or_above),
    tenant_id: int = Depends(deps.get_effective_tenant_id),
) -> Any:
    """Active members of the active tenant whose name or email starts with q."""
    esc = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    rows = await db.execute(
        select(User).join(TenantMembership, TenantMembership.user_id == User.id)
        .where(TenantMembership.tenant_id == tenant_id, User.is_active.is_(True),
               (User.full_name.ilike(esc, escape="\\")) | (User.email.ilike(esc, escape="\\")))
        .order_by(User.full_name, User.id).limit(10)
    )
    return [MentionableUser(id=u.id, name=u.full_name or (u.email or "").split("@")[0], email=u.email) for u in rows.scalars().all()]


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

    result = await db.execute(select(User).where(func.lower(User.email) == user_in.email))
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
        job_title=current_user.job_title,
        timezone=current_user.timezone,
        has_avatar=bool(current_user.avatar_key),
        mfa_enabled=current_user.mfa_enabled_at is not None,
        has_password=bool(current_user.password_login_enabled),
    )


@router.put("/me", response_model=UserMe)
async def update_user_me(
    *,
    db: AsyncSession = Depends(deps.get_db),
    user_in: UserUpdate,
    current_user: User = Depends(deps.get_current_active_user),
) -> Any:
    """Update own profile (name, job title, timezone)."""
    data = user_in.model_dump(exclude_unset=True)
    for field in ("full_name", "job_title", "timezone"):
        if field in data:
            setattr(current_user, field, data[field])
    await db.commit()
    # Reuse the /me builder for a consistent response.
    return await read_user_me(db=db, current_user=current_user)


def _audit_tenant(user: User):
    """Tenant for self-service audit rows (audit_logs.tenant_id is NOT NULL); None if no membership."""
    active = getattr(user, "_active_tenant_id", None)
    if active is None and user.memberships:
        return min(m.tenant_id for m in user.memberships)
    return active


AVATAR_ERROR = "Image must be PNG, JPEG or WebP up to 2 MB"


@router.put("/me/avatar", response_model=UserMe)
async def upload_avatar(
    *,
    db: AsyncSession = Depends(deps.get_db),
    file: UploadFile = File(...),
    current_user: User = Depends(deps.get_current_active_user),
) -> Any:
    data = await file.read(MAX_BYTES + 1)
    try:
        webp = await asyncio.to_thread(reencode_avatar, data)
    except ValueError:
        raise HTTPException(status_code=422, detail=AVATAR_ERROR)
    uid = current_user.id
    # Lock the row so concurrent uploads serialize and never orphan a key.
    old_key = (await db.execute(
        select(User.avatar_key).where(User.id == uid).with_for_update())).scalar_one()
    audit_tenant = _audit_tenant(current_user)
    key = f"avatars/{uid}/{secrets.token_hex(16)}.webp"
    storage = get_storage()

    async def _one():
        yield webp

    await storage.put(key, _one())
    try:
        current_user.avatar_key = key
        if audit_tenant is not None:
            await create_audit_log(db=db, entity_type="user", entity_id=uid, action="avatar_changed",
                                   tenant_id=audit_tenant, user_id=uid, changes=None)
        await db.commit()
    except BaseException:
        await db.rollback()
        try:
            await storage.delete(key)
        except Exception:
            pass
        raise
    if old_key:
        try:
            await storage.delete(old_key)
        except Exception:
            logger.warning("avatar cleanup failed user_id=%s", uid)
    return await read_user_me(db=db, current_user=current_user)


@router.delete("/me/avatar", status_code=204)
async def delete_avatar(
    *,
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
) -> Response:
    uid, old_key = current_user.id, current_user.avatar_key
    if old_key:
        audit_tenant = _audit_tenant(current_user)
        current_user.avatar_key = None
        if audit_tenant is not None:
            await create_audit_log(db=db, entity_type="user", entity_id=uid, action="avatar_removed",
                                   tenant_id=audit_tenant, user_id=uid, changes=None)
        await db.commit()
        try:
            await get_storage().delete(old_key)
        except Exception:
            logger.warning("avatar cleanup failed user_id=%s", uid)
    return Response(status_code=204)


@router.get("/{user_id}/avatar")
async def get_avatar(
    *,
    user_id: int,
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
) -> Any:
    target = (await db.execute(select(User).where(User.id == user_id))).scalars().first()
    if target is None or not target.avatar_key:
        raise HTTPException(status_code=404, detail="Not found")
    if not current_user.is_super_admin and current_user.id != user_id:
        mine, theirs = aliased(TenantMembership), aliased(TenantMembership)
        shared = (await db.execute(
            select(mine.id).join(theirs, theirs.tenant_id == mine.tenant_id)
            .where(mine.user_id == current_user.id, theirs.user_id == user_id).limit(1)
        )).first()
        if shared is None:
            raise HTTPException(status_code=404, detail="Not found")
    it = get_storage().open(target.avatar_key)
    try:
        first = await it.__anext__()
    except (StopAsyncIteration, FileNotFoundError, StorageError):
        await it.aclose()
        raise HTTPException(status_code=404, detail="Not found")
    except BaseException:
        await it.aclose()
        raise

    async def _chain():
        try:
            yield first
            async for c in it:
                yield c
        finally:
            await it.aclose()

    return StreamingResponse(_chain(), media_type="image/webp", headers={
        "Cache-Control": "private, max-age=300", "X-Content-Type-Options": "nosniff"})


@router.post("/me/password", response_model=TokenOnly)
async def change_password(
    *,
    request: Request,
    db: AsyncSession = Depends(deps.get_db),
    body: PasswordChange,
    current_user: User = Depends(deps.get_current_active_user),
    throttle: LoginThrottle = Depends(get_login_throttle),
) -> Any:
    """Change own password (requires the current one). Revokes all other sessions
    and returns a fresh token for this one."""
    if not current_user.password_login_enabled:
        raise HTTPException(status_code=400, detail="This account signs in through SSO and has no password.")
    key, ip = f"pwd:{current_user.id}", client_ip(request)
    retry_after = await throttle.reserve(key, ip)
    if retry_after:
        raise HTTPException(status_code=429, detail="Too many attempts. Try again later.",
                            headers={"Retry-After": str(retry_after)})
    if not security.verify_password(body.current_password, current_user.hashed_password):
        raise HTTPException(status_code=400, detail="Current password is incorrect")
    await throttle.reset(key, ip)

    active = getattr(current_user, "_active_tenant_id", None)
    audit_tenant = _audit_tenant(current_user)
    current_user.hashed_password = security.get_password_hash(body.new_password)
    security.bump_token_version(current_user)
    if audit_tenant is not None:  # audit_logs.tenant_id is NOT NULL
        await create_audit_log(db=db, entity_type="user", entity_id=current_user.id,
                               action="password_changed", tenant_id=audit_tenant,
                               user_id=current_user.id, changes=None)
    await db.commit()
    return TokenOnly(access_token=security.issue_access_token(current_user, active))


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
    security.bump_token_version(user)
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
