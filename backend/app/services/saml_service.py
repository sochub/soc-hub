"""SAML SP helpers built on python3-saml (OneLogin).

Each tenant has its own IdP settings (TenantSSOConfig); the SP side is derived
from settings.PUBLIC_BASE_URL and the tenant slug.
"""
from typing import Any, Dict
from urllib.parse import urlparse

from onelogin.saml2.auth import OneLogin_Saml2_Auth
from onelogin.saml2.settings import OneLogin_Saml2_Settings

from app.core.config import settings
from app.models.tenant_sso_config import TenantSSOConfig


def sp_urls(slug: str) -> Dict[str, str]:
    base = settings.PUBLIC_BASE_URL.rstrip("/")
    return {
        "entity_id": f"{base}/api/v1/auth/saml/{slug}/metadata",
        "acs_url": f"{base}/api/v1/auth/saml/{slug}/acs",
        "metadata_url": f"{base}/api/v1/auth/saml/{slug}/metadata",
        "login_url": f"{base}/api/v1/auth/saml/{slug}/login",
    }


def build_saml_settings(slug: str, cfg: TenantSSOConfig) -> Dict[str, Any]:
    urls = sp_urls(slug)
    return {
        "strict": True,
        "debug": False,
        "sp": {
            "entityId": urls["entity_id"],
            "assertionConsumerService": {
                "url": urls["acs_url"],
                "binding": "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST",
            },
            "NameIDFormat": "urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress",
        },
        "idp": {
            "entityId": cfg.idp_entity_id or "",
            "singleSignOnService": {
                "url": cfg.idp_sso_url or "",
                "binding": "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect",
            },
            "x509cert": cfg.idp_x509_cert or "",
        },
        "security": {
            "requestedAuthnContext": False,
            "wantAssertionsSigned": False,  # response signature still required by strict mode
            "wantMessagesSigned": False,
        },
    }


def build_request_dict(form_data: Dict[str, str], query: Dict[str, str], path: str) -> Dict[str, Any]:
    """python3-saml expects a Django/WSGI-style request dict. We derive host and
    scheme from PUBLIC_BASE_URL (the app sits behind nginx, so the incoming
    request's host header is not the externally visible one)."""
    parsed = urlparse(settings.PUBLIC_BASE_URL)
    https = "on" if parsed.scheme == "https" else "off"
    host = parsed.netloc or "localhost"
    return {
        "https": https,
        "http_host": host,
        "script_name": path,
        "server_port": parsed.port or (443 if https == "on" else 80),
        "get_data": dict(query),
        "post_data": dict(form_data),
    }


def make_auth(slug: str, cfg: TenantSSOConfig, request_dict: Dict[str, Any]) -> OneLogin_Saml2_Auth:
    return OneLogin_Saml2_Auth(request_dict, build_saml_settings(slug, cfg))


def sp_metadata_xml(slug: str, cfg: TenantSSOConfig) -> str:
    saml_settings = OneLogin_Saml2_Settings(
        build_saml_settings(slug, cfg), sp_validation_only=True
    )
    metadata = saml_settings.get_sp_metadata()
    errors = saml_settings.validate_metadata(metadata)
    if errors:
        raise ValueError(f"Invalid SP metadata: {', '.join(errors)}")
    return metadata if isinstance(metadata, str) else metadata.decode()
