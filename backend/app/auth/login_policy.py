"""Pure policy: may a member's session act in a tenant, given its login method."""
from fastapi import HTTPException

AMR_PWD, AMR_SAML, AMR_OIDC = "pwd", "saml", "oidc"
_SSO_AMRS = (AMR_SAML, AMR_OIDC)


def password_required_error() -> HTTPException:
    return HTTPException(status_code=403, detail={"code": "password_required"})


def sso_required_error(slug: str) -> HTTPException:
    return HTTPException(status_code=403, detail={"code": "sso_required", "slug": slug})


def is_password_fallback(method: str, amr: str) -> bool:
    return method != "password" and amr == AMR_PWD


def mfa_setup_blocks(require_mfa: bool | None, *, amr: str, mfa_enabled: bool, is_super_admin: bool) -> bool:
    """A tenant's "require two-factor" refuses this session: a password session of a member without MFA.
    SSO sessions are left to the IdP. The super admin (break-glass) is never refused here; its password
    sign-in already enforces the setting."""
    return bool(require_mfa) and amr == AMR_PWD and not mfa_enabled and not is_super_admin


def member_may_enter(
    method: str,
    *,
    amr: str,
    sso_tenant_id: int | None,
    tenant_id: int,
    role: str | None,
    mfa_enabled: bool,
    is_super_admin: bool,
) -> bool:
    # An SSO session only ever opens the tenant whose IdP issued it.
    if amr in _SSO_AMRS and sso_tenant_id != tenant_id:
        return False
    if method == "password":
        return True
    if amr == method:  # method is saml|oidc and equals the amr protocol
        return True
    if amr == AMR_PWD:  # break-glass password fallback
        return is_super_admin or (role == "admin" and mfa_enabled)
    return False
