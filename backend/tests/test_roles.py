from app.utils.roles import resolve_active_role


class FakeMembership:
    def __init__(self, tenant_id, role):
        self.tenant_id = tenant_id
        self.role = role


def test_super_admin_always_super_admin():
    assert resolve_active_role(True, 5, []) == "super_admin"


def test_member_role_for_active_tenant():
    ms = [FakeMembership(1, "admin"), FakeMembership(2, "viewer")]
    assert resolve_active_role(False, 2, ms) == "viewer"


def test_non_member_returns_none():
    ms = [FakeMembership(1, "admin")]
    assert resolve_active_role(False, 99, ms) is None


def test_no_active_tenant_returns_none():
    ms = [FakeMembership(1, "admin")]
    assert resolve_active_role(False, None, ms) is None
