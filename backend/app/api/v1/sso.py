import secrets
from typing import Any, Optional
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from app.core import security
from app.core.config import settings
from app.models.membership import TenantMembership
from app.models.tenant import Tenant
from app.models.tenant_sso_config import TenantSSOConfig
from app.models.user import User
from app.schemas.sso import SSOConfigOut, SSOConfigUpdate
from app.services import saml_service
from app.utils.audit import create_audit_log

# Public SAML SP endpoints (mounted under /auth)
saml_router = APIRouter()
# Tenant-admin config endpoints (mounted under /tenants, registered before
# tenants.router so /sso-config isn't swallowed by /{tenant_id})
sso_admin_router = APIRouter()

_ALLOWED_JIT_ROLES = {"analyst", "viewer"}


def _error_redirect(message: str) -> RedirectResponse:
    base = settings.PUBLIC_BASE_URL.rstrip("/")
    return RedirectResponse(f"{base}/login#sso_error={quote(message)}", status_code=303)


async def _tenant_and_config(db: AsyncSession, slug: str):
    tenant = (await db.execute(
        select(Tenant).where(Tenant.slug == slug, Tenant.is_active == True)  # noqa: E712
    )).scalars().first()
    if not tenant:
        return None, None
    cfg = (await db.execute(
        select(TenantSSOConfig).where(TenantSSOConfig.tenant_id == tenant.id)
    )).scalars().first()
    return tenant, cfg


def _sso_ready(cfg: Optional[TenantSSOConfig]) -> bool:
    return bool(cfg and cfg.enabled and cfg.idp_entity_id and cfg.idp_sso_url and cfg.idp_x509_cert)


async def _resolve_sso_user(
    db: AsyncSession, tenant: Tenant, cfg: TenantSSOConfig, email: str, full_name: str
) -> User:
    """Find-or-provision the SSO user per the tenant's JIT settings.
    Raises HTTPException(403) when the user/membership is missing and
    auto-provisioning is disabled."""
    email = email.strip().lower()
    role = cfg.default_role if cfg.default_role in _ALLOWED_JIT_ROLES else "viewer"

    user = (await db.execute(select(User).where(User.email == email))).scalars().first()
    if user and not user.is_active:
        raise HTTPException(status_code=403, detail="Your account is deactivated.")

    if not user:
        if not cfg.auto_provision:
            raise HTTPException(
                status_code=403,
                detail="No account for this email. Ask your admin for an invitation.",
            )
        user = User(
            email=email,
            # Unusable random password: SSO-provisioned users sign in via the IdP.
            hashed_password=security.get_password_hash(secrets.token_urlsafe(32)),
            full_name=full_name or email.split("@")[0],
            is_active=True,
            is_super_admin=False,
        )
        db.add(user)
        await db.flush()
        db.add(TenantMembership(user_id=user.id, tenant_id=tenant.id, role=role))
        await create_audit_log(db=db, entity_type="user", entity_id=user.id,
                               action="sso_provisioned", tenant_id=tenant.id,
                               user_id=user.id, changes={"role": role})
        await db.commit()
        return user

    membership = (await db.execute(
        select(TenantMembership).where(
            TenantMembership.user_id == user.id, TenantMembership.tenant_id == tenant.id)
    )).scalars().first()
    if not membership:
        if not cfg.auto_provision:
            raise HTTPException(
                status_code=403,
                detail="Your account is not a member of this tenant.",
            )
        db.add(TenantMembership(user_id=user.id, tenant_id=tenant.id, role=role))
        await create_audit_log(db=db, entity_type="user", entity_id=user.id,
                               action="sso_membership_added", tenant_id=tenant.id,
                               user_id=user.id, changes={"role": role})
        await db.commit()
    return user


@saml_router.get("/saml/{slug}/metadata")
async def saml_metadata(*, db: AsyncSession = Depends(deps.get_db), slug: str) -> Any:
    """SP metadata XML for the tenant — paste/import into the IdP. Public."""
    tenant, cfg = await _tenant_and_config(db, slug)
    if not tenant or not cfg:
        raise HTTPException(status_code=404, detail="SSO is not configured for this tenant.")
    xml = saml_service.sp_metadata_xml(slug, cfg)
    return Response(content=xml, media_type="application/xml")


@saml_router.get("/saml/{slug}/login")
async def saml_login(*, db: AsyncSession = Depends(deps.get_db), slug: str) -> Any:
    """SP-initiated login: redirect the browser to the tenant's IdP. Public."""
    tenant, cfg = await _tenant_and_config(db, slug)
    if not tenant or not _sso_ready(cfg):
        return _error_redirect("SSO is not enabled for this tenant.")
    req = saml_service.build_request_dict({}, {}, f"/api/v1/auth/saml/{slug}/login")
    auth = saml_service.make_auth(slug, cfg, req)
    return RedirectResponse(auth.login(), status_code=303)


