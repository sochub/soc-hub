import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.api import deps
from app.core import security
from app.core.config import settings
from app.models.invitation import Invitation, InvitationStatus
from app.models.membership import TenantMembership
from app.models.tenant import Tenant
from app.models.user import User, UserRole
from app.schemas import invitation as invitation_schema
from app.services.email_service import send_invitation_email

router = APIRouter()


@router.post("/", response_model=invitation_schema.InvitationResponse, status_code=201)
async def create_invitation(
    *,
    db: AsyncSession = Depends(deps.get_db),
    invitation_in: invitation_schema.InvitationCreate,
    current_user: User = Depends(deps.require_admin),
    tenant_id: int = Depends(deps.get_effective_tenant_id),
) -> Any:
    """Add a user to the active tenant. Admin only.

    Existing users are added directly as a membership (no token). New users get a
    token-based invitation to complete signup.
    """
    if invitation_in.role == UserRole.SUPER_ADMIN:
        raise HTTPException(status_code=400, detail="Cannot invite super admin users.")

    # Existing user → create a membership directly, no token needed.
    result = await db.execute(select(User).where(User.email == invitation_in.email))
    existing_user = result.scalars().first()
    if existing_user:
        dup = await db.execute(
            select(TenantMembership).where(
                TenantMembership.user_id == existing_user.id,
                TenantMembership.tenant_id == tenant_id,
            )
        )
        if dup.scalars().first():
            raise HTTPException(status_code=409, detail="User is already a member of this tenant.")
        db.add(TenantMembership(
            user_id=existing_user.id, tenant_id=tenant_id, role=invitation_in.role.value
        ))
        await db.commit()
        return invitation_schema.InvitationResponse(
            id=0,
            email=invitation_in.email,
            tenant_id=tenant_id,
            role=invitation_in.role.value,
            token="",
            status="added_directly",
            added_directly=True,
            created_at=datetime.now(timezone.utc),
            expires_at=datetime.now(timezone.utc),
            invite_link=None,
        )

    # Check for existing pending invitation
    result = await db.execute(
        select(Invitation).where(
            Invitation.email == invitation_in.email,
            Invitation.tenant_id == tenant_id,
            Invitation.status == InvitationStatus.PENDING,
        )
    )
    existing = result.scalars().first()
    if existing:
        raise HTTPException(status_code=409, detail="A pending invitation already exists for this email.")

    token = secrets.token_urlsafe(32)
    expires_at = datetime.now(timezone.utc) + timedelta(hours=settings.INVITATION_EXPIRE_HOURS)

    invitation = Invitation(
        email=invitation_in.email,
        tenant_id=tenant_id,
        role=invitation_in.role.value,
        token=token,
        invited_by=current_user.id,
        expires_at=expires_at,
    )
    db.add(invitation)
    await db.commit()
    await db.refresh(invitation)

    invite_link = f"{settings.FRONTEND_URL}/invite/{token}"

    # Try to send email (no-ops if SMTP not configured)
    await send_invitation_email(invitation_in.email, invite_link, tenant_id, db)

    response = invitation_schema.InvitationResponse.model_validate(invitation)
    response.invite_link = invite_link
    return response


@router.get("/", response_model=List[invitation_schema.InvitationResponse])
async def read_invitations(
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.require_admin),
    tenant_id: int = Depends(deps.get_effective_tenant_id),
) -> Any:
    """List tenant invitations. Admin only."""
    result = await db.execute(
        select(Invitation)
        .where(Invitation.tenant_id == tenant_id)
        .order_by(Invitation.created_at.desc())
    )
    invitations = result.scalars().all()
    responses = []
    for inv in invitations:
        resp = invitation_schema.InvitationResponse.model_validate(inv)
        resp.invite_link = f"{settings.FRONTEND_URL}/invite/{inv.token}"
        responses.append(resp)
    return responses


@router.delete("/{invitation_id}", status_code=204)
async def revoke_invitation(
    *,
    db: AsyncSession = Depends(deps.get_db),
    invitation_id: int,
    current_user: User = Depends(deps.require_admin),
    tenant_id: int = Depends(deps.get_effective_tenant_id),
) -> None:
    """Revoke an invitation. Admin only."""
    result = await db.execute(
        select(Invitation).where(
            Invitation.id == invitation_id,
            Invitation.tenant_id == tenant_id,
        )
    )
    invitation = result.scalars().first()
    if not invitation:
        raise HTTPException(status_code=404, detail="Invitation not found")

    invitation.status = InvitationStatus.REVOKED
    await db.commit()


