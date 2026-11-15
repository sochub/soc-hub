"""SSO group sync: IdP group claims -> escalation group membership (login-time, IdP authoritative for mapped groups)."""
import pytest
from pydantic import ValidationError
from sqlalchemy import select

from app.api import deps
from app.api.v1 import sso as sso_mod
from app.db.session import AsyncSessionLocal
from app.escalations import sso_sync
from app.escalations.sso_sync import oidc_groups, saml_groups, sync_user_groups
from app.main import app
from app.models.audit_log import AuditLog
from app.models.escalation import UserGroup, UserGroupMember
from app.models.membership import TenantMembership
from app.schemas.escalation import GroupIn
from tests.guest_fixtures import make_tenant, make_user
from tests.test_guest_sso import FakeAuth
from tests.test_oidc_login import (
    FakeIdP, _as_admin, _callback, _cookie, _flow, _go, _member, _oidc_tenant, _start,
)


# ---- pure parsing -------------------------------------------------------------------------------------------

def test_oidc_claim_parsing():
    assert oidc_groups({"groups": ["a", "b", 3, None]}, "groups") == ["a", "b"]
    assert oidc_groups({"groups": "a, b  c"}, "groups") == ["a", "b", "c"]
    assert oidc_groups({}, "groups") is None
    assert oidc_groups({"groups": 5}, "groups") is None
    assert oidc_groups({"roles": ["x"]}, "roles") == ["x"]
    # Entra group overage: the claim is replaced by a pointer, so "no groups" would be a lie
    assert oidc_groups({"_claim_names": {"groups": "src1"}, "_claim_sources": {}}, "groups") is None
    assert oidc_groups({"groups": ["a"], "_claim_names": {"groups": "src1"}}, "groups") is None
    assert len(oidc_groups({"groups": [f"g{i}" for i in range(900)]}, "groups")) == 500


def test_saml_attribute_parsing():
    assert saml_groups({"groups": ["a", "b", 1]}, "groups") == ["a", "b"]
    assert saml_groups({}, "groups") is None
    assert saml_groups({"groups": []}, "groups") == []


def test_group_in_idp_groups_validation():
    g = GroupIn(name="x", idp_groups=[" Soc-L2 ", "soc-l2", "SOC-L2", "", "other"])
    assert g.idp_groups == ["Soc-L2", "other"]
    assert GroupIn(name="x").idp_groups == []
    with pytest.raises(ValidationError):
        GroupIn(name="x", idp_groups=["a" * 257])
    with pytest.raises(ValidationError):
        GroupIn(name="x", idp_groups=[f"g{i}" for i in range(51)])


# ---- sync_user_groups ---------------------------------------------------------------------------------------

async def _setup(db, ids):
    ta = await make_tenant(db, ids)
    tx = await make_tenant(db, ids)
    u = await make_user(db, ids)
    v = await make_user(db, ids)
    ma = TenantMembership(user_id=u.id, tenant_id=ta.id, role="analyst")
    mv = TenantMembership(user_id=v.id, tenant_id=ta.id, role="viewer")
    mx = TenantMembership(user_id=u.id, tenant_id=tx.id, role="analyst")
    db.add_all([ma, mv, mx])
    mapped = UserGroup(tenant_id=ta.id, name="L2", idp_groups=["SOC-L2", "oid-123"])
    mapped2 = UserGroup(tenant_id=ta.id, name="L3", idp_groups=["soc-l3"])
    manual = UserGroup(tenant_id=ta.id, name="Manual")
    foreign = UserGroup(tenant_id=tx.id, name="FX", idp_groups=["soc-l2"])
    db.add_all([mapped, mapped2, manual, foreign])
    await db.flush()
    db.add_all([UserGroupMember(group_id=manual.id, membership_id=ma.id),
                UserGroupMember(group_id=mapped2.id, membership_id=ma.id)])
    await db.commit()
    return ta, tx, u, v, ma, mv, mx, mapped, mapped2, manual, foreign


async def _members(db, gid):
    return set((await db.execute(select(UserGroupMember.membership_id).where(UserGroupMember.group_id == gid)))
               .scalars().all())


