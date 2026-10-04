import asyncio
import logging
import sys
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
from app.utils.crypto import decrypt

BASE = "/api/v1/secrets"


def _deny():
    raise HTTPException(status_code=403, detail="Insufficient permissions")


def _graph(ref):
    return {"nodes": [{"id": "start", "type": "trigger", "config": {}},
                      {"id": "h", "type": "http_request",
                       "config": {"method": "GET", "url": "https://api.example.com/x",
                                  "headers": {"Authorization": "Bearer {{ %s }}" % ref}}}],
            "edges": [{"id": "e1", "source": "start", "target": "h", "source_handle": None}]}


async def _scenario(caplog):
    h = pysecrets.token_hex(3)
    ids = {"tenants": [], "users": [], "workflows": []}
    state = {"tenant": None, "role": "admin", "user": None}
    val = "sk-live-" + pysecrets.token_hex(12)
    val2 = "sk-new-" + pysecrets.token_hex(12)
    bodies = []
    await engine.dispose()
    async with AsyncSessionLocal() as db:
        try:
            ta = Tenant(name="wf-test", slug=f"wf-test-{h}-a")
            tb = Tenant(name="wf-test", slug=f"wf-test-{h}-b")
            user = User(email=f"sec-api-{h}@example.test", hashed_password=get_password_hash(pysecrets.token_hex(8)), is_active=True)
            db.add_all([ta, tb, user])
            await db.flush()
            ids["tenants"] += [ta.id, tb.id]
            ids["users"].append(user.id)
            await db.commit()
            state["tenant"], state["user"] = ta.id, user
            ta_id, tb_id = ta.id, tb.id

            app.dependency_overrides[deps.get_current_active_user] = lambda: state["user"]
            app.dependency_overrides[deps.get_effective_tenant_id] = lambda: state["tenant"]
            app.dependency_overrides[deps.require_admin] = lambda: state["user"] if state["role"] == "admin" else _deny()
            app.dependency_overrides[deps.require_analyst_or_above] = lambda: state["user"] if state["role"] != "viewer" else _deny()

            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
                async def call(method, url, **kw):
                    r = await c.request(method, url, **kw)
                    bodies.append(r.text)
                    return r

                caplog.set_level(logging.INFO)

                # (a) create
                caplog.clear()
                r = await call("POST", BASE + "/", json={"name": "API_KEY", "value": val,
                                                         "allowed_hosts": ["api.example.com", "*.example.org"],
                                                         "description": "d"})
                assert r.status_code == 201, f"a {r.status_code} {r.text}"
                assert "value" not in r.json() and r.json()["name"] == "API_KEY", "a body"
                assert r.json()["created_by_email"] == user.email, "a email"
                create_lines = [x for x in caplog.messages if x.startswith("secret create")]
                assert len(create_lines) == 1, f"j create {create_lines}"
                async with AsyncSessionLocal() as db2:
                    row = (await db2.execute(select(TenantSecret).where(TenantSecret.tenant_id == ta_id, TenantSecret.name == "API_KEY"))).scalars().one()
                    assert row.value_enc != val and val not in row.value_enc, "a enc"
                    assert decrypt(row.value_enc) == val, "a roundtrip"

                # (b) duplicate
                r = await call("POST", BASE + "/", json={"name": "API_KEY", "value": "x", "allowed_hosts": []})
                assert r.status_code == 409, "b"

                # (c) validation
                ok_hosts = ["api.example.com"]
                for label, body in [
                    ("bad name", {"name": "api_key", "value": "x", "allowed_hosts": ok_hosts}),
                    ("short name", {"name": "A", "value": "x", "allowed_hosts": ok_hosts}),
                    ("empty value", {"name": "EMPTY_V", "value": "", "allowed_hosts": ok_hosts}),
                    ("blank value", {"name": "EMPTY_V", "value": "   ", "allowed_hosts": ok_hosts}),
                    ("long value", {"name": "LONG_V", "value": "x" * 8193, "allowed_hosts": ok_hosts}),
                    ("bad host", {"name": "BAD_H", "value": "x", "allowed_hosts": ["Not A Host"]}),
                    ("ip host", {"name": "IP_H", "value": "x", "allowed_hosts": ["10.1.2.3"]}),
                    ("too many hosts", {"name": "MANY_H", "value": "x", "allowed_hosts": [f"h{i}.example.com" for i in range(51)]}),
                    ("long desc", {"name": "LONG_D", "value": "x", "allowed_hosts": ok_hosts, "description": "d" * 501}),
                ]:
                    r = await call("POST", BASE + "/", json=body)
                    assert r.status_code == 422, f"c {label} {r.status_code}"
                r = await call("POST", BASE + "/", json={"name": "MAX_V", "value": "x" * 8192, "allowed_hosts": []})
                assert r.status_code == 201, "c max value"
                # an IP is accepted only when on the tenant allowlist
                async with AsyncSessionLocal() as db2:
                    t = (await db2.execute(select(Tenant).where(Tenant.id == ta_id))).scalars().one()
                    t.workflow_http_allowlist = ["10.1.2.3"]
                    await db2.commit()
                r = await call("POST", BASE + "/", json={"name": "IP_H", "value": "x", "allowed_hosts": ["10.1.2.3"]})
                assert r.status_code == 201, f"c ip allowlisted {r.status_code}"

                # (l) 422s never echo the value; "ABC\n" is rejected; missing-field errors don't echo the body
                lv = "LEAK-" + pysecrets.token_hex(8)
                for label, method, url, body in [
                    ("long", "POST", BASE + "/", {"name": "LEAK_A", "value": lv + "x" * 8200, "allowed_hosts": []}),
                    ("ws", "POST", BASE + "/", {"name": "LEAK_A", "value": " \t ", "allowed_hosts": []}),
                    ("bad name", "POST", BASE + "/", {"name": "bad", "value": lv, "allowed_hosts": []}),
                    ("bad host", "POST", BASE + "/", {"name": "LEAK_A", "value": lv, "allowed_hosts": ["Bad Host"]}),
                    ("missing name", "POST", BASE + "/", {"value": lv, "allowed_hosts": []}),
                    ("nl name", "POST", BASE + "/", {"name": "ABC\n", "value": lv, "allowed_hosts": []}),
                    ("put long", "PUT", BASE + "/API_KEY", {"value": lv + "x" * 8200}),
                    ("put bad", "PUT", BASE + "/API_KEY", {"value": lv, "allowed_hosts": "nope"}),
                ]:
                    r = await call(method, url, json=body)
                    assert r.status_code == 422, f"l {label} {r.status_code}"
                    assert lv not in r.text and " \t " not in r.text, f"l {label} echoed"

                # (m) TOK vs TOK_2 and cross-tenant workflows don't leak into in_use_by
                for n in ("TOK", "TOK_2"):
                    assert (await call("POST", BASE + "/", json={"name": n, "value": "x", "allowed_hosts": []})).status_code == 201
                async with AsyncSessionLocal() as db2:
                    wt = Workflow(tenant_id=ta_id, name="uses-tok2", trigger_type="manual", graph=_graph("secrets.TOK_2"), enabled=False, version=1)
                    wo = Workflow(tenant_id=tb_id, name="other-tenant", trigger_type="manual", graph=_graph("secrets.TOK"), enabled=False, version=1)
                    db2.add_all([wt, wo])
                    await db2.flush()
                    ids["workflows"] += [wt.id, wo.id]
                    await db2.commit()
                    wt_id = wt.id
                by = {x["name"]: x for x in (await call("GET", BASE + "/")).json()}
                assert by["TOK"]["in_use_by"] == [], f"m TOK {by['TOK']['in_use_by']}"
                assert by["TOK_2"]["in_use_by"] == [{"id": wt_id, "name": "uses-tok2"}], "m TOK_2"

                # (e) in_use_by (exact name; FOO_BAR must not count for API_KEY prefix-wise, nor API_KEY2)
                async with AsyncSessionLocal() as db2:
                    w1 = Workflow(tenant_id=ta_id, name="uses-api-key", trigger_type="manual", graph=_graph("secrets.API_KEY"), enabled=False, version=1)
                    w2 = Workflow(tenant_id=ta_id, name="uses-other", trigger_type="manual", graph=_graph("secrets.API_KEY_2"), enabled=False, version=1)
                    w3 = Workflow(tenant_id=ta_id, name="alert-field", trigger_type="manual", graph=_graph("alert.payload.secrets.API_KEY"), enabled=False, version=1)
                    db2.add_all([w1, w2, w3])
                    await db2.flush()
                    ids["workflows"] += [w1.id, w2.id, w3.id]
                    await db2.commit()
                    w1_id = w1.id
                r = await call("GET", BASE + "/")
                assert r.status_code == 200, "e"
                by = {x["name"]: x for x in r.json()}
                assert by["API_KEY"]["in_use_by"] == [{"id": w1_id, "name": "uses-api-key"}], f"e {by['API_KEY']['in_use_by']}"
                assert by["MAX_V"]["in_use_by"] == [] and all("value" not in x for x in r.json()), "e other"

                # (f) PUT
                caplog.clear()
                r = await call("PUT", BASE + "/API_KEY", json={"value": "   ", "description": "nd"})
                assert r.status_code == 200 and r.json()["description"] == "nd", "f blank"
                async with AsyncSessionLocal() as db2:
                    row = (await db2.execute(select(TenantSecret).where(TenantSecret.tenant_id == ta_id, TenantSecret.name == "API_KEY"))).scalars().one()
                    assert decrypt(row.value_enc) == val, "f kept"
                r = await call("PUT", BASE + "/API_KEY", json={"value": val2, "allowed_hosts": ["api.example.com"]})
                assert r.status_code == 200 and r.json()["allowed_hosts"] == ["api.example.com"], "f new"
                assert r.json()["in_use_by"] == [{"id": w1_id, "name": "uses-api-key"}], "f in_use"
                async with AsyncSessionLocal() as db2:
                    row = (await db2.execute(select(TenantSecret).where(TenantSecret.tenant_id == ta_id, TenantSecret.name == "API_KEY"))).scalars().one()
                    assert decrypt(row.value_enc) == val2, "f replaced"
                async with AsyncSessionLocal() as db2:
                    n0 = (await db2.execute(select(func.count()).select_from(AuditLog).where(AuditLog.tenant_id == ta_id, AuditLog.entity_type == "secret"))).scalar()
                r = await call("PUT", BASE + "/API_KEY", json={"description": "nd", "allowed_hosts": ["api.example.com"], "value": ""})
                assert r.status_code == 200, "f noop"
                async with AsyncSessionLocal() as db2:
                    n1 = (await db2.execute(select(func.count()).select_from(AuditLog).where(AuditLog.tenant_id == ta_id, AuditLog.entity_type == "secret"))).scalar()
                assert n1 == n0, "f noop audit"
                r = await call("PUT", BASE + "/NOPE_X", json={"description": "x"})
                assert r.status_code == 404, "f unknown"
                r = await call("PUT", BASE + "/API_KEY", json={"allowed_hosts": ["10.9.9.9"]})
                assert r.status_code == 422, "f bad host"
                upd = [x for x in caplog.messages if x.startswith("secret update")]
                assert len(upd) == 3, f"j update {upd}"

                # (g) delete in use
                caplog.clear()
                r = await call("DELETE", BASE + "/API_KEY")
                assert r.status_code == 409 and r.json()["workflows"] == [{"id": w1_id, "name": "uses-api-key"}], f"g {r.status_code}"
                r = await call("DELETE", BASE + "/API_KEY?force=true")
                assert r.status_code == 204, "g force"
                r = await call("DELETE", BASE + "/MAX_V")
                assert r.status_code == 204, "g unused"
                r = await call("DELETE", BASE + "/MAX_V")
                assert r.status_code == 404, "g gone"
                dl = [x for x in caplog.messages if x.startswith("secret delete")]
                assert len(dl) == 3, f"j delete {dl}"

                # (h) cross tenant
                async with AsyncSessionLocal() as db2:
                    db2.add(TenantSecret(tenant_id=tb_id, name="THEIRS", value_enc="x", allowed_hosts=[]))
                    await db2.commit()
                for m, kw in (("PUT", {"json": {"description": "x"}}), ("DELETE", {})):
                    r = await call(m, BASE + "/THEIRS", **kw)
                    assert r.status_code == 404, f"h {m}"
                names = (await call("GET", BASE + "/names")).json()
                assert "THEIRS" not in [x["name"] for x in names], "h names"

                # (d) roles
                state["role"] = "analyst"
                for m, u, kw in (("POST", BASE + "/", {"json": {"name": "AN_KEY", "value": "x", "allowed_hosts": []}}),
                                 ("PUT", BASE + "/IP_H", {"json": {"description": "x"}}),
                                 ("DELETE", BASE + "/IP_H", {}), ("GET", BASE + "/", {})):
                    r = await call(m, u, **kw)
                    assert r.status_code == 403, f"d analyst {m}"
                r = await call("GET", BASE + "/names")
                assert r.status_code == 200 and "IP_H" in {x["name"] for x in r.json()}, "d names"
                assert all(set(x) == {"name", "description", "allowed_hosts"} for x in r.json()), "d names shape"
                state["role"] = "viewer"
                assert (await call("GET", BASE + "/names")).status_code == 403, "d viewer"
                state["role"] = "admin"

                # (i) audit
                async with AsyncSessionLocal() as db2:
                    logs = (await db2.execute(select(AuditLog).where(AuditLog.tenant_id == ta_id, AuditLog.entity_type == "secret"))).scalars().all()
                    assert {x.action for x in logs} == {"create", "update", "delete"}, "i actions"
                    for x in logs:
                        assert x.changes and "name" in x.changes and "fields" in x.changes, "i shape"
                        dumped = str(x.changes)
                        assert val not in dumped and val2 not in dumped and "sk-" not in dumped, "i value"
                    assert any("value" in x.changes["fields"] for x in logs if x.action == "update"), "i value-changed"

                # (j) logs never contain the value
                assert val not in caplog.text and val2 not in caplog.text, "j value"

                # (k) responses
                assert bodies and all(val not in b and val2 not in b for b in bodies), "k"
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
            if sys.exc_info()[0] is None:
                assert left == 0
    await engine.dispose()


def test_secrets_api(caplog):
    asyncio.run(_scenario(caplog))