@router.post("/{invitation_id}/resend", response_model=invitation_schema.InvitationResponse)
async def resend_invitation(
    *,
    db: AsyncSession = Depends(deps.get_db),
    invitation_id: int,
    current_user: User = Depends(deps.require_admin),
    tenant_id: int = Depends(deps.get_effective_tenant_id),
) -> Any:
    """Resend an invitation: generates a new token, resets expiry, and re-sends email. Admin only."""
    result = await db.execute(
        select(Invitation).where(
            Invitation.id == invitation_id,
            Invitation.tenant_id == tenant_id,
        )
    )
    invitation = result.scalars().first()
    if not invitation:
        raise HTTPException(status_code=404, detail="Invitation not found")

    if invitation.status == InvitationStatus.ACCEPTED:
        raise HTTPException(status_code=400, detail="This invitation has already been accepted.")

    # Reset token, expiry, and status
    invitation.token = secrets.token_urlsafe(32)
    invitation.expires_at = datetime.now(timezone.utc) + timedelta(hours=settings.INVITATION_EXPIRE_HOURS)
    invitation.status = InvitationStatus.PENDING
    await db.commit()
    await db.refresh(invitation)

    invite_link = f"{settings.FRONTEND_URL}/invite/{invitation.token}"
    await send_invitation_email(invitation.email, invite_link, tenant_id, db)

    response = invitation_schema.InvitationResponse.model_validate(invitation)
    response.invite_link = invite_link
    return response


@router.get("/validate/{token}", response_model=invitation_schema.InvitationValidation)
async def validate_invitation(
    *,
    db: AsyncSession = Depends(deps.get_db),
    token: str,
) -> Any:
    """Validate an invitation token. Public endpoint, no auth required."""
    result = await db.execute(select(Invitation).where(Invitation.token == token))
    invitation = result.scalars().first()

    if not invitation:
        return invitation_schema.InvitationValidation(
            email="", tenant_name="", role="", valid=False
        )

    if invitation.status != InvitationStatus.PENDING:
        return invitation_schema.InvitationValidation(
            email=invitation.email, tenant_name="", role=invitation.role, valid=False
        )

    if invitation.expires_at.replace(tzinfo=timezone.utc) < datetime.now(timezone.utc):
        invitation.status = InvitationStatus.EXPIRED
        await db.commit()
        return invitation_schema.InvitationValidation(
            email=invitation.email, tenant_name="", role=invitation.role, valid=False
        )

    # Get tenant name
    result = await db.execute(select(Tenant).where(Tenant.id == invitation.tenant_id))
    tenant = result.scalars().first()

    return invitation_schema.InvitationValidation(
        email=invitation.email,
        tenant_name=tenant.name if tenant else "",
        role=invitation.role,
        valid=True,
    )


@router.post("/accept", response_model=dict)
async def accept_invitation(
    *,
    db: AsyncSession = Depends(deps.get_db),
    accept_in: invitation_schema.InvitationAccept,
) -> Any:
    """Accept an invitation and create a user account. Public endpoint, no auth required."""
    result = await db.execute(select(Invitation).where(Invitation.token == accept_in.token))
    invitation = result.scalars().first()

    if not invitation:
        raise HTTPException(status_code=404, detail="Invalid invitation token.")

    if invitation.status != InvitationStatus.PENDING:
        raise HTTPException(status_code=400, detail="This invitation is no longer valid.")

    if invitation.expires_at.replace(tzinfo=timezone.utc) < datetime.now(timezone.utc):
        invitation.status = InvitationStatus.EXPIRED
        await db.commit()
        raise HTTPException(status_code=400, detail="This invitation has expired.")

    # Check if user already exists
    result = await db.execute(select(User).where(User.email == invitation.email))
    if result.scalars().first():
        raise HTTPException(status_code=409, detail="A user with this email already exists.")

    # Create the account, then add the tenant membership from the invitation.
    user = User(
        email=invitation.email,
        hashed_password=security.get_password_hash(accept_in.password),
        full_name=accept_in.full_name,
        is_active=True,
        is_super_admin=False,
    )
    db.add(user)
    await db.flush()
    db.add(TenantMembership(
        user_id=user.id, tenant_id=invitation.tenant_id, role=invitation.role
    ))

    invitation.status = InvitationStatus.ACCEPTED
    await db.commit()

    return {"message": "Account created successfully. You can now log in."}
