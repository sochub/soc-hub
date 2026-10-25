"""The viewer role is read-only on cases, timeline, artifacts and IOCs."""
from sqlalchemy import delete

from app.core import security
from app.db.session import AsyncSessionLocal
from app.models.membership import TenantMembership
from tests.guest_fixtures import client, make_case, make_tenant, make_user, run
from tests.session_fixtures import make_web_token

WRITES = [
    ("POST", "/api/v1/cases/"),
    ("PUT", "/api/v1/cases/{c}"),
    ("POST", "/api/v1/cases/{c}/timeline"),
    ("PUT", "/api/v1/cases/{c}/timeline/1"),
    ("DELETE", "/api/v1/cases/{c}/timeline/1"),
    ("POST", "/api/v1/artifacts/"),
    ("PUT", "/api/v1/artifacts/1"),
    ("DELETE", "/api/v1/artifacts/1"),
    ("DELETE", "/api/v1/artifacts/1/case/{c}"),
    ("POST", "/api/v1/iocs/"),
    ("PUT", "/api/v1/iocs/1"),
    ("DELETE", "/api/v1/iocs/1"),
]


def test_viewer_cannot_write_but_can_read():
    async def body(ids):
        async with AsyncSessionLocal() as db:
            t = await make_tenant(db, ids)
            c = await make_case(db, ids, t)
            u = await make_user(db, ids)
            db.add(TenantMembership(user_id=u.id, tenant_id=t.id, role="viewer"))
            await db.commit()
            h = {"Authorization": f"Bearer {await make_web_token(None, u, t.id)}"}
        try:
            async with client() as cl:
                for method, path in WRITES:
                    r = await cl.request(method, path.format(c=c.id), headers=h, json={"status": "resolved"})
                    assert r.status_code == 403, (method, path, r.status_code)
                assert (await cl.get(f"/api/v1/cases/{c.id}", headers=h)).status_code == 200
                assert (await cl.get("/api/v1/iocs/", headers=h)).status_code == 200
        finally:
            async with AsyncSessionLocal() as db:
                await db.execute(delete(TenantMembership).where(TenantMembership.user_id == u.id))
                await db.commit()
    run(body)
