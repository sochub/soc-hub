import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, List

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func
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
from app.utils.account_guard import assert_super_admin_may_bootstrap, deny
from app.utils.emails import normalize_email

router = APIRouter()

CANNOT_ADD_EXISTING = ("This account signs in through another organization's single sign-on and can't be "
                       "added to this tenant.")


async def _has_members(db: AsyncSession, tenant_id: int) -> bool:
    return (await db.execute(select(TenantMembership.id).where(
        TenantMembership.tenant_id == tenant_id).limit(1))).first() is not None


@router.post("/", response_model=invitation_schema.InvitationResponse, status_code=201)
async def create_invitation(
    *,
    request: Request,
    db: AsyncSession = Depends(deps.get_db),
    invitation_in: invitation_schema.InvitationCreate,
    current_user: User = Depends(deps.require_admin),
    tenant_id: int = Depends(deps.get_effective_tenant_id),
) -> Any:
    """Invite a user to the active tenant. Admin only.

    New users get a token invitation to complete signup. Existing users get the same kind of token invitation
    and join only by accepting it while signed in as that account (POST /invitations/accept-existing), so no
    tenant can attach itself to someone else's account. The one exception adds an existing user directly:
    bootstrapping a tenant with no members yet (its first admin).

    A super admin only bootstraps: while the tenant has no active admin they may add or invite its admin;
    after that, inviting is the tenant admin's job (app.utils.account_guard).
    """
    if invitation_in.role == UserRole.SUPER_ADMIN:
        raise HTTPException(status_code=400, detail="Cannot invite super admin users.")
    await assert_super_admin_may_bootstrap(db, current_user, tenant_id, action="invite",
                                           role=invitation_in.role.value, request=request)

    # FOR UPDATE: account-wide actions lock the same row, so "is this account owned by one tenant only?" can't
    # race a membership being added here (app.utils.account_guard).
    result = await db.execute(select(User).where(func.lower(User.email) == invitation_in.email).with_for_update())
    existing_user = result.scalars().first()
    if existing_user and existing_user.is_super_admin and not current_user.is_super_admin:
        # Break-glass accounts are managed by super admins only. Same answer as another account that can't be
        # added, so it reveals nothing about the account.
        await deny(request, current_user, tenant_id, "invite", target_id=None,
                   detail=CANNOT_ADD_EXISTING, status_code=409)
    if existing_user:
        dup = await db.execute(
            select(TenantMembership).where(
                TenantMembership.user_id == existing_user.id,
                TenantMembership.tenant_id == tenant_id,
            )
        )
        if dup.scalars().first():
            raise HTTPException(status_code=409, detail="User is already a member of this tenant.")
        if not existing_user.password_login_enabled:
            # SSO-only accounts may belong to a single tenant (SSO refuses multi-tenant users),
            # so adding one elsewhere would lock them out of their own organization.
            raise HTTPException(status_code=409, detail=CANNOT_ADD_EXISTING)
    if existing_user and (current_user.is_super_admin or not await _has_members(db, tenant_id)):
        # (a super admin got here only while the tenant has no active admin: assert_super_admin_may_bootstrap)
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
            func.lower(Invitation.email) == invitation_in.email,
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
    await assert_super_admin_may_bootstrap(db, current_user, tenant_id, action="invitation_revoke")
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
    await assert_super_admin_may_bootstrap(db, current_user, tenant_id, action="invitation_resend")
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
    """Validate an invitation token. Public endpoint, no auth required.

    Missing, used and expired tokens get the same empty answer, so a stale or leaked link reveals nothing;
    the invitee's email, tenant and role are shown only for a live invitation (the acceptance screen)."""
    result = await db.execute(select(Invitation).where(Invitation.token == token))
    invitation = result.scalars().first()
    invalid = invitation_schema.InvitationValidation(email="", tenant_name="", role="", valid=False)

    if not invitation or invitation.status != InvitationStatus.PENDING:
        return invalid

    if invitation.expires_at.replace(tzinfo=timezone.utc) < datetime.now(timezone.utc):
        invitation.status = InvitationStatus.EXPIRED
        await db.commit()
        return invalid

    # Get tenant name
    result = await db.execute(select(Tenant).where(Tenant.id == invitation.tenant_id))
    tenant = result.scalars().first()

    existing = (await db.execute(select(User.id).where(
        func.lower(User.email) == normalize_email(invitation.email)))).first() is not None
    return invitation_schema.InvitationValidation(
        email=invitation.email,
        tenant_name=tenant.name if tenant else "",
        role=invitation.role,
        valid=True,
        existing_account=existing,
    )


async def _live_invitation(db: AsyncSession, token: str) -> Invitation:
    invitation = (await db.execute(
        select(Invitation).where(Invitation.token == token).with_for_update())).scalars().first()
    if not invitation:
        raise HTTPException(status_code=404, detail="Invalid invitation token.")
    if invitation.status != InvitationStatus.PENDING:
        raise HTTPException(status_code=400, detail="This invitation is no longer valid.")
    if invitation.expires_at.replace(tzinfo=timezone.utc) < datetime.now(timezone.utc):
        invitation.status = InvitationStatus.EXPIRED
        await db.commit()
        raise HTTPException(status_code=400, detail="This invitation has expired.")
    return invitation


@router.post("/accept-existing", response_model=dict)
async def accept_invitation_existing(
    *,
    db: AsyncSession = Depends(deps.get_db),
    accept_in: invitation_schema.InvitationAcceptExisting,
    current_user: User = Depends(deps.get_current_active_user),
) -> Any:
    """Join the invitation's tenant as the signed-in account. The invitation must be addressed to this account's
    email: only the account owner can consent to a new membership."""
    invitation = await _live_invitation(db, accept_in.token)
    if normalize_email(invitation.email) != normalize_email(current_user.email or ""):
        raise HTTPException(status_code=403, detail="This invitation is for a different account.")
    # Same row lock as create_invitation / account-wide actions (app.utils.account_guard).
    user = (await db.execute(select(User).where(User.id == current_user.id).with_for_update())).scalars().one()
    if not user.password_login_enabled:
        raise HTTPException(status_code=409, detail=CANNOT_ADD_EXISTING)
    dup = (await db.execute(select(TenantMembership.id).where(
        TenantMembership.user_id == user.id, TenantMembership.tenant_id == invitation.tenant_id))).first()
    if dup is None:
        db.add(TenantMembership(user_id=user.id, tenant_id=invitation.tenant_id, role=invitation.role))
    invitation.status = InvitationStatus.ACCEPTED
    await db.commit()
    return {"message": "Invitation accepted.", "tenant_id": invitation.tenant_id}


@router.post("/accept", response_model=dict)
async def accept_invitation(
    *,
    db: AsyncSession = Depends(deps.get_db),
    accept_in: invitation_schema.InvitationAccept,
) -> Any:
    """Accept an invitation and create a user account. Public endpoint, no auth required.
    An invitation for an existing account is accepted signed in (accept-existing)."""
    invitation = await _live_invitation(db, accept_in.token)

    # Check if user already exists (invitations predating normalization may be mixed-case)
    email = normalize_email(invitation.email)
    result = await db.execute(select(User).where(func.lower(User.email) == email))
    if result.scalars().first():
        raise HTTPException(status_code=409, detail="A user with this email already exists.")

    # Create the account, then add the tenant membership from the invitation.
    user = User(
        email=email,
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
