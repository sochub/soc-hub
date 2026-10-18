"""Case assets API: CRUD, validation, ordering, tenant isolation, viewer read-only."""
import asyncio
import secrets

import httpx
from fastapi import HTTPException
from sqlalchemy import delete

from app.api import deps
from app.core.security import get_password_hash
from app.db.session import AsyncSessionLocal, engine
from app.main import app
from app.models.case import Case
from app.models.case_asset import CaseAsset
from app.models.tenant import Tenant
from app.models.user import User


def _deny():
    raise HTTPException(status_code=403, detail="Insufficient permissions")


async def _scenario(monkeypatch):
    await engine.dispose()
    h = secrets.token_hex(3)
    ids = {"tenants": [], "users": [], "cases": []}
    state = {"tenant": None, "role": "analyst", "user": None}
    monkeypatch.setattr(deps, "_active_role", lambda u: state["role"])
    async with AsyncSessionLocal() as db:
        try:
            t, t2 = Tenant(name="as-test", slug=f"as-{h}-a"), Tenant(name="as-test", slug=f"as-{h}-b")
            user = User(email=f"as-{h}@example.test", hashed_password=get_password_hash(secrets.token_hex(8)),
                        is_active=True)
            db.add_all([t, t2, user])
            await db.flush()
            ids["tenants"] += [int(t.id), int(t2.id)]
            ids["users"].append(int(user.id))
            ca, cb = Case(title="as-a", tenant_id=t.id), Case(title="as-b", tenant_id=t2.id)
            db.add_all([ca, cb])
            await db.flush()
            ids["cases"] += [int(ca.id), int(cb.id)]
            await db.commit()
            tid, tid2 = ids["tenants"]
            cid = ids["cases"][0]
            state["tenant"], state["user"] = tid, user
            app.dependency_overrides[deps.get_current_active_user] = lambda: state["user"]
            app.dependency_overrides[deps.get_effective_tenant_id] = lambda: state["tenant"]
            app.dependency_overrides[deps.require_analyst_or_above] = (
                lambda: state["user"] if state["role"] in ("admin", "analyst") else _deny())
            base = f"/api/v1/cases/{cid}/assets"
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
                r = await c.post(base, json={"name": "wks-02", "asset_type": "host", "ip": "10.0.0.2", "tags": ["vip"]})
                assert r.status_code == 201, r.text
                b = r.json()
                assert b["name"] == "wks-02" and b["compromise_status"] == "unknown" and b["tags"] == ["vip"], b
                r = await c.post(base, json={"name": "acct-1", "asset_type": "account"})
                assert r.status_code == 201, r.text
                a = r.json()
                assert (await c.post(base, json={"name": "x", "asset_type": "toaster"})).status_code == 422
                assert [x["name"] for x in (await c.get(base)).json()] == ["acct-1", "wks-02"]
                r = await c.put(f"{base}/{b['id']}", json={"compromise_status": "compromised"})
                assert r.status_code == 200 and r.json()["compromise_status"] == "compromised", r.text
                assert (await c.put(f"{base}/{b['id']}", json={"compromise_status": "bogus"})).status_code == 422
                state["role"] = "viewer"
                assert (await c.get(base)).status_code == 200
                assert (await c.post(base, json={"name": "x"})).status_code == 403
                state["role"] = "analyst"
                state["tenant"] = tid2
                assert (await c.get(base)).status_code == 404
                assert (await c.delete(f"{base}/{a['id']}")).status_code == 404
                state["tenant"] = tid
                assert (await c.delete(f"{base}/{a['id']}")).status_code == 204
                assert (await c.delete(f"{base}/{b['id']}")).status_code == 204
                assert (await c.get(base)).json() == []
        finally:
            app.dependency_overrides.clear()
            async with AsyncSessionLocal() as db3:
                await db3.execute(delete(CaseAsset).where(CaseAsset.case_id.in_(ids["cases"])))
                await db3.execute(delete(Case).where(Case.id.in_(ids["cases"])))
                await db3.execute(delete(User).where(User.id.in_(ids["users"])))
                await db3.execute(delete(Tenant).where(Tenant.id.in_(ids["tenants"])))
                await db3.commit()
            await engine.dispose()


def test_case_assets_api(monkeypatch):
    asyncio.run(_scenario(monkeypatch))
