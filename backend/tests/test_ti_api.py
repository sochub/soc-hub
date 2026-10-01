import asyncio
import json
import logging
import re
import secrets
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import httpx
from fastapi import HTTPException
from sqlalchemy import delete, func, select, update

from app.api import deps
from app.core.config import settings
from app.core.security import get_password_hash
from app.db.session import AsyncSessionLocal, engine
from app.enrichment.config import DEFAULTS, decrypt_keys
from app.enrichment.sources import REGISTRY
from app.enrichment.sources.base import LookupResult
from app.main import app
from app.models.artifact import Artifact, ArtifactType
from app.models.audit_log import AuditLog
from app.models.enrichment_result import EnrichmentResult
from app.models.ioc import IOC
from app.models.tenant import Tenant
from app.models.tenant_enrichment_config import TenantEnrichmentConfig
from app.models.user import User

VT_KEY = "vt-SECRET-1"
VT_KEY2 = "vt-SECRET-2"
AB_KEY = "ab-SECRET-3"
ALL_KEYS = (VT_KEY, VT_KEY2, AB_KEY)
CFG = "/api/v1/enrichment/config"
LOG_RX = re.compile(r"^ti_config (save|test|delete) tenant=\d+ outcome=[a-z_]+$")
LOGGER = "app.api.v1.enrichment"


def _deny():
    raise HTTPException(status_code=403, detail="Insufficient permissions")


