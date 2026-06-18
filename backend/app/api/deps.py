from typing import Optional
from fastapi import Depends, HTTPException, Query, Security, status
from fastapi.security import APIKeyHeader, OAuth2PasswordBearer
from jose import jwt, JWTError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.db.session import get_db
from app.models.tenant import Tenant
from app.models.user import User
from app.models.webhook import Webhook
from app.schemas import user as user_schema
from app.utils.roles import resolve_active_role

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login/access-token")


async def get_current_user(
    db: AsyncSession = Depends(get_db), token: str = Depends(oauth2_scheme)
) -> User:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        email: str = payload.get("sub")
        if email is None:
            raise credentials_exception
        token_data = user_schema.TokenData(
            email=email, active_tenant_id=payload.get("active_tenant_id")
        )
    except JWTError:
        raise credentials_exception

    result = await db.execute(
        select(User).options(selectinload(User.memberships)).where(User.email == token_data.email)
    )
    user = result.scalars().first()
    if user is None:
        raise credentials_exception
    # Transient, request-scoped: the active tenant chosen for this token.
    user._active_tenant_id = token_data.active_tenant_id
    return user


def get_current_active_user(
    current_user: User = Depends(get_current_user),
) -> User:
    if not current_user.is_active:
        raise HTTPException(status_code=400, detail="Inactive user")
    return current_user


def _active_role(current_user: User) -> Optional[str]:
    """Resolve the caller's role string in their active tenant ('super_admin',
    'admin', 'analyst', 'viewer', or None)."""
    active = getattr(current_user, "_active_tenant_id", None)
    return resolve_active_role(current_user.is_super_admin, active, current_user.memberships)


def require_admin(current_user: User = Depends(get_current_active_user)) -> User:
    if _active_role(current_user) in ("super_admin", "admin"):
        return current_user
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions"
    )


def require_analyst_or_above(current_user: User = Depends(get_current_active_user)) -> User:
    if _active_role(current_user) in ("super_admin", "admin", "analyst"):
        return current_user
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions"
    )


def require_super_admin(current_user: User = Depends(get_current_active_user)) -> User:
    """Strict super-admin-only check."""
    if not current_user.is_super_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Super admin access required"
        )
    return current_user


def get_effective_tenant_id(
    current_user: User = Depends(get_current_active_user),
    tenant_id: Optional[int] = Query(None),
) -> int:
    """Resolve the tenant to operate in.

    Super admins use the active tenant from the token, with an optional
    ``?tenant_id=`` override. Regular users use the active tenant from the token,
    which must be one of their memberships.
    """
    active = getattr(current_user, "_active_tenant_id", None)

    if current_user.is_super_admin:
        chosen = tenant_id if tenant_id is not None else active
        if chosen is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Super admin must select a tenant.",
            )
        return chosen

    if active is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No active tenant selected.",
        )
    if not any(m.tenant_id == active for m in current_user.memberships):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not a member of the active tenant.",
        )
    return active


# Named so the OpenAPI reference shows the webhook auth as an API key in the
# X-API-Key header. auto_error=False keeps our own 401 below (a missing header
# returns 401, matching the invalid-key behavior verify_webhooks.py asserts).
webhook_api_key_scheme = APIKeyHeader(name="X-API-Key", auto_error=False, scheme_name="WebhookApiKey")


async def get_webhook_from_key(
    db: AsyncSession = Depends(get_db),
    x_api_key: str | None = Security(webhook_api_key_scheme),
) -> Webhook:
    """Resolve the webhook that owns the supplied API key.

    Each webhook belongs to one tenant; the key alone determines both the
    destination tenant and the alert's source name. The owning tenant must be
    active.
    """
    if not x_api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key",
        )
    result = await db.execute(
        select(Webhook)
        .join(Tenant, Tenant.id == Webhook.tenant_id)
        .where(
            Webhook.api_key == x_api_key,
            Tenant.is_active == True,  # noqa: E712
        )
    )
    webhook = result.scalars().first()
    if webhook is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key",
        )
    return webhook
