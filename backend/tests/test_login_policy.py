import pytest

from app.auth.login_policy import (
    member_may_enter,
    is_password_fallback,
    password_required_error,
    sso_required_error,
)


def _may(method, amr, sso=None, tenant=1, role="analyst", mfa=False, sa=False):
    return member_may_enter(
        method, amr=amr, sso_tenant_id=sso, tenant_id=tenant,
        role=role, mfa_enabled=mfa, is_super_admin=sa,
    )


@pytest.mark.parametrize(
    "kwargs,expected",
    [
        (dict(method="password", amr="pwd"), True),
        (dict(method="password", amr="saml", sso=1), True),
        (dict(method="password", amr="saml", sso=2), False),
        (dict(method="saml", amr="saml", sso=1), True),
        (dict(method="saml", amr="saml", sso=2), False),
        (dict(method="saml", amr="oidc", sso=1), False),
        (dict(method="oidc", amr="oidc", sso=1), True),
        (dict(method="oidc", amr="pwd", role="analyst"), False),
        (dict(method="oidc", amr="pwd", role="admin", mfa=True), True),
        (dict(method="oidc", amr="pwd", role="admin", mfa=False), False),
        (dict(method="saml", amr="pwd", role="analyst", sa=True), True),
    ],
)
def test_member_may_enter(kwargs, expected):
    assert _may(**kwargs) is expected


def test_is_password_fallback():
    assert is_password_fallback("saml", "pwd")
    assert not is_password_fallback("password", "pwd")
    assert not is_password_fallback("saml", "saml")


def test_errors():
    e = password_required_error()
    assert e.status_code == 403 and e.detail == {"code": "password_required"}
    e = sso_required_error("acme")
    assert e.status_code == 403 and e.detail == {"code": "sso_required", "slug": "acme"}
