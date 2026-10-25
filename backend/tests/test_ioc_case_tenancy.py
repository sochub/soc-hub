"""REST IOCs may only link to a case of the caller's tenant (create and update)."""
import httpx
from sqlalchemy import delete, select

from app.api import deps
from app.db.session import AsyncSessionLocal
from app.main import app
from app.models.ioc import IOC
from tests.guest_fixtures import make_case, make_tenant, make_user, run


def _client():
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")


def test_ioc_create_rejects_foreign_case():
    async def body(ids):
        created = []
        try:
            async with AsyncSessionLocal() as db:
                t, other = await make_tenant(db, ids), await make_tenant(db, ids)
                user = await make_user(db, ids)
                own, foreign = await make_case(db, ids, t), await make_case(db, ids, other)
            app.dependency_overrides[deps.require_analyst_or_above] = lambda: user
            app.dependency_overrides[deps.get_effective_tenant_id] = lambda: t.id
            async with _client() as c:
                r = await c.post("/api/v1/iocs/", json={"ioc_type": "ip_address", "value": "203.0.113.9",
                                                        "case_id": foreign.id})
                if r.status_code == 200:  # record it so cleanup removes it even while this is red
                    created.append(r.json()["id"])
                assert r.status_code == 404 and r.json()["detail"] == "Case not found"
                r = await c.post("/api/v1/iocs/", json={"ioc_type": "ip_address", "value": "203.0.113.9",
                                                        "case_id": own.id})
                assert r.status_code == 200, r.text
                created.append(r.json()["id"])
                r = await c.put(f"/api/v1/iocs/{created[0]}", json={"case_id": foreign.id})
                assert r.status_code == 404 and r.json()["detail"] == "Case not found"
                r = await c.put(f"/api/v1/iocs/{created[0]}", json={"case_id": None})
                assert r.status_code == 200 and r.json()["case_id"] is None
            async with AsyncSessionLocal() as db:
                rows = (await db.execute(select(IOC).where(IOC.value == "203.0.113.9",
                                                           IOC.tenant_id.in_([t.id, other.id])))).scalars().all()
                assert [x.id for x in rows] == created
        finally:
            async with AsyncSessionLocal() as db:
                await db.execute(delete(IOC).where(IOC.id.in_(created or [-1])))
                await db.commit()
    run(body)
