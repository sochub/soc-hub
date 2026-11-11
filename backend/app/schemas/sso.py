import re
from typing import List, Optional
from urllib.parse import urlparse

from pydantic import BaseModel, field_validator

from app.models.tenant import LOGIN_METHODS

_DOMAIN_RE = re.compile(r"^(?=.{1,253}$)([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")


class SSOConfigUpdate(BaseModel):
    """Omitted (None) fields are left unchanged."""
    member_login_method: Optional[str] = None
    guest_login_method: Optional[str] = None
    idp_entity_id: Optional[str] = None
    idp_sso_url: Optional[str] = None
    idp_x509_cert: Optional[str] = None
    oidc_issuer: Optional[str] = None
    oidc_client_id: Optional[str] = None
    # Write-only: never returned. Empty string keeps the stored secret.
    oidc_client_secret: Optional[str] = None
    oidc_scopes: Optional[str] = None
    auto_provision: Optional[bool] = None
    # Email domains JIT provisioning may create accounts for; required (non-empty) while auto_provision is on.
    allowed_email_domains: Optional[List[str]] = None
    default_role: Optional[str] = None
    saml_allow_idp_initiated: Optional[bool] = None

    @field_validator("allowed_email_domains")
    @classmethod
    def _domains_ok(cls, v: Optional[List[str]]) -> Optional[List[str]]:
        if v is None:
            return v
        domains = list(dict.fromkeys(d.strip().lower().lstrip("@") for d in v if d and d.strip()))
        if len(domains) > 50:
            raise ValueError("at most 50 allowed email domains")
        bad = [d for d in domains if not _DOMAIN_RE.match(d)]
        if bad:
            raise ValueError(f"not a valid domain: {bad[0][:100]}")
        return domains

    @field_validator("default_role")
    @classmethod
    def _role_ok(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in ("analyst", "viewer"):
            raise ValueError("default_role must be 'analyst' or 'viewer'")
        return v

    @field_validator("member_login_method", "guest_login_method")
    @classmethod
    def _method_ok(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in LOGIN_METHODS:
            raise ValueError(f"login method must be one of {', '.join(LOGIN_METHODS)}")
        return v

    @field_validator("oidc_issuer")
    @classmethod
    def _issuer_ok(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        v = v.strip()
        if v and (urlparse(v).scheme != "https" or not urlparse(v).hostname):
            raise ValueError("oidc_issuer must be an https URL")
        return v

    @field_validator("oidc_client_id")
    @classmethod
    def _strip(cls, v: Optional[str]) -> Optional[str]:
        return v.strip() if v is not None else v

    @field_validator("oidc_scopes")
    @classmethod
    def _scopes_ok(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        scopes = v.split()
        if "openid" not in scopes:
            raise ValueError("oidc_scopes must include 'openid'")
        return " ".join(scopes)


class SSOConfigOut(BaseModel):
    member_login_method: str
    guest_login_method: str
    idp_entity_id: Optional[str] = None
    idp_sso_url: Optional[str] = None
    idp_x509_cert: Optional[str] = None
    oidc_issuer: Optional[str] = None
    oidc_client_id: Optional[str] = None
    oidc_scopes: str
    has_oidc_client_secret: bool
    auto_provision: bool
    allowed_email_domains: List[str] = []
    default_role: str
    saml_allow_idp_initiated: bool = False
    # Service-provider values the admin pastes into their IdP
    sp_entity_id: str
    sp_acs_url: str
    sp_metadata_url: str
    sp_login_url: str
    oidc_redirect_uri: str