async def _scenario(monkeypatch, caplog, recorder):
    await engine.dispose()
    h = secrets.token_hex(3)
    ids = {"tenants": [], "users": [], "iocs": [], "artifacts": []}
    state = {"tenant": None, "role": "admin", "user": None}
    async with AsyncSessionLocal() as db:
        try:
            t = Tenant(name="wf-test", slug=f"wf-test-{h}-tiapi")
            t2 = Tenant(name="wf-test", slug=f"wf-test-{h}-tiapi2")
            user = User(email=f"ti-api-{h}@example.test", hashed_password=get_password_hash(secrets.token_hex(8)),
                        is_active=True)
            db.add_all([t, t2, user])
            await db.flush()
            ids["tenants"] += [int(t.id), int(t2.id)]
            ids["users"].append(int(user.id))
            await db.commit()
            tid, tid2 = ids["tenants"]

            ioc = IOC(tenant_id=tid, ioc_type="domain", value="evil.example", tlp="amber", tags=[])
            plain = IOC(tenant_id=tid, ioc_type="domain", value="clean.example", tlp="green", tags=[])
            email = IOC(tenant_id=tid, ioc_type="email", value="a@b.example", tlp="green", tags=[])
            other = IOC(tenant_id=tid2, ioc_type="domain", value="other.example", tlp="green", tags=[])
            nulltlp = IOC(tenant_id=tid, ioc_type="domain", value="nulltlp.example", tags=[])
            art = Artifact(tenant_id=tid, artifact_type=ArtifactType.DOMAIN, value="art.example")
            db.add_all([ioc, plain, email, other, nulltlp, art])
            await db.flush()
            ids["iocs"] += [int(ioc.id), int(plain.id), int(email.id), int(other.id), int(nulltlp.id)]
            ids["artifacts"].append(int(art.id))
            await db.commit()
            await db.execute(update(IOC).where(IOC.id == int(nulltlp.id)).values(tlp=None))
            await db.commit()
            ioc_id, plain_id, email_id, other_id, null_id = ids["iocs"]
            art_id = ids["artifacts"][0]

            state["tenant"], state["user"] = tid, user
            app.dependency_overrides[deps.get_current_active_user] = lambda: state["user"]
            app.dependency_overrides[deps.get_effective_tenant_id] = lambda: state["tenant"]
            app.dependency_overrides[deps.require_admin] = (
                lambda: state["user"] if state["role"] == "admin" else _deny())
            app.dependency_overrides[deps.require_analyst_or_above] = (
                lambda: state["user"] if state["role"] in ("admin", "analyst") else _deny())

            def ti_lines():
                return [rec.getMessage() for rec in caplog.records if rec.name == LOGGER]

            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as client:
                async def call(method, url, op=None, outcome=None, **kw):
                    # o) exactly one well-formed ti_config line per save/test/delete; none for anything else
                    n0 = len(ti_lines())
                    r = await client.request(method, url, **kw)
                    new = ti_lines()[n0:]
                    if op is None:
                        assert new == [], f"o unexpected log {new}"
                    else:
                        assert len(new) == 1 and LOG_RX.match(new[0]), f"o {op} {new}"
                        assert new[0].startswith(f"ti_config {op} tenant={tid} "), f"o {op} {new}"
                        if outcome:
                            assert new[0].endswith(f"outcome={outcome}"), f"o outcome {new}"
                    return r

                async def stored_keys():
                    async with AsyncSessionLocal() as db2:
                        row = (await db2.execute(select(TenantEnrichmentConfig).where(
                            TenantEnrichmentConfig.tenant_id == tid))).scalars().first()
                        return row, (decrypt_keys(row.credentials_enc, tid)[0] if row else {})

                # a) GET /config with no row -> defaults, credentials_set both False
                r = await call("GET", CFG)
                assert r.status_code == 200, f"a {r.status_code} {r.text}"
                d = r.json()
                for k in ("auto_max_tlp", "artifact_tlp", "cache_ttl_hours", "sources", "vt_per_minute",
                          "vt_per_day", "internal_domains"):
                    assert d[k] == DEFAULTS[k], f"a {k} {d[k]}"
                assert d["credentials_set"] == {"virustotal": False, "abusech": False}, "a credentials_set"

                # b) PUT with a VT key -> 200; key never in response or DB plaintext; audit has field names only
                r = await call("PUT", CFG, "save", json={"virustotal_api_key": VT_KEY, "auto_max_tlp": "amber",
                                                         "internal_domains": ["corp.local"]})
                assert r.status_code == 200, f"b {r.status_code} {r.text}"
                assert VT_KEY not in r.text, "b response echoes key"
                d = r.json()
                assert d["credentials_set"]["virustotal"] is True and d["credentials_set"]["abusech"] is False, "b set"
                assert d["auto_max_tlp"] == "amber" and d["internal_domains"] == ["corp.local"], f"b fields {d}"
                row, keys = await stored_keys()
                assert keys.get("virustotal_api_key") == VT_KEY, "b stored key"
                assert VT_KEY not in (row.credentials_enc or ""), "b plaintext key in DB"
                assert VT_KEY not in json.dumps({"s": row.sources, "d": row.internal_domains}), "b key in row"
                audits = (await db.execute(select(AuditLog).where(
                    AuditLog.tenant_id == tid, AuditLog.entity_type == "enrichment_config"))).scalars().all()
                assert audits, "b no audit row"
                assert "virustotal_api_key" in json.dumps(audits[-1].changes), f"b audit fields {audits[-1].changes}"
                assert all(VT_KEY not in json.dumps(a.changes) for a in audits), "b key in audit"

                # c) blank key keeps the stored key; clear_virustotal removes it
                r = await call("PUT", CFG, "save", json={"virustotal_api_key": "   ", "auto_max_tlp": "amber"})
                assert r.status_code == 200 and r.json()["credentials_set"]["virustotal"] is True, f"c blank {r.text}"
                assert (await stored_keys())[1].get("virustotal_api_key") == VT_KEY, "c blank lost key"
                r = await call("PUT", CFG, "save", json={"clear_virustotal": True})
                assert r.status_code == 200 and r.json()["credentials_set"]["virustotal"] is False, f"c clear {r.text}"
                assert "virustotal_api_key" not in (await stored_keys())[1], "c key still stored"

                # c2) partial PUTs merge: fields not sent keep their stored values (never loosened)
                r = await call("PUT", CFG, "save", json={"auto_max_tlp": "none", "internal_domains": ["corp.local"],
                                                         "sources": {"crtsh": False}})
                assert r.status_code == 200, f"c2 setup {r.text}"
                for partial in ({"virustotal_api_key": "x"}, {"clear_virustotal": True}):
                    r = await call("PUT", CFG, "save", json=partial)
                    assert r.status_code == 200, f"c2 {partial} {r.text}"
                    d = r.json()
                    assert d["auto_max_tlp"] == "none", f"c2 {partial} auto {d}"
                    assert d["internal_domains"] == ["corp.local"], f"c2 {partial} domains {d}"
                    assert d["sources"]["crtsh"] is False, f"c2 {partial} sources {d}"
                    row, _ = await stored_keys()
                    assert (row.auto_max_tlp, row.internal_domains, row.sources.get("crtsh")) == (
                        "none", ["corp.local"], False), f"c2 {partial} stored"
                r = await call("PUT", CFG, "save", json={"auto_max_tlp": "green", "internal_domains": [],
                                                         "sources": dict(DEFAULTS["sources"])})
                assert r.status_code == 200 and r.json()["auto_max_tlp"] == "green", f"c2 restore {r.text}"

                # d) invalid bodies -> 422 each (rejected before the handler, so no log line)
                for bad in ({"auto_max_tlp": "purple"}, {"cache_ttl_hours": 0}, {"vt_per_minute": 0},
                            {"internal_domains": ["not a domain"]}, {"sources": {"shodan": True}}):
                    r = await call("PUT", CFG, json=bad)
                    assert r.status_code == 422, f"d {bad} {r.status_code}"

                # e) analyst PUT /config -> 403
                state["role"] = "analyst"
                r = await call("PUT", CFG, json={"auto_max_tlp": "green"})
                assert r.status_code == 403, f"e {r.status_code}"
                state["role"] = "admin"

                # f) IOC GET -> enrichable, no results, no suggestion, effective_tlp amber
                r = await call("GET", f"/api/v1/enrichment/ioc/{ioc_id}")
                assert r.status_code == 200, f"f {r.status_code} {r.text}"
                d = r.json()
                assert d["enrichable"] is True and d["results"] == [] and d["suggestion"] is None, f"f {d}"
                assert d["effective_tlp"] == "amber", f"f tlp {d}"
                assert (d["indicator_type"], d["indicator_value"]) == ("domain", "evil.example"), f"f ind {d}"

                # g) run without confirm -> 409; with confirm -> 202 pending rows for rdap+crtsh, enqueued with force
                r = await call("POST", f"/api/v1/enrichment/ioc/{ioc_id}/run", json={})
                assert r.status_code == 409 and "TLP:amber" in r.json()["detail"], f"g 409 {r.status_code} {r.text}"
                assert r.json()["detail"] == ("This sends the value to external threat-intel services (TLP:amber). "
                                              "Confirm to continue."), f"g detail {r.text}"
                r = await call("POST", f"/api/v1/enrichment/ioc/{ioc_id}/run", json={"confirm": True})
                assert r.status_code == 202, f"g 202 {r.status_code} {r.text}"
                res = r.json()["results"]
                assert {x["source"] for x in res} == {"rdap", "crtsh"}, f"g sources {res}"
                assert all(x["status"] == "pending" for x in res), f"g status {res}"
                assert ((tid, "domain", "evil.example", "amber", True), {}) in recorder, f"g recorder {recorder}"

                # g2) kill switch off -> 503 before 422/409, no pending rows written
                async def n_rows():
                    return (await db.execute(select(func.count()).select_from(EnrichmentResult).where(
                        EnrichmentResult.tenant_id == tid))).scalar()
                before = await n_rows()
                monkeypatch.setattr(settings, "ENRICHMENT_ENABLED", False)
                n_rec = len(recorder)
                for target, body in ((plain_id, {"confirm": True}), (email_id, {"confirm": True}), (null_id, {})):
                    r = await call("POST", f"/api/v1/enrichment/ioc/{target}/run", json=body)
                    assert r.status_code == 503, f"g2 {target} {r.status_code} {r.text}"
                    assert r.json()["detail"] == "Threat-intel enrichment is disabled", f"g2 detail {r.text}"
                monkeypatch.setattr(settings, "ENRICHMENT_ENABLED", True)
                assert await n_rows() == before and len(recorder) == n_rec, "g2 rows written or enqueued"

                # the IOC list schema requires a TLP string, so give it one back before step l
                await db.execute(update(IOC).where(IOC.id == null_id).values(tlp="green"))
                await db.commit()

                # h) seeded VT + ThreatFox malicious -> critical suggestion with the malware tag
                now = datetime.now(timezone.utc)
                db.add_all([
                    EnrichmentResult(tenant_id=tid, indicator_type="domain", indicator_value="evil.example",
                                     source="virustotal", status="ok", verdict="malicious",
                                     summary={"malicious": 20}, fetched_at=now, updated_at=now),
                    EnrichmentResult(tenant_id=tid, indicator_type="domain", indicator_value="evil.example",
                                     source="threatfox", status="ok", verdict="malicious",
                                     summary={"malware_printable": "Emotet"}, fetched_at=now, updated_at=now),
                ])
                await db.commit()
                r = await call("GET", f"/api/v1/enrichment/ioc/{ioc_id}")
                assert r.status_code == 200, f"h {r.status_code}"
                assert r.json()["suggestion"] == {"threat_level": "critical", "tags": ["malware:emotet"]}, f"h {r.text}"
                assert {x["source"] for x in r.json()["results"]} == {"rdap", "crtsh", "virustotal", "threatfox"}, "h rows"

                # i) artifact GET -> suggestion None; run without confirm -> 409 (artifact_tlp default amber)
                r = await call("GET", f"/api/v1/enrichment/artifact/{art_id}")
                assert r.status_code == 200, f"i {r.status_code} {r.text}"
                assert r.json()["suggestion"] is None and r.json()["effective_tlp"] == "amber", f"i {r.text}"
                assert r.json()["auto"] is False, f"i auto {r.text}"
                r = await call("POST", f"/api/v1/enrichment/artifact/{art_id}/run", json={"confirm": False})
                assert r.status_code == 409 and "TLP:amber" in r.json()["detail"], f"i run {r.status_code} {r.text}"

                # j) email IOC -> not enrichable; run -> 422
                r = await call("GET", f"/api/v1/enrichment/ioc/{email_id}")
                assert r.status_code == 200 and r.json()["enrichable"] is False, f"j get {r.text}"
                r = await call("POST", f"/api/v1/enrichment/ioc/{email_id}/run", json={"confirm": True})
                assert r.status_code == 422 and r.json()["detail"] == "This value can't be enriched", f"j run {r.text}"

                # k) cross-tenant IOC -> 404 on GET and run; unknown kind -> 404
                r = await call("GET", f"/api/v1/enrichment/ioc/{other_id}")
                assert r.status_code == 404, f"k get {r.status_code}"
                r = await call("POST", f"/api/v1/enrichment/ioc/{other_id}/run", json={"confirm": True})
                assert r.status_code == 404, f"k run {r.status_code}"
                r = await call("GET", f"/api/v1/enrichment/case/{ioc_id}")
                assert r.status_code == 404, f"k kind {r.status_code}"

                # l) IOC list carries the worst ok verdict; IOCs without results get None
                r = await call("GET", "/api/v1/iocs/")
                assert r.status_code == 200, f"l {r.status_code} {r.text}"
                by_id = {x["id"]: x for x in r.json()}
                assert by_id[ioc_id]["enrichment_verdict"] == "malicious", f"l seeded {by_id.get(ioc_id)}"
                assert by_id[plain_id]["enrichment_verdict"] is None, "l plain"
                assert by_id[email_id]["enrichment_verdict"] is None, "l email"
                assert other_id not in by_id, "l cross-tenant leak"

                # m) a pending row older than 10 minutes is stalled; a fresh pending row is not
                await db.execute(update(EnrichmentResult).where(
                    EnrichmentResult.tenant_id == tid, EnrichmentResult.source == "rdap",
                    EnrichmentResult.indicator_value == "evil.example").values(
                    updated_at=datetime.now(timezone.utc) - timedelta(minutes=11)))
                await db.commit()
                r = await call("GET", f"/api/v1/enrichment/ioc/{ioc_id}")
                st = {x["source"]: x["stalled"] for x in r.json()["results"]}
                assert st["rdap"] is True and st["crtsh"] is False and st["virustotal"] is False, f"m {st}"

                # n) /config/test: no key -> "No key set"; then patched lookups -> VT error, abuse.ch ok
                async def boom(*a, **k):
                    raise AssertionError("n network lookup without a key")
                monkeypatch.setitem(REGISTRY, "virustotal", SimpleNamespace(NAME="virustotal", TYPES=frozenset(),
                                                                            lookup=boom))
                monkeypatch.setitem(REGISTRY, "urlhaus", SimpleNamespace(NAME="urlhaus", TYPES=frozenset(),
                                                                         lookup=boom))
                r = await call("POST", CFG + "/test", "test")
                assert r.status_code == 200, f"n nokey {r.status_code} {r.text}"
                assert r.json() == {"virustotal": {"ok": False, "message": "No key set"},
                                    "abusech": {"ok": False, "message": "No key set"}}, f"n nokey {r.text}"
                r = await call("PUT", CFG, "save", json={"virustotal_api_key": VT_KEY2, "abusech_auth_key": AB_KEY})
                assert r.status_code == 200 and all(r.json()["credentials_set"].values()), f"n put {r.text}"
                seen = []

                async def vt_lookup(itype, value, key, **kw):
                    seen.append(("vt", itype, value, key))
                    return LookupResult(status="error", error="invalid API key")

                async def uh_lookup(itype, value, key, **kw):
                    seen.append(("uh", itype, value, key))
                    return LookupResult(status="not_found")
                monkeypatch.setitem(REGISTRY, "virustotal", SimpleNamespace(NAME="virustotal", TYPES=frozenset(),
                                                                            lookup=vt_lookup))
                monkeypatch.setitem(REGISTRY, "urlhaus", SimpleNamespace(NAME="urlhaus", TYPES=frozenset(),
                                                                         lookup=uh_lookup))
                r = await call("POST", CFG + "/test", "test")
                assert r.status_code == 200, f"n {r.status_code} {r.text}"
                d = r.json()
                assert d["virustotal"] == {"ok": False, "message": "invalid API key"}, f"n vt {d}"
                assert d["abusech"]["ok"] is True, f"n abusech {d}"
                assert seen == [("vt", "ip", "8.8.8.8", VT_KEY2), ("uh", "ip", "8.8.8.8", AB_KEY)], f"n calls {seen}"
                assert not any(k in r.text for k in ALL_KEYS), "n key in response"

                # n2) rate_limited -> "quota reached"; error text containing the key is scrubbed
                async def vt_rl(itype, value, key, **kw):
                    return LookupResult(status="rate_limited")

                async def uh_leaky(itype, value, key, **kw):
                    return LookupResult(status="error", error=f"bad key {key}")
                monkeypatch.setitem(REGISTRY, "virustotal", SimpleNamespace(NAME="virustotal", TYPES=frozenset(),
                                                                            lookup=vt_rl))
                monkeypatch.setitem(REGISTRY, "urlhaus", SimpleNamespace(NAME="urlhaus", TYPES=frozenset(),
                                                                         lookup=uh_leaky))
                r = await call("POST", CFG + "/test", "test")
                d = r.json()
                assert d["virustotal"] == {"ok": False, "message": "quota reached"}, f"n2 vt {d}"
                assert d["abusech"]["ok"] is False and AB_KEY not in r.text, f"n2 scrub {d}"

                # o) DELETE logs one delete line and resets to defaults
                r = await call("DELETE", CFG, "delete")
                assert r.status_code == 204, f"o delete {r.status_code}"
                r = await call("GET", CFG)
                assert r.json()["credentials_set"] == {"virustotal": False, "abusech": False}, "o after delete"
                assert (await stored_keys())[0] is None, "o row remains"

            audits = (await db.execute(select(AuditLog).where(
                AuditLog.tenant_id == tid, AuditLog.entity_type == "enrichment_config"))).scalars().all()
            assert "delete" in {a.action for a in audits}, "o delete audit"
            assert all(not any(k in json.dumps(a.changes) for k in ALL_KEYS) for a in audits), "b key in audit"
            # o) no key text in any log line from the module
            assert all(not any(k in m for k in ALL_KEYS) for m in ti_lines()), "o key in log"
            assert all(not any(k in rec.getMessage() for k in ALL_KEYS) for rec in caplog.records), "o key in any log"
        finally:
            app.dependency_overrides.clear()
            await db.rollback()
            tids = ids["tenants"]
            if tids:
                await db.execute(delete(EnrichmentResult).where(EnrichmentResult.tenant_id.in_(tids)))
            if ids["iocs"]:
                await db.execute(delete(IOC).where(IOC.id.in_(ids["iocs"])))
            if ids["artifacts"]:
                await db.execute(delete(Artifact).where(Artifact.id.in_(ids["artifacts"])))
            if tids:
                await db.execute(delete(TenantEnrichmentConfig).where(TenantEnrichmentConfig.tenant_id.in_(tids)))
                await db.execute(delete(AuditLog).where(AuditLog.tenant_id.in_(tids)))
            if ids["users"]:
                await db.execute(delete(User).where(User.id.in_(ids["users"])))
            if tids:
                await db.execute(delete(Tenant).where(Tenant.id.in_(tids)))
            await db.commit()
            left = (await db.execute(select(func.count()).select_from(Tenant).where(
                Tenant.id.in_(tids or [-1])))).scalar()
            assert left == 0, f"leaked tenants {tids}"
    await engine.dispose()


def test_enrichment_api(monkeypatch, caplog, _no_real_enrichment_enqueue):
    caplog.set_level(logging.INFO)
    asyncio.run(_scenario(monkeypatch, caplog, _no_real_enrichment_enqueue))
