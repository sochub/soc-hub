import asyncio
import secrets as pysecrets

import httpx
from fastapi import HTTPException
from sqlalchemy import delete, select

from app.api import deps
from app.core.security import get_password_hash
from app.db.session import AsyncSessionLocal, engine
from app.main import app
from app.models.audit_log import AuditLog
from app.models.case import Case
from app.models.escalation import CaseEscalation, EscalationTier, UserGroup
from app.models.membership import TenantMembership
from app.models.tenant import Tenant
from app.models.user import User


def _deny():
    raise HTTPException(status_code=403, detail="Insufficient permissions")


async def _scenario():
    h = pysecrets.token_hex(3)
    ids = {"tenants": [], "users": [], "cases": []}
    state = {"tenant": None, "role": "admin", "user": None}
    await engine.dispose()
    async with AsyncSessionLocal() as db:
        try:
            ta = Tenant(name="esc-test", slug=f"esc-test-{h}-a")
            tx = Tenant(name="esc-test", slug=f"esc-test-{h}-x")
            db.add_all([ta, tx])
            await db.flush()
            ids["tenants"] += [ta.id, tx.id]

            def mk(tag, name, sa=False):
                return User(email=f"{tag}-{h}@example.test", full_name=name, is_active=True, is_super_admin=sa,
                            hashed_password=get_password_hash(pysecrets.token_hex(8)))
            admin, u1, u2, ux, sa = mk("adm", "Ada Admin"), mk("u1", "Una"), mk("u2", "Dos"), mk("ux", "Foreign"), mk("sa", "Root", True)
            uv = mk("uv", "Vera Viewer")
            db.add_all([admin, u1, u2, ux, sa, uv])
            await db.flush()
            ids["users"] += [x.id for x in (admin, u1, u2, ux, sa, uv)]
            db.add(TenantMembership(user_id=uv.id, tenant_id=ta.id, role="viewer"))
            db.add(TenantMembership(user_id=admin.id, tenant_id=ta.id, role="admin"))
            m1 = TenantMembership(user_id=u1.id, tenant_id=ta.id, role="analyst")
            db.add_all([m1, TenantMembership(user_id=u2.id, tenant_id=ta.id, role="analyst"),
                        TenantMembership(user_id=ux.id, tenant_id=tx.id, role="analyst")])
            gx = UserGroup(tenant_id=tx.id, name="foreign")
            case = Case(title="esc case", tenant_id=ta.id)
            db.add_all([gx, case])
            await db.commit()
            ids["cases"].append(case.id)
            state["tenant"], state["user"] = ta.id, admin
            ta_id, tx_id, gx_id, case_id = ta.id, tx.id, gx.id, case.id
            u1_id, u2_id, ux_id, m1_id, admin_obj, sa_obj = u1.id, u2.id, ux.id, m1.id, admin, sa
            uv_id = uv.id

            app.dependency_overrides[deps.get_current_active_user] = lambda: state["user"]
            app.dependency_overrides[deps.get_effective_tenant_id] = lambda: state["tenant"]
            app.dependency_overrides[deps.require_analyst_or_above] = (
                lambda: state["user"] if state["role"] != "viewer" else _deny())
            app.dependency_overrides[deps.require_admin] = (
                lambda: state["user"] if state["role"] in ("admin", "super_admin") else _deny())

            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
                E = "/api/v1/escalations"
                # CRUD happy path
                r = await c.post(f"{E}/groups", json={"name": "L1", "description": "first", "member_user_ids": [u1_id, u2_id]})
                assert r.status_code == 201, r.text
                g1 = r.json()
                assert {m["user_id"] for m in g1["members"]} == {u1_id, u2_id} and g1["tier_ids"] == []
                assert (await c.post(f"{E}/groups", json={"name": "L1", "member_user_ids": []})).status_code == 409
                r = await c.post(f"{E}/groups", json={"name": "L2", "member_user_ids": []})
                g2 = r.json()
                r = await c.put(f"{E}/groups/{g1['id']}", json={"name": "L1b", "member_user_ids": [u1_id]})
                assert r.status_code == 200 and r.json()["name"] == "L1b" and len(r.json()["members"]) == 1
                assert (await c.put(f"{E}/groups/{g2['id']}", json={"name": "L1b", "member_user_ids": []})).status_code == 409
                # foreign group 404, foreign member 422
                assert (await c.put(f"{E}/groups/{gx_id}", json={"name": "z", "member_user_ids": []})).status_code == 404
                assert (await c.delete(f"{E}/groups/{gx_id}")).status_code == 404
                assert (await c.post(f"{E}/groups", json={"name": "bad", "member_user_ids": [ux_id]})).status_code == 422
                # viewers can't act on escalations, so they can't be group members
                r = await c.post(f"{E}/groups", json={"name": "bad", "member_user_ids": [u1_id, uv_id]})
                assert r.status_code == 422 and r.json()["detail"] == "Viewers can't be in escalation groups", r.text
                r = await c.put(f"{E}/groups/{g2['id']}", json={"name": "L2", "member_user_ids": [uv_id]})
                assert r.status_code == 422, r.text
                # policy validation
                P = f"{E}/policy"
                t = lambda **k: {"name": "T", "notify_email": True, "notify_slack": False, "group_ids": [g1["id"]], **k}
                assert (await c.put(P, json={"tiers": [t(group_ids=[])]})).status_code == 422
                assert (await c.put(P, json={"tiers": [t(group_ids=[gx_id])]})).status_code == 422
                assert (await c.put(P, json={"tiers": [t(accept_timeout_minutes=0)]})).status_code == 422
                assert (await c.put(P, json={"tiers": [t(accept_timeout_minutes=10081)]})).status_code == 422
                assert (await c.put(P, json={"tiers": [t(slack_channel="Bad Chan!")]})).status_code == 422
                assert (await c.put(P, json={"tiers": [t(slack_channel="#soc-t2", accept_timeout_minutes=30)]})).status_code == 200
                r = await c.put(P, json={"tiers": [t(name="Tier A"), t(name="Tier B", group_ids=[g1["id"], g2["id"]])]})
                assert r.status_code == 200, r.text
                ta_, tb = r.json()["tiers"]
                assert (ta_["position"], tb["position"]) == (1, 2) and tb["group_ids"] == sorted([g1["id"], g2["id"]])
                # reorder keeps ids
                r = await c.put(P, json={"tiers": [{**tb, "id": tb["id"]}, {**ta_, "id": ta_["id"]}]})
                assert r.status_code == 200, r.text
                o = r.json()["tiers"]
                assert [x["id"] for x in o] == [tb["id"], ta_["id"]] and [x["position"] for x in o] == [1, 2]
                assert [x["id"] for x in (await c.get(P)).json()["tiers"]] == [tb["id"], ta_["id"]]
                # delete blocked by tier, naming it
                r = await c.delete(f"{E}/groups/{g1['id']}")
                assert r.status_code == 409 and "Tier A" in r.json()["detail"], r.text
                # remove a tier -> deleted
                r = await c.put(P, json={"tiers": [{**tb, "id": tb["id"]}]})
                assert [x["id"] for x in r.json()["tiers"]] == [tb["id"]]
                async with AsyncSessionLocal() as s2:
                    assert (await s2.execute(select(EscalationTier).where(EscalationTier.id == ta_["id"]))).first() is None
                    s2.add(CaseEscalation(tenant_id=ta_id, case_id=case_id, to_group_id=g2["id"], status="pending",
                                          reason="r", summary="s", requested_help="h", policy_snapshot={}))
                    await s2.commit()
                # g2 is still in tier B -> clear tiers, then pending blocks delete
                assert (await c.put(P, json={"tiers": []})).status_code == 200
                r = await c.delete(f"{E}/groups/{g2['id']}")
                assert r.status_code == 409 and "pending escalation" in r.json()["detail"], r.text
                async with AsyncSessionLocal() as s2:
                    await s2.execute(delete(CaseEscalation).where(CaseEscalation.case_id == case_id))
                    await s2.commit()
                assert (await c.delete(f"{E}/groups/{g2['id']}")).status_code == 204
                # Review focus 3: deleting a membership drops the member from the group
                assert (await c.get(f"{E}/groups")).json()[0]["members"][0]["user_id"] == u1_id
                async with AsyncSessionLocal() as s2:
                    await s2.execute(delete(TenantMembership).where(TenantMembership.id == m1_id))
                    await s2.commit()
                gl = (await c.get(f"{E}/groups")).json()
                assert gl[0]["members"] == []
                # audits
                async with AsyncSessionLocal() as s2:
                    acts = set((await s2.execute(select(AuditLog.action).where(
                        AuditLog.tenant_id == ta_id, AuditLog.action.like("user_group_%")
                        | (AuditLog.action == "escalation_policy_updated")))).scalars().all())
                assert {"user_group_created", "user_group_updated", "user_group_deleted",
                        "escalation_policy_updated"} <= acts, acts
                # roles
                for role in ("analyst", "viewer"):
                    state["role"] = role
                    assert (await c.post(f"{E}/groups", json={"name": "n", "member_user_ids": []})).status_code == 403
                    assert (await c.put(P, json={"tiers": []})).status_code == 403
                state["role"] = "analyst"
                assert (await c.get(f"{E}/groups")).status_code == 200 and (await c.get(P)).status_code == 200
                state["role"] = "viewer"
                assert (await c.get(f"{E}/groups")).status_code == 403
                # super admin on a tenant that has an admin
                state["role"], state["user"] = "super_admin", sa_obj
                from app.utils.account_guard import TENANT_HAS_ADMIN
                for r in (await c.post(f"{E}/groups", json={"name": "sa", "member_user_ids": []}),
                          await c.put(P, json={"tiers": []}),
                          await c.delete(f"{E}/groups/{g1['id']}")):
                    assert r.status_code == 403 and r.json()["detail"] == TENANT_HAS_ADMIN, r.text
        finally:
            app.dependency_overrides.clear()
            await db.rollback()
            async with AsyncSessionLocal() as s3:
                await s3.execute(delete(Case).where(Case.id.in_(ids["cases"])))
                await s3.execute(delete(Tenant).where(Tenant.id.in_(ids["tenants"])))
                await s3.execute(delete(User).where(User.id.in_(ids["users"])))
                await s3.commit()
    await engine.dispose()


def test_escalation_admin_api():
    asyncio.run(_scenario())
