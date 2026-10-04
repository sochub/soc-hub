import asyncio
import logging
import secrets as pysecrets

import httpx
from fastapi import HTTPException
from sqlalchemy import delete, func, select

from app.api import deps
from app.core.security import get_password_hash
from app.db.session import AsyncSessionLocal, engine
from app.main import app
from app.models.audit_log import AuditLog
from app.models.tenant import Tenant
from app.models.tenant_secret import TenantSecret
from app.models.user import User
from app.models.workflow import Workflow
from app.secrets.refs import NAME_RE
from app.secrets.scan import apply_conversion, scan_graph
from app.utils.crypto import decrypt, encrypt

BASE = "/api/v1/secrets"


def _g(url="https://api.example.com/x", headers=None):
    return {"nodes": [{"id": "start", "type": "trigger", "config": {}},
                      {"id": "h", "type": "http_request", "config": {"method": "GET", "url": url, "headers": headers or {}}}],
            "edges": [{"id": "e1", "source": "start", "target": "h", "source_handle": None}]}


def test_scan_pure():
    f = scan_graph(_g(headers={"Authorization": "Bearer abc123", "X-Api-Key": "k-1", "Accept": "x"}))
    assert [(x["location"], x["key"], x["literal"], x["host"]) for x in f] == [
        ("header", "Authorization", "abc123", "api.example.com"), ("header", "X-Api-Key", "k-1", "api.example.com")]
    assert f[0]["suggested_name"] == "API_EXAMPLE_COM_AUTHORIZATION"
    q = scan_graph(_g(url="https://api.example.com/x?a=1&apikey=zzz&b=2"))
    assert [(x["location"], x["key"], x["literal"], x["suggested_name"]) for x in q] == [
        ("query", "apikey", "zzz", "API_EXAMPLE_COM_APIKEY")]
    assert scan_graph(_g(headers={"Authorization": "Bearer {{ secrets.A }}", "x-api-key": "{{ case.id }}"},
                         url="https://api.example.com/x?token={{ secrets.T }}")) == []
    assert scan_graph(_g(url="https://{{ trigger.host }}/x", headers={"x-api-key": "k"})) == []
    assert scan_graph(_g(headers={"x-api-key": "k\u00e9", "Authorization": " Bearer x ", "apikey": "a\x01b"})) == []
    n = scan_graph(_g(url="https://1.2.3.4/x", headers={"x-api-key": "k"}))[0]["suggested_name"]
    assert n == "S_1_2_3_4_X_API_KEY" and NAME_RE.match(n)
    long = scan_graph(_g(url="https://" + "a" * 60 + ".com/x", headers={"x-api-key": "k"}))[0]["suggested_name"]
    assert len(long) == 64 and NAME_RE.match(long)


def test_apply_conversion():
    g = _g(url="https://api.example.com/x?a=1&apikey=zzz&b=2#f", headers={"Authorization": "bearer abc", "X-Api-Key": "k"})
    items = [{**f, "secret_name": "S" + str(i) + "X"} for i, f in enumerate(scan_graph(g))]
    out = apply_conversion(g, items)
    cfg = out["nodes"][1]["config"]
    assert cfg["headers"] == {"Authorization": "bearer {{ secrets.S0X }}", "X-Api-Key": "{{ secrets.S1X }}"}
    assert cfg["url"] == "https://api.example.com/x?a=1&apikey={{ secrets.S2X }}&b=2#f"
    assert g["nodes"][1]["config"]["headers"]["X-Api-Key"] == "k"  # input untouched


def _deny():
    raise HTTPException(status_code=403, detail="Insufficient permissions")


