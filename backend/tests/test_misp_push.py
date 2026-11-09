import json
import secrets

import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select

from app.api import deps
from app.core.security import get_password_hash
from app.db.session import AsyncSessionLocal, engine
from app.main import app
from app.models.audit_log import AuditLog
from app.models.case import Case, CaseLink
from app.models.ioc import IOC
from app.models.tenant import Tenant
from app.models.tenant_enrichment_config import TenantEnrichmentConfig
from app.models.user import User
from app.services import misp_push
from app.utils.crypto import encrypt

KEY = "MISP-PUSH-SECRET"
CFG = json.dumps({"url": "https://misp.example/", "key": KEY, "verify": True})
URL = "/api/v1/cases/{}/misp-push"


@pytest.fixture(autouse=True)
def _allow_hosts(monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "MISP_PRIVATE_HOSTS", ["misp.example"])


class Fake:
    def __init__(self, dup=False):
        self.calls, self.dup = [], dup

    def __call__(self, req):
        body = json.loads(req.content)
        self.calls.append((req.url.path, body, req.headers.get("authorization")))
        if req.url.path == "/events/add":
            return httpx.Response(200, json={"Event": {"id": "5", "uuid": "uuid-1234"}})
        if self.dup:
            return httpx.Response(403, json={"errors": {"value": ["A similar attribute already exists for this event."]}})
        return httpx.Response(200, json={"Attribute": {"id": "1"}})


async def _setup(configured=True):
    h = secrets.token_hex(3)
    ids = {"iocs": {}}
    async with AsyncSessionLocal() as db:
        t = Tenant(name="mp-test", slug=f"mp-test-{h}")
        t2 = Tenant(name="mp-test", slug=f"mp-test2-{h}")
        u = User(email=f"mp-{h}@example.test", hashed_password=get_password_hash(secrets.token_hex(8)), is_active=True)
        db.add_all([t, t2, u])
        await db.flush()
        c1 = Case(title="C1", tenant_id=t.id)
        c2 = Case(title="C2", tenant_id=t.id)
        c3 = Case(title="C3", tenant_id=t2.id)
        db.add_all([c1, c2, c3])
        await db.flush()
        rows = {
            "ip": IOC(tenant_id=t.id, case_id=c1.id, ioc_type="ip_address", value="1.2.3.4", tlp="amber", description="c2"),
            "md5": IOC(tenant_id=t.id, case_id=c1.id, ioc_type="file_hash", value="a" * 32, tlp="green"),
            "badhash": IOC(tenant_id=t.id, case_id=c1.id, ioc_type="file_hash", value="abc", tlp="green"),
            "red": IOC(tenant_id=t.id, case_id=c1.id, ioc_type="domain", value="red.example", tlp="red"),
            "other": IOC(tenant_id=t.id, case_id=c1.id, ioc_type="other", value="x", tlp="amber"),
            "c2": IOC(tenant_id=t.id, case_id=c2.id, ioc_type="domain", value="two.example", tlp="amber"),
            "t2": IOC(tenant_id=t2.id, case_id=c3.id, ioc_type="domain", value="t2.example", tlp="amber"),
        }
        db.add_all(rows.values())
        if configured:
            db.add(TenantEnrichmentConfig(tenant_id=t.id, credentials_enc=encrypt(json.dumps({"misp": CFG}))))
        await db.commit()
        ids.update(t=t.id, t2=t2.id, u=u.id, c1=c1.id, c2=c2.id, c3=c3.id, user=u)
        ids["iocs"] = {k: v.id for k, v in rows.items()}
    return ids


async def _cleanup(ids):
    async with AsyncSessionLocal() as s:
        ts = [ids["t"], ids["t2"]]
        await s.execute(delete(CaseLink).where(CaseLink.case_id.in_([ids["c1"], ids["c2"], ids["c3"]])))
        await s.execute(delete(IOC).where(IOC.id.in_(ids["iocs"].values())))
        await s.execute(delete(Case).where(Case.id.in_([ids["c1"], ids["c2"], ids["c3"]])))
        await s.execute(delete(AuditLog).where(AuditLog.tenant_id.in_(ts)))
        await s.execute(delete(TenantEnrichmentConfig).where(TenantEnrichmentConfig.tenant_id.in_(ts)))
        await s.execute(delete(Tenant).where(Tenant.id.in_(ts)))
        await s.execute(delete(User).where(User.id == ids["u"]))
        await s.commit()


async def _run(fn, configured=True, fake=None, monkeypatch=None):
    await engine.dispose()
    ids = await _setup(configured)
    fake = fake or Fake()
    misp_push.TRANSPORT = httpx.MockTransport(fake)

    async def _db():
        async with AsyncSessionLocal() as s:
            yield s

    app.dependency_overrides[deps.get_db] = _db
    app.dependency_overrides[deps.require_analyst_or_above] = lambda: ids["user"]
    app.dependency_overrides[deps.get_effective_tenant_id] = lambda: ids["t"]
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            await fn(c, ids, fake)
    finally:
        app.dependency_overrides.clear()
        misp_push.TRANSPORT = None
        await _cleanup(ids)
        await engine.dispose()


