"""Scan/convert/update edge cases: non-convertible hosts, undecryptable reuse, PUT host revalidation."""
import asyncio
import secrets as pysecrets

import httpx
from sqlalchemy import delete, func, select, update

from app.api import deps
from app.core.security import get_password_hash
from app.db.session import AsyncSessionLocal, engine
from app.main import app
from app.models.audit_log import AuditLog
from app.models.tenant import Tenant
from app.models.tenant_secret import TenantSecret
from app.models.user import User
from app.models.workflow import Workflow
from app.utils.crypto import encrypt

BASE = "/api/v1/secrets"


def _g(url, headers):
    return {"nodes": [{"id": "start", "type": "trigger", "config": {}},
                      {"id": "h", "type": "http_request", "config": {"method": "GET", "url": url, "headers": headers}}],
            "edges": [{"id": "e1", "source": "start", "target": "h", "source_handle": None}]}


async def _scenario():
    h = pysecrets.token_hex(3)
    ids = {"tenants": [], "users": [], "workflows": [], "secrets": []}
    lit = "sk-live-" + pysecrets.token_hex(12)
    texts = []
    await engine.dispose()
    async with AsyncSessionLocal() as db:
        try:
            t = Tenant(name="wf-test", slug=f"wf-test-{h}-ec", workflow_http_allowlist=["intranet", "old-host"])
            user = User(email=f"sec-ec-{h}@example.test", hashed_password=get_password_hash(pysecrets.token_hex(8)), is_active=True)
            db.add_all([t, user])
            await db.flush()
            ids["tenants"].append(t.id)
            ids["users"].append(user.id)
            # 'localhost' is not a valid secret host pattern and not on the allowlist -> reported, not convertible
            w_local = Workflow(tenant_id=t.id, name="local", trigger_type="manual", enabled=False, version=1,
                               graph=_g("http://localhost/x", {"Authorization": f"Bearer {lit}"}))
            w_ok = Workflow(tenant_id=t.id, name="ok", trigger_type="manual", enabled=False, version=1,
                            graph=_g("https://api.example.com/x", {"X-Api-Key": lit}))
            db.add_all([w_local, w_ok])
            await db.flush()
            ids["workflows"] += [w_local.id, w_ok.id]
            bad = TenantSecret(tenant_id=t.id, name="BROKEN", value_enc="garbage", allowed_hosts=["api.example.com"])
            hosts = TenantSecret(tenant_id=t.id, name="HOSTS", value_enc=encrypt("v"), allowed_hosts=["old-host", "a.example.com"])
            db.add_all([bad, hosts])
            await db.flush()
            ids["secrets"] += [bad.id, hosts.id]
            await db.commit()
            tid, wl, wo = t.id, w_local.id, w_ok.id

            app.dependency_overrides[deps.get_current_active_user] = lambda: user
            app.dependency_overrides[deps.get_effective_tenant_id] = lambda: tid
            app.dependency_overrides[deps.require_admin] = lambda: user
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
                async def call(method, url, **kw):
                    r = await c.request(method, url, **kw)
                    texts.append(r.text)
                    return r

                # (6) scan reports the non-pattern host as non-convertible with a reason
                r = await call("GET", BASE + "/scan")
                assert r.status_code == 200, r.text
                by = {x["workflow_id"]: x for x in r.json()}
                assert by[wl]["convertible"] is False and by[wl]["host"] == "localhost"
                assert by[wl]["reason"] == "add the host to the tenant HTTP allowlist to convert"
                assert by[wo]["convertible"] is True and "reason" not in by[wo]
                item = {"node_id": "h", "location": "header", "key": "Authorization", "secret_name": "LOCAL_AUTH"}
                r = await call("POST", f"{BASE}/convert/{wl}", json={"items": [item]})
                assert r.status_code == 422 and "allowlist" in r.text, r.text

                # (8) reuse of a secret whose value can't be decrypted -> 409, not 500
                item = {"node_id": "h", "location": "header", "key": "X-Api-Key", "secret_name": "BROKEN"}
                r = await call("POST", f"{BASE}/convert/{wo}", json={"items": [item]})
                assert r.status_code == 409, r.text
                assert r.json()["detail"] == "Secret BROKEN could not be decrypted — re-enter it in Integrations"

                # (9) PUT keeps an unchanged entry that is no longer allowlisted; new entries are validated
                await db.execute(update(Tenant).where(Tenant.id == tid).values(workflow_http_allowlist=["intranet"]))
                await db.commit()
                r = await call("PUT", BASE + "/HOSTS", json={"allowed_hosts": ["old-host", "a.example.com", "b.example.com"]})
                assert r.status_code == 200 and r.json()["allowed_hosts"] == ["old-host", "a.example.com", "b.example.com"], r.text
                r = await call("PUT", BASE + "/HOSTS", json={"allowed_hosts": ["old-host", "other-host"]})
                assert r.status_code == 422 and "other-host" in r.text and "old-host" not in r.text, r.text
                r = await call("PUT", BASE + "/HOSTS", json={"allowed_hosts": ["old-host", "intranet"]})
                assert r.status_code == 200 and r.json()["allowed_hosts"] == ["old-host", "intranet"], r.text
            async with AsyncSessionLocal() as d2:
                for wid, url in ((wl, "http://localhost/x"), (wo, "https://api.example.com/x")):
                    w = (await d2.execute(select(Workflow).where(Workflow.id == wid))).scalars().one()
                    assert w.version == 1 and w.graph["nodes"][1]["config"]["url"] == url
                names = set((await d2.execute(select(TenantSecret.name).where(TenantSecret.tenant_id == tid))).scalars())
                assert names == {"BROKEN", "HOSTS"}
            assert not any(lit in x for x in texts), "literal leaked in a response"
        finally:
            app.dependency_overrides.clear()
            await db.rollback()
            await db.execute(delete(AuditLog).where(AuditLog.tenant_id.in_(ids["tenants"])))
            await db.execute(delete(Workflow).where(Workflow.id.in_(ids["workflows"])))
            await db.execute(delete(TenantSecret).where(TenantSecret.id.in_(ids["secrets"])))
            await db.execute(delete(User).where(User.id.in_(ids["users"])))
            await db.execute(delete(Tenant).where(Tenant.id.in_(ids["tenants"])))
            await db.commit()
            left = (await db.execute(select(func.count()).select_from(Tenant).where(Tenant.slug.like("wf-test-%")))).scalar()
            assert left == 0
    await engine.dispose()


def test_scan_convert_update_edge_cases():
    asyncio.run(_scenario())