def test_sync_user_groups():
    from tests.guest_fixtures import run

    async def body(ids):
        async with AsyncSessionLocal() as db:
            ta, tx, u, v, ma, mv, mx, mapped, mapped2, manual, foreign = await _setup(db, ids)
            tid, uid, vid = ta.id, u.id, v.id
            ma_id, mv_id, mx_id, g1, g2, gm, gx = ma.id, mv.id, mx.id, mapped.id, mapped2.id, manual.id, foreign.id

        async def sync(groups, user=uid):
            async with AsyncSessionLocal() as db:
                out = await sync_user_groups(db, tenant_id=tid, user_id=user, idp_groups=groups)
                await db.commit()
                return out

        async def state():
            async with AsyncSessionLocal() as db:
                return {k: await _members(db, k) for k in (g1, g2, gm, gx)}

        # absent claim: nothing changes
        assert await sync(None) == {"added": [], "removed": []}
        s = await state()
        assert s[g1] == set() and s[g2] == {ma_id} and s[gm] == {ma_id}

        # case-insensitive match adds L2; L3 (mapped, not asserted) is removed; manual + other tenant untouched
        assert await sync(["soc-l2"]) == {"added": [g1], "removed": [g2]}
        s = await state()
        assert s[g1] == {ma_id} and s[g2] == set() and s[gm] == {ma_id} and s[gx] == set()
        async with AsyncSessionLocal() as db:
            a = (await db.execute(select(AuditLog).where(AuditLog.action == "user_group_sso_sync",
                                                         AuditLog.tenant_id == tid))).scalars().all()
        assert len(a) == 1 and a[0].entity_type == "user" and a[0].entity_id == uid
        assert a[0].changes == {"added": [g1], "removed": [g2]}

        # IdP object id also matches; second identical sync changes nothing and writes no audit
        assert await sync(["OID-123", "unrelated"]) == {"added": [], "removed": []}
        async with AsyncSessionLocal() as db:
            n = len((await db.execute(select(AuditLog).where(AuditLog.action == "user_group_sso_sync",
                                                             AuditLog.tenant_id == tid))).scalars().all())
        assert n == 1

        # empty list: removed from every mapped group, manual group stays
        assert await sync([]) == {"added": [], "removed": [g1]}
        s = await state()
        assert s[g1] == set() and s[gm] == {ma_id}

        # viewers are never added
        assert await sync(["soc-l2", "soc-l3"], user=vid) == {"added": [], "removed": []}
        assert (await state())[g1] == set()
        # ... and are removed if they somehow are in one
        async with AsyncSessionLocal() as db:
            db.add(UserGroupMember(group_id=g1, membership_id=mv_id))
            await db.commit()
        assert await sync(["soc-l2"], user=vid) == {"added": [], "removed": [g1]}

        # a user with no membership here is a no-op
        async with AsyncSessionLocal() as db:
            other = await make_user(db, ids)
        assert await sync(["soc-l2"], user=other.id) == {"added": [], "removed": []}
    run(body)


# ---- config + group API -------------------------------------------------------------------------------------

def test_groups_claim_config_roundtrip():
    async def body(c, ids):
        async with AsyncSessionLocal() as db:
            t = await make_tenant(db, ids)
            admin = await make_user(db, ids)
        _as_admin(admin, t.id)
        assert (await c.get("/api/v1/tenants/sso-config")).json()["groups_claim"] == "groups"
        r = await c.put("/api/v1/tenants/sso-config", json={"groups_claim": "  roles "})
        assert r.status_code == 200 and r.json()["groups_claim"] == "roles", r.text
        assert (await c.get("/api/v1/tenants/sso-config")).json()["groups_claim"] == "roles"
        assert (await c.put("/api/v1/tenants/sso-config", json={"groups_claim": ""})).status_code == 422
        assert (await c.put("/api/v1/tenants/sso-config", json={"groups_claim": "x" * 201})).status_code == 422
        r = await c.put("/api/v1/tenants/sso-config", json={"oidc_scopes": "openid email"})
        assert r.json()["groups_claim"] == "roles"  # omitted keeps it
    _go(body)


def test_group_api_idp_groups_roundtrip():
    async def body(c, ids):
        async with AsyncSessionLocal() as db:
            t = await make_tenant(db, ids)
            admin = await make_user(db, ids)
        _as_admin(admin, t.id)
        app.dependency_overrides[deps.get_current_active_user] = lambda: admin
        app.dependency_overrides[deps.require_analyst_or_above] = lambda: admin
        E = "/api/v1/escalations/groups"
        r = await c.post(E, json={"name": "L2", "member_user_ids": [], "idp_groups": [" SOC-L2 ", "soc-l2", "abc"]})
        assert r.status_code == 201, r.text
        assert r.json()["idp_groups"] == ["SOC-L2", "abc"]
        gid = r.json()["id"]
        r = await c.put(f"{E}/{gid}", json={"name": "L2", "member_user_ids": [], "idp_groups": ["z"]})
        assert r.status_code == 200 and r.json()["idp_groups"] == ["z"]
        assert [g["idp_groups"] for g in (await c.get(E)).json()] == [["z"]]
        assert (await c.put(f"{E}/{gid}", json={"name": "L2", "member_user_ids": [],
                                                "idp_groups": ["a" * 257]})).status_code == 422
        r = await c.put(f"{E}/{gid}", json={"name": "L2", "member_user_ids": []})
        assert r.json()["idp_groups"] == []
    _go(body)