@pytest.mark.asyncio
async def test_first_push_then_reuse_and_skips():
    async def body(c, ids, fake):
        i = ids["iocs"]
        r = await c.post(URL.format(ids["c1"]), json={"ioc_ids": [i["ip"], i["md5"], i["badhash"], i["red"], i["other"]]})
        assert r.status_code == 200, r.text
        j = r.json()
        assert j["event_uuid"] == "uuid-1234" and j["event_url"] == "https://misp.example/events/view/uuid-1234"
        assert sorted(j["pushed"]) == sorted([i["ip"], i["md5"]])
        assert {(s["id"], s["reason"]) for s in j["skipped"]} == {
            (i["badhash"], "unmappable_hash"), (i["red"], "tlp_red"), (i["other"], "unmappable")}
        paths = [p for p, _, _ in fake.calls]
        assert paths[0] == "/events/add" and paths.count("/events/add") == 1
        ev = fake.calls[0][1]["Event"]
        assert ev == {"info": "SOC Hub case #%d: C1" % ids["c1"], "distribution": 0, "analysis": 0, "threat_level_id": 2}
        attrs = {b["value"]: b for p, b, _ in fake.calls if p.startswith("/attributes/add/")}
        assert attrs["1.2.3.4"] == {"type": "ip-dst", "value": "1.2.3.4", "to_ids": True, "comment": "c2"}
        assert attrs["a" * 32]["type"] == "md5"
        assert all(a == KEY for _, _, a in fake.calls)
        async with AsyncSessionLocal() as s:
            links = (await s.execute(select(CaseLink).where(CaseLink.case_id == ids["c1"]))).scalars().all()
            assert [(l.system, l.external_id) for l in links] == [("misp", "uuid-1234")]
            a = (await s.execute(select(AuditLog).where(AuditLog.tenant_id == ids["t"],
                                                       AuditLog.action == "misp_push"))).scalars().all()
            assert len(a) == 1 and a[0].entity_type == "case" and a[0].entity_id == ids["c1"]
            assert a[0].changes["pushed"] == 2 and a[0].changes["skipped"] == 3
        fake.calls.clear()
        r = await c.post(URL.format(ids["c1"]), json={"ioc_ids": [i["ip"]]})
        assert r.status_code == 200 and r.json()["event_uuid"] == "uuid-1234"
        assert [p for p, _, _ in fake.calls] == ["/attributes/add/uuid-1234"]
    await _run(body)


@pytest.mark.asyncio
async def test_mixed_cases_and_other_tenant_rejected():
    async def body(c, ids, fake):
        i = ids["iocs"]
        r = await c.post(URL.format(ids["c1"]), json={"ioc_ids": [i["ip"], i["c2"]]})
        assert r.status_code == 400
        r = await c.post(URL.format(ids["c1"]), json={"ioc_ids": [i["t2"]]})
        assert r.status_code == 400
        r = await c.post(URL.format(ids["c3"]), json={"ioc_ids": [i["t2"]]})
        assert r.status_code == 404
        assert fake.calls == []
    await _run(body)


@pytest.mark.asyncio
async def test_unconfigured_is_409():
    async def body(c, ids, fake):
        r = await c.post(URL.format(ids["c1"]), json={"ioc_ids": [ids["iocs"]["ip"]]})
        assert r.status_code == 409 and r.json()["detail"] == "misp_not_configured"
    await _run(body, configured=False)


@pytest.mark.asyncio
async def test_duplicate_attribute_counts_as_pushed():
    async def body(c, ids, fake):
        r = await c.post(URL.format(ids["c1"]), json={"ioc_ids": [ids["iocs"]["ip"]]})
        assert r.status_code == 200 and r.json()["pushed"] == [ids["iocs"]["ip"]]
    await _run(body, fake=Fake(dup=True))


@pytest.mark.asyncio
async def test_misp_error_is_502_without_key():
    def handler(req):
        return httpx.Response(500, text=f"boom {KEY}")

    async def body(c, ids, fake):
        r = await c.post(URL.format(ids["c1"]), json={"ioc_ids": [ids["iocs"]["ip"]]})
        assert r.status_code == 502 and KEY not in r.text
    await _run(body, fake=handler)


@pytest.mark.asyncio
async def test_db_duplicate_entry_500_is_not_pushed():
    def handler(req):
        if req.url.path == "/events/add":
            return httpx.Response(200, json={"Event": {"id": "5", "uuid": "uuid-1234"}})
        return httpx.Response(500, text="SQLSTATE Duplicate entry for key")

    async def body(c, ids, fake):
        r = await c.post(URL.format(ids["c1"]), json={"ioc_ids": [ids["iocs"]["ip"]]})
        assert r.status_code == 502 and r.json()["detail"]["pushed_ids"] == []
    await _run(body, fake=handler)


@pytest.mark.asyncio
async def test_partial_failure_audited_and_ids_returned():
    n = {"attrs": 0}

    def handler(req):
        if req.url.path == "/events/add":
            return httpx.Response(200, json={"Event": {"id": "5", "uuid": "uuid-1234"}})
        n["attrs"] += 1
        return httpx.Response(200, json={}) if n["attrs"] == 1 else httpx.Response(500, text=KEY)

    async def body(c, ids, fake):
        i = ids["iocs"]
        r = await c.post(URL.format(ids["c1"]), json={"ioc_ids": [i["ip"], i["md5"]]})
        assert r.status_code == 502 and KEY not in r.text
        assert r.json()["detail"]["pushed_ids"] == [i["ip"]]
        async with AsyncSessionLocal() as s:
            a = (await s.execute(select(AuditLog).where(AuditLog.tenant_id == ids["t"],
                                                       AuditLog.action == "misp_push"))).scalars().all()
            assert len(a) == 1 and a[0].changes["partial"] is True and a[0].changes["pushed"] == 1
            assert (await s.execute(select(CaseLink).where(CaseLink.case_id == ids["c1"]))).scalars().first()
    await _run(body, fake=handler)