async def _scenario(caplog):
    h = pysecrets.token_hex(3)
    ids = {"tenants": [], "users": [], "workflows": []}
    state = {"tenant": None, "role": "admin", "user": None}
    lit, lit2 = "sk-live-" + pysecrets.token_hex(12), "sk-q-" + pysecrets.token_hex(12)
    texts = []
    await engine.dispose()
    async with AsyncSessionLocal() as db:
        try:
            ta = Tenant(name="wf-test", slug=f"wf-test-{h}-a")
            user = User(email=f"sec-scan-{h}@example.test", hashed_password=get_password_hash(pysecrets.token_hex(8)), is_active=True)
            db.add_all([ta, user])
            await db.flush()
            ids["tenants"].append(ta.id)
            ids["users"].append(user.id)
            graph = _g(url=f"https://api.example.com/x?apikey={lit2}", headers={"Authorization": f"Bearer {lit}"})
            wf = Workflow(tenant_id=ta.id, name="w", trigger_type="manual", trigger_filter=None, graph=graph, enabled=True,
                          version=1, created_by=user.id)
            db.add(wf)
            await db.flush()
            ids["workflows"].append(wf.id)
            await db.commit()
            state["tenant"], state["user"] = ta.id, user
            ta_id, wf_id = ta.id, wf.id

            app.dependency_overrides[deps.get_current_active_user] = lambda: state["user"]
            app.dependency_overrides[deps.get_effective_tenant_id] = lambda: state["tenant"]
            app.dependency_overrides[deps.require_admin] = lambda: state["user"] if state["role"] == "admin" else _deny()
            app.dependency_overrides[deps.require_analyst_or_above] = lambda: state["user"] if state["role"] != "viewer" else _deny()
            caplog.set_level(logging.INFO)

            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
                async def call(method, url, **kw):
                    r = await c.request(method, url, **kw)
                    texts.append(r.text)
                    return r

                r = await call("GET", BASE + "/scan")
                assert r.status_code == 200, r.text
                got = sorted((x["key"], x["suggested_name"]) for x in r.json())
                assert got == [("Authorization", "API_EXAMPLE_COM_AUTHORIZATION"), ("apikey", "API_EXAMPLE_COM_APIKEY")], got
                assert all(x["workflow_id"] == wf_id and "literal" not in x for x in r.json())
                assert lit not in r.text and lit2 not in r.text

                state["role"] = "analyst"
                assert (await call("GET", BASE + "/scan")).status_code == 403
                items = [{"node_id": "h", "location": "header", "key": "Authorization", "secret_name": "API_AUTH"}]
                assert (await call("POST", f"{BASE}/convert/{wf_id}", json={"items": items})).status_code == 403
                state["role"] = "admin"

                # conflict: existing secret with a different value -> 409, nothing created, workflow unchanged
                db.add(TenantSecret(tenant_id=ta_id, name="API_QKEY", value_enc=encrypt("other-value"), allowed_hosts=[], created_by=user.id, updated_by=user.id))
                await db.commit()
                two = items + [{"node_id": "h", "location": "query", "key": "apikey", "secret_name": "API_QKEY"}]
                r = await call("POST", f"{BASE}/convert/{wf_id}", json={"items": two})
                assert r.status_code == 409, r.text
                async with AsyncSessionLocal() as d2:
                    names = set((await d2.execute(select(TenantSecret.name).where(TenantSecret.tenant_id == ta_id))).scalars())
                    w = (await d2.execute(select(Workflow).where(Workflow.id == wf_id))).scalars().one()
                    assert names == {"API_QKEY"} and w.version == 1 and w.graph == graph

                # unknown item / bad name -> 422, nothing changes
                bad = [{"node_id": "h", "location": "header", "key": "X-Nope", "secret_name": "API_AUTH"}]
                assert (await call("POST", f"{BASE}/convert/{wf_id}", json={"items": bad})).status_code == 422
                badname = [{**items[0], "secret_name": lit}]
                assert (await call("POST", f"{BASE}/convert/{wf_id}", json={"items": badname})).status_code == 422
                # existing secret with the SAME value is reused
                async with AsyncSessionLocal() as d2:
                    row = (await d2.execute(select(TenantSecret).where(TenantSecret.name == "API_QKEY", TenantSecret.tenant_id == ta_id))).scalars().one()
                    row.value_enc = encrypt(lit2)
                    await d2.commit()
                # same value but host not bound -> 409, nothing changes
                caplog.clear()
                r = await call("POST", f"{BASE}/convert/{wf_id}", json={"items": two})
                assert r.status_code == 409 and "not allowed for host api.example.com" in r.text, r.text
                async with AsyncSessionLocal() as d2:
                    assert set((await d2.execute(select(TenantSecret.name).where(TenantSecret.tenant_id == ta_id))).scalars()) == {"API_QKEY"}
                    row = (await d2.execute(select(TenantSecret).where(TenantSecret.name == "API_QKEY", TenantSecret.tenant_id == ta_id))).scalars().one()
                    assert row.allowed_hosts == []
                    row.allowed_hosts = ["*.example.com"]
                    await d2.commit()
                caplog.clear()
                r = await call("POST", f"{BASE}/convert/{wf_id}", json={"items": two})
                assert r.status_code == 200, r.text
                assert r.json()["created"] == ["API_AUTH"] and r.json()["reused"] == ["API_QKEY"] and r.json()["version"] == 2
                async with AsyncSessionLocal() as d2:
                    w = (await d2.execute(select(Workflow).where(Workflow.id == wf_id))).scalars().one()
                    cfg = w.graph["nodes"][1]["config"]
                    assert cfg["headers"]["Authorization"] == "Bearer {{ secrets.API_AUTH }}"
                    assert cfg["url"] == "https://api.example.com/x?apikey={{ secrets.API_QKEY }}"
                    s = (await d2.execute(select(TenantSecret).where(TenantSecret.name == "API_AUTH", TenantSecret.tenant_id == ta_id))).scalars().one()
                    assert decrypt(s.value_enc) == lit and s.allowed_hosts == ["api.example.com"]
                    logs = (await d2.execute(select(AuditLog).where(AuditLog.tenant_id == ta_id))).scalars().all()
                    assert {(a.entity_type, a.action) for a in logs} == {("secret", "create"), ("workflow", "update")}
                    assert lit not in str([a.changes for a in logs])
                assert lit not in caplog.text and lit2 not in caplog.text
                # converted workflow is valid when read back
                r = await call("GET", f"/api/v1/workflows/{wf_id}")
                assert r.status_code == 200 and r.json()["validation_errors"] == [], r.text
                # disabled workflow + Token prefix + '+' in a query literal; secret allowed_hosts bound to the host
                lit3 = "tok-" + pysecrets.token_hex(8)
                g2 = _g(url="https://api.example.com/y?key=a+b", headers={"Authorization": f"Token {lit3}"})
                wf2 = Workflow(tenant_id=ta_id, name="w2", trigger_type="manual", trigger_filter=None, graph=g2, enabled=False,
                               version=1, created_by=user.id)
                tb = Tenant(name="wf-test", slug=f"wf-test-{h}-b")
                db.add_all([wf2, tb])
                await db.flush()
                ids["workflows"].append(wf2.id)
                ids["tenants"].append(tb.id)
                await db.commit()
                it2 = [{"node_id": "h", "location": "header", "key": "Authorization", "secret_name": "T_AUTH"},
                       {"node_id": "h", "location": "query", "key": "key", "secret_name": "T_KEY"}]
                state["tenant"] = tb.id
                assert (await call("POST", f"{BASE}/convert/{wf2.id}", json={"items": it2})).status_code == 404
                state["tenant"] = ta_id
                r = await call("POST", f"{BASE}/convert/{wf2.id}", json={"items": it2})
                assert r.status_code == 200 and r.json()["created"] == ["T_AUTH", "T_KEY"], r.text
                async with AsyncSessionLocal() as d2:
                    w = (await d2.execute(select(Workflow).where(Workflow.id == wf2.id))).scalars().one()
                    assert w.graph["nodes"][1]["config"]["headers"]["Authorization"] == "Token {{ secrets.T_AUTH }}"
                    assert w.graph["nodes"][1]["config"]["url"] == "https://api.example.com/y?key={{ secrets.T_KEY }}"
                    k = (await d2.execute(select(TenantSecret).where(TenantSecret.name == "T_KEY", TenantSecret.tenant_id == ta_id))).scalars().one()
                    assert decrypt(k.value_enc) == "a b"
                assert (await call("GET", BASE + "/scan")).json() == []
            assert not any(lit in t or lit2 in t for t in texts), "literal leaked in a response"
        finally:
            app.dependency_overrides.clear()
            await db.rollback()
            tids = ids["tenants"]
            await db.execute(delete(AuditLog).where(AuditLog.tenant_id.in_(tids)))
            await db.execute(delete(Workflow).where(Workflow.id.in_(ids["workflows"])))
            await db.execute(delete(TenantSecret).where(TenantSecret.tenant_id.in_(tids)))
            await db.execute(delete(User).where(User.id.in_(ids["users"])))
            await db.execute(delete(Tenant).where(Tenant.id.in_(tids)))
            await db.commit()
            left = (await db.execute(select(func.count()).select_from(Tenant).where(Tenant.slug.like("wf-test-%")))).scalar()
            assert left == 0
    await engine.dispose()


def test_scan_convert_api(caplog):
    asyncio.run(_scenario(caplog))