# ---- login integration --------------------------------------------------------------------------------------

async def _group(db, tenant, idp):
    g = UserGroup(tenant_id=tenant.id, name="L2", idp_groups=idp)
    db.add(g)
    await db.commit()
    return g.id


def test_oidc_login_syncs_groups(monkeypatch):
    async def body(c, ids):
        async with AsyncSessionLocal() as db:
            t = await _oidc_tenant(db, ids)
            u = await _member(db, ids, "an", [t])
            gid = await _group(db, t, ["SOC-L2"])
        idp = FakeIdP(monkeypatch)
        idp.email = u.email
        idp.claims = {"groups": "soc-l2, other"}
        r = await _flow(c, idp, t.slug)
        assert r.headers["location"].endswith("/login#sso=ok")
        async with AsyncSessionLocal() as db:
            assert len(await _members(db, gid)) == 1
        # token without the claim leaves membership alone; an empty list removes it
        idp.claims = {}
        assert (await _flow(c, idp, t.slug)).headers["location"].endswith("#sso=ok")
        async with AsyncSessionLocal() as db:
            assert len(await _members(db, gid)) == 1
        idp.claims = {"groups": []}
        assert (await _flow(c, idp, t.slug)).headers["location"].endswith("#sso=ok")
        async with AsyncSessionLocal() as db:
            assert await _members(db, gid) == set()
    _go(body)


def test_oidc_custom_claim_name(monkeypatch):
    async def body(c, ids):
        async with AsyncSessionLocal() as db:
            t = await _oidc_tenant(db, ids)
            u = await _member(db, ids, "an", [t])
            gid = await _group(db, t, ["ops"])
            from app.models.tenant_sso_config import TenantSSOConfig
            cfg = (await db.execute(select(TenantSSOConfig).where(TenantSSOConfig.tenant_id == t.id))).scalars().one()
            cfg.groups_claim = "roles"
            await db.commit()
        idp = FakeIdP(monkeypatch)
        idp.email = u.email
        idp.claims = {"groups": ["ignored"], "roles": ["Ops"]}
        assert (await _flow(c, idp, t.slug)).headers["location"].endswith("#sso=ok")
        async with AsyncSessionLocal() as db:
            assert len(await _members(db, gid)) == 1
    _go(body)


def test_sync_failure_never_blocks_login(monkeypatch):
    async def body(c, ids):
        async with AsyncSessionLocal() as db:
            t = await _oidc_tenant(db, ids)
            u = await _member(db, ids, "an", [t])
        idp = FakeIdP(monkeypatch)
        idp.email = u.email
        idp.claims = {"groups": ["x"]}

        async def boom(*a, **k):
            raise RuntimeError("db exploded")
        monkeypatch.setattr(sso_mod, "sync_user_groups", boom)
        r = await _flow(c, idp, t.slug)
        assert r.status_code == 303 and r.headers["location"].endswith("/login#sso=ok")
        assert _cookie(r)
    _go(body)


def test_saml_acs_syncs_groups(monkeypatch):
    from tests.guest_fixtures import make_tenant as mk
    from app.models.tenant_sso_config import TenantSSOConfig

    class GroupAuth(FakeAuth):
        groups = None

        def get_attributes(self):
            return {} if self.groups is None else {"memberOf": self.groups}

    async def body(c, ids):
        async with AsyncSessionLocal() as db:
            t = await mk(db, ids, sso=True, idp_initiated=True)
            t.member_login_method = "saml"
            cfg = (await db.execute(select(TenantSSOConfig).where(TenantSSOConfig.tenant_id == t.id))).scalars().one()
            cfg.groups_claim = "memberOf"
            await db.commit()
            u = await _member(db, ids, "sa", [t])
            gid = await _group(db, t, ["cn=soc,dc=x"])
        monkeypatch.setattr(sso_mod.saml_service, "make_auth", lambda slug, cfg, req: GroupAuth(u.email))
        GroupAuth.groups = ["CN=SOC,DC=x"]
        r = await c.post(f"/api/v1/auth/saml/{t.slug}/acs", data={"SAMLResponse": "x"})
        assert r.status_code == 303 and r.headers["location"].endswith("#sso=ok"), r.headers["location"]
        async with AsyncSessionLocal() as db:
            assert len(await _members(db, gid)) == 1
        GroupAuth.groups = None  # attribute absent: untouched
        await c.post(f"/api/v1/auth/saml/{t.slug}/acs", data={"SAMLResponse": "x"})
        async with AsyncSessionLocal() as db:
            assert len(await _members(db, gid)) == 1
        GroupAuth.groups = ["other"]
        await c.post(f"/api/v1/auth/saml/{t.slug}/acs", data={"SAMLResponse": "x"})
        async with AsyncSessionLocal() as db:
            assert await _members(db, gid) == set()
    _go(body)
