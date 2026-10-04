import asyncio
import secrets as pysecrets

import httpx
from fastapi import HTTPException
from sqlalchemy import delete, func, select

from app.api import deps
from app.core.security import get_password_hash
from app.db.session import AsyncSessionLocal, engine
from app.main import app
from app.models.case import Case
from app.models.membership import TenantMembership
from app.models.notification import CaseFollower, Notification
from app.models.tenant import Tenant
from app.models.user import User


def _deny():
    raise HTTPException(status_code=403, detail="Insufficient permissions")


async def _scenario():
    h = pysecrets.token_hex(3)
    ids = {"tenants": [], "users": [], "cases": []}
    state = {"tenant": None, "role": "analyst", "user": None}
    await engine.dispose()
    async with AsyncSessionLocal() as db:
        try:
            ta = Tenant(name="wf-test", slug=f"wf-test-{h}-a")
            tx = Tenant(name="wf-test", slug=f"wf-test-{h}-x")
            db.add_all([ta, tx])
            await db.flush()
            ids["tenants"] += [ta.id, tx.id]

            def mk(tag, name, active=True):
                return User(email=f"{tag}-{h}@example.test", full_name=name, is_active=active,
                            hashed_password=get_password_hash(pysecrets.token_hex(8)))
            u = mk("u", "Zed User")
            w = mk("w", "Other W")
            actor = mk("actor", None)
            named = mk("named", "Nina Named")
            al = [mk(f"al{i}", f"al{h}{i:02d}") for i in range(12)]
            alx = mk("alx", f"al{h}x")
            ald = mk("ald", f"al{h}dead", active=False)
            pct = mk("pct", "100% Sure")
            db.add_all([u, w, actor, named, alx, ald, pct] + al)
            await db.flush()
            ids["users"] += [x.id for x in [u, w, actor, named, alx, ald, pct] + al]
            for x in [u, w, actor, named, ald, pct] + al:
                db.add(TenantMembership(user_id=x.id, tenant_id=ta.id, role="analyst"))
            db.add(TenantMembership(user_id=alx.id, tenant_id=tx.id, role="analyst"))
            db.add(TenantMembership(user_id=u.id, tenant_id=tx.id, role="analyst"))
            ca = Case(title="Case A", tenant_id=ta.id)
            cx = Case(title="Case X", tenant_id=tx.id)
            co = Case(title="Owned", tenant_id=ta.id, owner_id=u.id)
            db.add_all([ca, cx, co])
            await db.flush()
            ids["cases"] += [ca.id, cx.id, co.id]
            ns = []
            for i, (uid, tid, cid, actor_id) in enumerate([
                (u.id, ta.id, ca.id, actor.id), (u.id, ta.id, ca.id, named.id), (u.id, ta.id, ca.id, None),
                (u.id, tx.id, cx.id, None), (w.id, ta.id, ca.id, None)]):
                n = Notification(tenant_id=tid, user_id=uid, case_id=cid, type="comment",
                                 summary=f"s{i}", actor_id=actor_id)
                db.add(n)
                await db.flush()
                ns.append(n.id)
            await db.commit()
            state["tenant"], state["user"] = ta.id, u
            ca_id, cx_id, co_id, tx_id = ca.id, cx.id, co.id, tx.id
            mine, foreign_tenant, others = ns[:3], ns[3], ns[4]

            app.dependency_overrides[deps.get_current_active_user] = lambda: state["user"]
            app.dependency_overrides[deps.get_effective_tenant_id] = lambda: state["tenant"]
            app.dependency_overrides[deps.require_analyst_or_above] = lambda: state["user"] if state["role"] != "viewer" else _deny()

            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
                N = "/api/v1/notifications"
                # (a) list
                r = await c.get(N + "/")
                assert r.status_code == 200, r.text
                body = r.json()
                assert [x["id"] for x in body] == sorted(mine, reverse=True), "a order"
                by = {x["id"]: x for x in body}
                assert all(x["case_title"] == "Case A" for x in body), "a title"
                assert by[mine[0]]["actor_name"] == f"actor-{h}", "a local part"
                assert by[mine[1]]["actor_name"] == "Nina Named", "a name"
                assert by[mine[2]]["actor_name"] is None, "a automation"
                assert set(body[0]) == {"id", "type", "summary", "case_id", "case_title", "actor_name",
                                        "timeline_event_id", "read_at", "created_at"}
                assert "@" not in str([x["actor_name"] for x in body])
                r = await c.get(N + "/", params={"limit": 2})
                assert [x["id"] for x in r.json()] == sorted(mine, reverse=True)[:2], "a limit"
                r = await c.get(N + "/", params={"before_id": mine[2]})
                assert [x["id"] for x in r.json()] == [mine[1], mine[0]], "a before"
                assert (await c.get(N + "/", params={"limit": 0})).status_code == 422
                assert (await c.get(N + "/", params={"limit": 101})).status_code == 422

                # (b) unread count and read
                assert (await c.get(N + "/unread-count")).json() == {"count": 3}
                assert (await c.post(f"{N}/{mine[0]}/read")).status_code == 204
                assert (await c.get(N + "/unread-count")).json() == {"count": 2}
                r = await c.get(N + "/", params={"unread_only": "true"})
                assert [x["id"] for x in r.json()] == [mine[2], mine[1]], "a unread_only"
                assert (await c.post(f"{N}/{others}/read")).status_code == 404
                assert (await c.post(f"{N}/{foreign_tenant}/read")).status_code == 404
                assert (await c.post(f"{N}/99999999/read")).status_code == 404
                async with AsyncSessionLocal() as d2:
                    assert (await d2.get(Notification, others)).read_at is None, "b W untouched"
                    assert (await d2.get(Notification, foreign_tenant)).read_at is None, "b X untouched"

                # (c) read-all
                r = await c.post(N + "/read-all")
                assert r.status_code == 200 and r.json() == {"updated": 2}, r.text
                assert (await c.get(N + "/unread-count")).json() == {"count": 0}
                async with AsyncSessionLocal() as d2:
                    assert (await d2.get(Notification, others)).read_at is None, "c W untouched"
                    assert (await d2.get(Notification, foreign_tenant)).read_at is None, "c X untouched"

                # (d) follow
                F = f"/api/v1/cases/{ca_id}/follow"
                assert (await c.get(F)).json() == {"following": False}
                assert (await c.put(F)).status_code == 204
                assert (await c.get(F)).json() == {"following": True}
                assert (await c.put(F)).status_code == 204
                async with AsyncSessionLocal() as d2:
                    cnt = (await d2.execute(select(func.count()).select_from(CaseFollower).where(
                        CaseFollower.case_id == ca_id, CaseFollower.user_id == u.id))).scalar()
                    assert cnt == 1, "d one row"
                assert (await c.delete(F)).status_code == 204
                assert (await c.get(F)).json() == {"following": False}
                FX = f"/api/v1/cases/{cx_id}/follow"
                for m in ("get", "put", "delete"):
                    assert (await getattr(c, m)(FX)).status_code == 404, f"d tenant {m}"
                # owner is implicitly followed
                FO = f"/api/v1/cases/{co_id}/follow"
                assert (await c.get(FO)).json() == {"following": True}
                assert (await c.put(FO)).status_code == 204
                assert (await c.delete(FO)).status_code == 204
                assert (await c.get(FO)).json() == {"following": True}
                state["role"] = "viewer"
                assert (await c.put(F)).status_code == 204, "d viewer put"
                assert (await c.get(F)).json() == {"following": True}
                state["role"] = "analyst"

                # (e) mentionable
                M = "/api/v1/users/mentionable"
                r = await c.get(M, params={"q": f"al{h}"})
                assert r.status_code == 200, r.text
                assert len(r.json()) == 10, "e limit"
                assert all(set(x) == {"id", "name", "email"} for x in r.json())
                r = await c.get(M, params={"q": f"al{h}x"})
                assert r.json() == [], "e other tenant"
                r = await c.get(M, params={"q": f"al{h}dead"})
                assert r.json() == [], "e deactivated"
                r = await c.get(M, params={"q": f"AL{h}00".upper()})
                assert [x["id"] for x in r.json()] == [al[0].id], "e case-insens"
                r = await c.get(M, params={"q": f"actor-{h}"})
                assert [x["id"] for x in r.json()] == [actor.id], "e email prefix"
                assert r.json()[0]["name"] == f"actor-{h}", "e name fallback"
                r = await c.get(M, params={"q": "%"})
                assert r.json() == [], "e percent"
                r = await c.get(M, params={"q": "_"})
                assert r.json() == [], "e underscore"
                r = await c.get(M, params={"q": "100%"})
                assert [x["id"] for x in r.json()] == [pct.id], "e literal percent"
                state["role"] = "viewer"
                assert (await c.get(M, params={"q": "al"})).status_code == 403, "e viewer"
                state["role"] = "analyst"
        finally:
            app.dependency_overrides.clear()
            await db.rollback()
            await db.execute(delete(Notification).where(Notification.tenant_id.in_(ids["tenants"])))
            await db.execute(delete(CaseFollower).where(CaseFollower.tenant_id.in_(ids["tenants"])))
            await db.execute(delete(Case).where(Case.id.in_(ids["cases"])))
            await db.execute(delete(TenantMembership).where(TenantMembership.tenant_id.in_(ids["tenants"])))
            await db.execute(delete(User).where(User.id.in_(ids["users"])))
            await db.execute(delete(Tenant).where(Tenant.id.in_(ids["tenants"])))
            await db.commit()
    await engine.dispose()


def test_notifications_api():
    asyncio.run(_scenario())
