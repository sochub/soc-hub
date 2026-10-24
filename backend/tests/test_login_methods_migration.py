import importlib.util
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "3eda39884bf3_login_methods_oidc.py"


def _load():
    spec = importlib.util.spec_from_file_location("login_methods_migration", _PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize("guest_sso,saml,expected", [
    (False, False, ("password", "password")),
    (True, True, ("saml", "saml")),
    (False, True, ("saml", "password")),
    (True, False, ("password", "saml")),
])
def test_backfill_mapping(guest_sso, saml, expected):
    assert _load().backfill_values(guest_sso, saml) == expected