@saml_router.post("/saml/{slug}/acs")
async def saml_acs(*, request: Request, db: AsyncSession = Depends(deps.get_db), slug: str) -> Any:
    """Assertion Consumer Service: validate the IdP response, sign the user in,
    and bounce back to the frontend with the JWT in the URL fragment. Public."""
    tenant, cfg = await _tenant_and_config(db, slug)
    if not tenant or not _sso_ready(cfg):
        return _error_redirect("SSO is not enabled for this tenant.")

    form = await request.form()
    req = saml_service.build_request_dict(
        {k: v for k, v in form.items()}, {}, f"/api/v1/auth/saml/{slug}/acs"
    )
    auth = saml_service.make_auth(slug, cfg, req)
    auth.process_response()
    if auth.get_errors() or not auth.is_authenticated():
        reason = auth.get_last_error_reason() or ", ".join(auth.get_errors()) or "invalid SAML response"
        return _error_redirect(f"SSO sign-in failed: {reason}")

    email = (auth.get_nameid() or "").strip()
    attrs = auth.get_attributes() or {}
    if "@" not in email:
        for key in ("email", "mail", "urn:oid:0.9.2342.19200300.100.1.3",
                    "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/emailaddress"):
            vals = attrs.get(key) or []
            if vals and "@" in vals[0]:
                email = vals[0].strip()
                break
    if "@" not in email:
        return _error_redirect("SSO response did not include an email address.")

    full_name = ""
    for key in ("displayName", "cn", "name",
                "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/name"):
        vals = attrs.get(key) or []
        if vals and vals[0].strip():
            full_name = vals[0].strip()
            break

    try:
        user = await _resolve_sso_user(db, tenant, cfg, email, full_name)
    except HTTPException as exc:
        return _error_redirect(exc.detail)

    await create_audit_log(db=db, entity_type="user", entity_id=user.id, action="sso_login",
                           tenant_id=tenant.id, user_id=user.id)
    await db.commit()

    token = security.create_access_token({"sub": user.email, "active_tenant_id": tenant.id})
    base = settings.PUBLIC_BASE_URL.rstrip("/")
    return RedirectResponse(f"{base}/login#sso_token={quote(token)}", status_code=303)


def _config_out(slug: str, cfg: Optional[TenantSSOConfig]) -> SSOConfigOut:
    urls = saml_service.sp_urls(slug)
    return SSOConfigOut(
        enabled=bool(cfg and cfg.enabled),
        idp_entity_id=cfg.idp_entity_id if cfg else None,
        idp_sso_url=cfg.idp_sso_url if cfg else None,
        idp_x509_cert=cfg.idp_x509_cert if cfg else None,
        auto_provision=bool(cfg and cfg.auto_provision),
        default_role=(cfg.default_role if cfg else "viewer"),
        sp_entity_id=urls["entity_id"],
        sp_acs_url=urls["acs_url"],
        sp_metadata_url=urls["metadata_url"],
        sp_login_url=urls["login_url"],
    )


@sso_admin_router.get("/sso-config", response_model=SSOConfigOut)
async def get_sso_config(
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.require_admin),
    tenant_id: int = Depends(deps.get_effective_tenant_id),
) -> Any:
    """The active tenant's SSO configuration plus SP values for the IdP side."""
    tenant = (await db.execute(select(Tenant).where(Tenant.id == tenant_id))).scalars().first()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    cfg = (await db.execute(
        select(TenantSSOConfig).where(TenantSSOConfig.tenant_id == tenant_id)
    )).scalars().first()
    return _config_out(tenant.slug, cfg)


@sso_admin_router.put("/sso-config", response_model=SSOConfigOut)
async def update_sso_config(
    *,
    db: AsyncSession = Depends(deps.get_db),
    body: SSOConfigUpdate,
    current_user: User = Depends(deps.require_admin),
    tenant_id: int = Depends(deps.get_effective_tenant_id),
) -> Any:
    """Create/update the active tenant's SSO configuration. Admin only."""
    tenant = (await db.execute(select(Tenant).where(Tenant.id == tenant_id))).scalars().first()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    cfg = (await db.execute(
        select(TenantSSOConfig).where(TenantSSOConfig.tenant_id == tenant_id)
    )).scalars().first()
    if not cfg:
        cfg = TenantSSOConfig(tenant_id=tenant_id)
        db.add(cfg)

    for field in ("enabled", "idp_entity_id", "idp_sso_url", "idp_x509_cert",
                  "auto_provision", "default_role"):
        value = getattr(body, field)
        if value is not None:
            setattr(cfg, field, value)

    if cfg.enabled and not (cfg.idp_entity_id and cfg.idp_sso_url and cfg.idp_x509_cert):
        raise HTTPException(
            status_code=400,
            detail="To enable SSO, provide the IdP entity ID, SSO URL, and x509 certificate.",
        )

    await create_audit_log(db=db, entity_type="tenant", entity_id=tenant_id,
                           action="sso_config_updated", tenant_id=tenant_id,
                           user_id=current_user.id,
                           changes={"enabled": cfg.enabled, "auto_provision": cfg.auto_provision})
    await db.commit()
    await db.refresh(cfg)
    return _config_out(tenant.slug, cfg)
