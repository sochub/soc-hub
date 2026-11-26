"""SSO role mapping: IdP groups set the member's role at each SSO sign-in (highest match, else the default role)."""
from sqlalchemy import select

from app.db.session import AsyncSessionLocal
from app.models.audit_log import AuditLog
from app.models.membership import TenantMembership
from app.models.tenant_sso_config import TenantSSOConfig
from tests.guest_fixtures import make_tenant, make_user
from tests.test_oidc_login import FakeIdP, _as_admin, _flow, _go, _member, _oidc_tenant

MAPPING = {"admin": ["SOC-Admins"], "analyst": ["SOC-Analysts"], "viewer": ["SOC-Readers"]}


async def _role(user_id, tenant_id):
    async with AsyncSessionLocal() as db:
        return (await db.execute(select(TenantMembership.role).where(
            TenantMembership.user_id == user_id, TenantMembership.tenant_id == tenant_id))).scalar_one()


async def _map(tenant_id, mapping, default_role="viewer"):
    async with AsyncSessionLocal() as db:
        cfg = (await db.execute(select(TenantSSOConfig).where(TenantSSOConfig.tenant_id == tenant_id))).scalars().one()
        cfg.role_groups, cfg.default_role = mapping, default_role
        await db.commit()


def test_login_sets_role_from_groups(monkeypatch):
    async def body(c, ids):
        async with AsyncSessionLocal() as db:
            t = await _oidc_tenant(db, ids)
            u = await _member(db, ids, "an", [t], role="viewer")
            keeper = await _member(db, ids, "keeper", [t], role="admin")  # another admin, so demotion is allowed
        idp = FakeIdP(monkeypatch)
        idp.email = u.email

        # mapping off: groups never touch the role
        idp.claims = {"groups": ["soc-admins"]}
        assert (await _flow(c, idp, t.slug)).headers["location"].endswith("#sso=ok")
        assert await _role(u.id, t.id) == "viewer"

        await _map(t.id, MAPPING, default_role="analyst")
        # highest matching role wins; names compare case-insensitively
        idp.claims = {"groups": ["soc-readers", "SOC-ADMINS"]}
        assert (await _flow(c, idp, t.slug)).headers["location"].endswith("#sso=ok")
        assert await _role(u.id, t.id) == "admin"
        idp.claims = {"groups": ["SOC-Readers"]}
        await _flow(c, idp, t.slug)
        assert await _role(u.id, t.id) == "viewer"
        # no mapped group: the default role
        idp.claims = {"groups": ["something-else"]}
        await _flow(c, idp, t.slug)
        assert await _role(u.id, t.id) == "analyst"
        # claim absent (IdP misconfigured): role unchanged
        idp.claims = {"groups": ["soc-readers"]}
        await _flow(c, idp, t.slug)
        idp.claims = {}
        await _flow(c, idp, t.slug)
        assert await _role(u.id, t.id) == "viewer"

        # the last admin is never demoted by a login
        async with AsyncSessionLocal() as db:
            await db.execute(TenantMembership.__table__.update().where(
                TenantMembership.user_id == keeper.id, TenantMembership.tenant_id == t.id).values(role="viewer"))
            await db.commit()
        idp.claims = {"groups": ["soc-admins"]}
        await _flow(c, idp, t.slug)
        assert await _role(u.id, t.id) == "admin"
        idp.claims = {"groups": ["soc-readers"]}
        assert (await _flow(c, idp, t.slug)).headers["location"].endswith("#sso=ok")  # still signs in
        assert await _role(u.id, t.id) == "admin"

        async with AsyncSessionLocal() as db:
            actions = (await db.execute(select(AuditLog.action, AuditLog.changes).where(
                AuditLog.tenant_id == t.id, AuditLog.action.like("sso_role_sync%")).order_by(AuditLog.id))).all()
        assert [a for a, _ in actions] == ["sso_role_sync"] * 5 + ["sso_role_sync_blocked"]
        assert actions[0][1]["from"] == "viewer" and actions[0][1]["to"] == "admin"
        assert actions[-1][1]["reason"] == "last_admin"
    _go(body)


def test_jit_user_gets_mapped_role(monkeypatch):
    async def body(c, ids):
        async with AsyncSessionLocal() as db:
            t = await _oidc_tenant(db, ids, auto_provision=True)
        await _map(t.id, MAPPING)
        idp = FakeIdP(monkeypatch)
        idp.email = f"g-{ids.h}-new@corp.example"
        idp.claims = {"groups": ["SOC-Analysts"]}
        assert (await _flow(c, idp, t.slug)).headers["location"].endswith("#sso=ok")
        async with AsyncSessionLocal() as db:
            row = (await db.execute(select(TenantMembership).where(TenantMembership.tenant_id == t.id))).scalars().one()
            ids.users.append(row.user_id)
        assert row.role == "analyst"
    _go(body)


def test_role_groups_config_roundtrip():
    async def body(c, ids):
        async with AsyncSessionLocal() as db:
            t = await make_tenant(db, ids)
            admin = await make_user(db, ids)
        _as_admin(admin, t.id)
        assert (await c.get("/api/v1/tenants/sso-config")).json()["role_groups"] == {}
        r = await c.put("/api/v1/tenants/sso-config",
                        json={"role_groups": {"admin": [" SOC-Admins ", "SOC-Admins", ""], "viewer": []}})
        assert r.status_code == 200, r.text
        assert r.json()["role_groups"] == {"admin": ["SOC-Admins"]}
        assert (await c.put("/api/v1/tenants/sso-config", json={"oidc_scopes": "openid email"})).json()["role_groups"] \
            == {"admin": ["SOC-Admins"]}  # omitted keeps it
        for bad in ({"owner": ["x"]}, {"admin": ["x" * 201]}, {"admin": [f"g{i}" for i in range(51)]}):
            assert (await c.put("/api/v1/tenants/sso-config", json={"role_groups": bad})).status_code == 422, bad
        assert (await c.put("/api/v1/tenants/sso-config", json={"role_groups": {}})).json()["role_groups"] == {}
    _go(body)
