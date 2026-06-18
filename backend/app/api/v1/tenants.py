import secrets
from typing import Any, List
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.api import deps
from app.models.membership import TenantMembership
from app.models.tenant import Tenant
from app.models.user import User
from app.schemas import tenant as tenant_schema
from app.schemas.user import User as UserSchema

router = APIRouter()


def generate_webhook_key() -> str:
    """Generate a high-entropy, URL-safe webhook API key for a tenant."""
    return f"whk_{secrets.token_urlsafe(32)}"

@router.post("/", response_model=tenant_schema.Tenant, status_code=201)
async def create_tenant(
    *,
    db: AsyncSession = Depends(deps.get_db),
    tenant_in: tenant_schema.TenantCreate,
    current_user: User = Depends(deps.require_super_admin),
) -> Any:
    """Create a new tenant. Super admin only."""
    result = await db.execute(select(Tenant).where(Tenant.slug == tenant_in.slug))
    if result.scalars().first():
        raise HTTPException(status_code=409, detail="A tenant with this slug already exists.")

    tenant = Tenant(**tenant_in.model_dump())
    db.add(tenant)
    await db.commit()
    await db.refresh(tenant)
    return tenant


@router.get("/", response_model=List[tenant_schema.Tenant])
async def read_tenants(
    db: AsyncSession = Depends(deps.get_db),
    skip: int = 0,
    limit: int = 100,
    current_user: User = Depends(deps.require_super_admin),
) -> Any:
    """List all tenants. Super admin only."""
    result = await db.execute(select(Tenant).offset(skip).limit(limit))
    return result.scalars().all()

@router.get("/{tenant_id}", response_model=tenant_schema.Tenant)
async def read_tenant(
    *,
    db: AsyncSession = Depends(deps.get_db),
    tenant_id: int,
    current_user: User = Depends(deps.require_super_admin),
) -> Any:
    """Get tenant by ID. Super admin only."""
    result = await db.execute(select(Tenant).where(Tenant.id == tenant_id))
    tenant = result.scalars().first()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    return tenant

@router.put("/{tenant_id}", response_model=tenant_schema.Tenant)
async def update_tenant(
    *,
    db: AsyncSession = Depends(deps.get_db),
    tenant_id: int,
    tenant_in: tenant_schema.TenantUpdate,
    current_user: User = Depends(deps.require_super_admin),
) -> Any:
    """Update a tenant. Super admin only."""
    result = await db.execute(select(Tenant).where(Tenant.id == tenant_id))
    tenant = result.scalars().first()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    update_data = tenant_in.model_dump(exclude_unset=True)

    if "slug" in update_data and update_data["slug"] != tenant.slug:
        slug_check = await db.execute(select(Tenant).where(Tenant.slug == update_data["slug"]))
        if slug_check.scalars().first():
            raise HTTPException(status_code=409, detail="A tenant with this slug already exists.")

    for field, value in update_data.items():
        setattr(tenant, field, value)

    await db.commit()
    await db.refresh(tenant)
    return tenant

@router.delete("/{tenant_id}", response_model=tenant_schema.Tenant)
async def deactivate_tenant(
    *,
    db: AsyncSession = Depends(deps.get_db),
    tenant_id: int,
    current_user: User = Depends(deps.require_super_admin),
) -> Any:
    """Soft deactivate a tenant. Super admin only."""
    result = await db.execute(select(Tenant).where(Tenant.id == tenant_id))
    tenant = result.scalars().first()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    tenant.is_active = False
    await db.commit()
    await db.refresh(tenant)
    return tenant

@router.get("/{tenant_id}/users", response_model=List[UserSchema])
async def read_tenant_users(
    *,
    db: AsyncSession = Depends(deps.get_db),
    tenant_id: int,
    current_user: User = Depends(deps.require_super_admin),
) -> Any:
    """List users in a tenant. Super admin only."""
    result = await db.execute(select(Tenant).where(Tenant.id == tenant_id))
    if not result.scalars().first():
        raise HTTPException(status_code=404, detail="Tenant not found")

    rows = await db.execute(
        select(User, TenantMembership.role)
        .join(TenantMembership, TenantMembership.user_id == User.id)
        .where(TenantMembership.tenant_id == tenant_id)
    )
    return [
        UserSchema(
            id=u.id, email=u.email, full_name=u.full_name,
            is_active=u.is_active, is_super_admin=u.is_super_admin, role=role,
        )
        for u, role in rows.all()
    ]
