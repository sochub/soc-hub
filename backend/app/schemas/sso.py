from typing import Optional
from pydantic import BaseModel, field_validator


class SSOConfigUpdate(BaseModel):
    enabled: Optional[bool] = None
    idp_entity_id: Optional[str] = None
    idp_sso_url: Optional[str] = None
    idp_x509_cert: Optional[str] = None
    auto_provision: Optional[bool] = None
    default_role: Optional[str] = None

    @field_validator("default_role")
    @classmethod
    def _role_ok(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in ("analyst", "viewer"):
            raise ValueError("default_role must be 'analyst' or 'viewer'")
        return v


class SSOConfigOut(BaseModel):
    enabled: bool
    idp_entity_id: Optional[str] = None
    idp_sso_url: Optional[str] = None
    idp_x509_cert: Optional[str] = None
    auto_provision: bool
    default_role: str
    # Service-provider values the admin pastes into their IdP
    sp_entity_id: str
    sp_acs_url: str
    sp_metadata_url: str
    sp_login_url: str
