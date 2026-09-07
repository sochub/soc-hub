import secrets
from typing import Any, List
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.api import deps
from app.models.membership import TenantMembership
from app.models.tenant import Tenant
from app.models.user import User
from app.models.sla_policy import SLAPolicy
from app.schemas import tenant as tenant_schema
from app.schemas.sla_policy import SLAPolicyItem, SLAPolicyUpdate
from app.schemas.user import User as UserSchema
from app.utils.sla import DEFAULT_SLA_MINUTES
from app.utils.tenant_deletion import delete_tenant_cascade

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

@router.get("/sla-policies", response_model=List[SLAPolicyItem])
async def get_sla_policies(
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.require_admin),
    tenant_id: int = Depends(deps.get_effective_tenant_id),
) -> Any:
    """Effective SLA targets for every severity — the tenant's override
    where one exists, else the hardcoded default. Admin only."""
    rows = (await db.execute(select(SLAPolicy).where(SLAPolicy.tenant_id == tenant_id))).scalars().all()
    overrides = {r.severity: r for r in rows}
    return [
        SLAPolicyItem(
            severity=severity,
            response_target_minutes=(
                overrides[severity].response_target_minutes if severity in overrides else defaults["response"]
            ),
            resolution_target_minutes=(
                overrides[severity].resolution_target_minutes if severity in overrides else defaults["resolution"]
            ),
        )
        for severity, defaults in DEFAULT_SLA_MINUTES.items()
    ]


@router.put("/sla-policies", response_model=List[SLAPolicyItem])
async def update_sla_policies(
    *,
    db: AsyncSession = Depends(deps.get_db),
    body: SLAPolicyUpdate,
    current_user: User = Depends(deps.require_admin),
    tenant_id: int = Depends(deps.get_effective_tenant_id),
) -> Any:
    """Upsert the tenant's SLA overrides. Admin only."""
    existing = (await db.execute(select(SLAPolicy).where(SLAPolicy.tenant_id == tenant_id))).scalars().all()
    by_severity = {r.severity: r for r in existing}

    for item in body.policies:
        if item.severity not in DEFAULT_SLA_MINUTES:
            raise HTTPException(status_code=400, detail=f"Unknown severity: {item.severity}")
        row = by_severity.get(item.severity)
        if row:
            row.response_target_minutes = item.response_target_minutes
            row.resolution_target_minutes = item.resolution_target_minutes
        else:
            db.add(SLAPolicy(
                tenant_id=tenant_id, severity=item.severity,
                response_target_minutes=item.response_target_minutes,
                resolution_target_minutes=item.resolution_target_minutes,
            ))

    await db.commit()
    return await get_sla_policies(db=db, current_user=current_user, tenant_id=tenant_id)


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

@router.delete("/{tenant_id}/permanent", status_code=204)
async def delete_tenant_permanently(
    *,
    db: AsyncSession = Depends(deps.get_db),
    tenant_id: int,
    current_user: User = Depends(deps.require_super_admin),
) -> None:
    """Permanently delete a deactivated tenant and all of its data. Super
    admin only. Irreversible — the tenant must already be deactivated."""
    result = await db.execute(select(Tenant).where(Tenant.id == tenant_id))
    tenant = result.scalars().first()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    if tenant.is_active:
        raise HTTPException(status_code=400, detail="Deactivate the tenant before deleting it permanently.")

    await delete_tenant_cascade(db, tenant)
    await db.commit()

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
