import asyncio
import hashlib
import json
import logging
import re
import secrets
import xml.etree.ElementTree as ET  # parses our own generated SVG (R2)
from datetime import datetime, timezone

import httpx
from fastapi import HTTPException
from sqlalchemy import delete, func, select

from app.ai import llm
from app.ai.errors import AIError
from app.api import deps
from app.core.security import get_password_hash
from app.db.session import AsyncSessionLocal, engine
from app.main import app
from app.models.audit_log import AuditLog
from app.models.case import Case, TimelineEvent
from app.models.case_report import CaseReport
from app.models.ioc import IOC
from app.models.tenant import Tenant
from app.models.user import User
from app.reports.pdf import RenderTimeout

LOGGER = "app.api.v1.reports"
LOG_RX = re.compile(r"^report (get|put|draft|chart|export) tenant=\d+ case=\d+ format=\S+ full=\S+ outcome=[a-z_]+$")
FILENAME_RX = re.compile(r'^attachment; filename="(case-\d{4}-report-TLP-RED-\d{8}\.pdf)"$')
SECRET_SUMMARY = "SUMMARY-MARKER-the-ceo-laptop-was-compromised"
SECRET_IMPACT = "IMPACT-MARKER-payroll-exposed"
SECRET_LESSONS = "LESSONS-MARKER-patch-faster"
AI_TEXT = "AIDRAFT-MARKER-should-not-be-saved"
URL_WITH_CREDS = "ftp://user:pw@evil.example/x"
DOMAIN = "evil.example.com"


def _deny():
    raise HTTPException(status_code=403, detail="Insufficient permissions")


async def _scenario(monkeypatch, caplog):
    await engine.dispose()
    h = secrets.token_hex(3)
    ids = {"tenants": [], "users": [], "cases": [], "iocs": []}
    state = {"tenant": None, "role": "analyst", "user": None}
    ai = {"provider": None, "reply": None, "raise": False, "calls": []}

    async def fake_describe(db, tenant_id):
        return {"provider": ai["provider"], "model": "m" if ai["provider"] else None, "source": "tenant"}

    async def fake_complete(messages, *, tenant_id, temperature=None, json_mode=False):
        ai["calls"].append({"messages": messages, "tenant_id": tenant_id, "json_mode": json_mode})
        if ai["raise"]:
            raise AIError("provider said: secret-host.internal 401")
        return ai["reply"]

    monkeypatch.setattr(llm, "describe", fake_describe)
    monkeypatch.setattr(llm, "complete", fake_complete)
    async with AsyncSessionLocal() as db:
        try:
            t = Tenant(name="wf-test", slug=f"wf-test-{h}-rp")
            t2 = Tenant(name="wf-test", slug=f"wf-test-{h}-rp2")
            user = User(email=f"wf-test-{h}-rp@example.test", hashed_password=get_password_hash(secrets.token_hex(8)),
                        is_active=True)
            db.add_all([t, t2, user])
            await db.flush()
            ids["tenants"] += [int(t.id), int(t2.id)]
            ids["users"].append(int(user.id))
            await db.commit()
            tid, tid2 = ids["tenants"]
            ca, cb = Case(title="rp-a", tenant_id=tid), Case(title="rp-b", tenant_id=tid2)
            db.add_all([ca, cb])
            await db.flush()
            ids["cases"] += [int(ca.id), int(cb.id)]
            cid, cid_b = ids["cases"]
            iocs = [IOC(tenant_id=tid, case_id=cid, ioc_type="ip_address", value="10.9.8.7", tlp="red",
                        threat_level="high"),
                    IOC(tenant_id=tid, case_id=cid, ioc_type="domain", value=DOMAIN, tlp="green",
                        threat_level="medium"),
                    IOC(tenant_id=tid, case_id=cid, ioc_type="url", value=URL_WITH_CREDS, tlp="amber",
                        threat_level="low")]
            db.add_all(iocs)
            await db.flush()
            ids["iocs"] += [int(i.id) for i in iocs]
            await db.commit()

            state["tenant"], state["user"] = tid, user
            app.dependency_overrides[deps.get_current_active_user] = lambda: state["user"]
            app.dependency_overrides[deps.get_effective_tenant_id] = lambda: state["tenant"]
            app.dependency_overrides[deps.require_analyst_or_above] = (
                lambda: state["user"] if state["role"] in ("admin", "analyst") else _deny())

            def lines():
                return [rec.getMessage() for rec in caplog.records if rec.name == LOGGER]

            base = f"/api/v1/cases/{cid}/report"

            async def audit_rows(**where):
                async with AsyncSessionLocal() as db2:
                    q = select(AuditLog).where(AuditLog.tenant_id == tid)
                    for k, v in where.items():
                        q = q.where(getattr(AuditLog, k) == v)
                    return (await db2.execute(q.order_by(AuditLog.id))).scalars().all()

            async def report_row():
                async with AsyncSessionLocal() as db2:
                    return (await db2.execute(select(CaseReport).where(CaseReport.case_id == cid))).scalars().first()

            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t",
                                         timeout=120) as client:
                async def call(method, url, op=None, outcome=None, **kw):
                    # k) exactly one well-formed line per handled operation; none when a dependency rejects first
                    n0 = len(lines())
                    r = await client.request(method, url, **kw)
                    new = lines()[n0:]
                    if op is None:
                        assert new == [], f"k unexpected log {new}"
                    else:
                        assert len(new) == 1 and LOG_RX.match(new[0]), f"k {op} {new}"
                        assert new[0].startswith(f"report {op} tenant={state['tenant']} "), f"k {op} {new}"
                        assert new[0].endswith(f"outcome={outcome}"), f"k outcome {op} {new}"
                    return r

                # a) GET with no row: defaults, computed min_tlp (red IOC), ai_available false
                r = await call("GET", base, "get", "ok")
                assert r.status_code == 200, f"a {r.status_code} {r.text}"
                d = r.json()
                assert d["min_tlp"] == "red", f"a min_tlp {d}"
                assert d["ai_available"] is False, f"a ai {d}"
                rep = d["report"]
                assert rep["tlp"] == "amber", f"a tlp {rep}"
                for k in ("executive_summary", "impact", "lessons_learned", "first_seen_at", "detected_at",
                          "contained_at", "recovered_at", "updated_by_email", "updated_at"):
                    assert rep[k] is None, f"a default {k} {rep}"
                assert d["milestones"]["detected"]["source"] == "computed", f"a milestones {d['milestones']}"
                assert d["milestones"]["recovered"]["source"] == "unknown", f"a milestones {d['milestones']}"
                assert "out_of_order" in d["milestones"], "a out_of_order"
                assert await report_row() is None, "a GET created a row"

                # d) viewer: 403 on PUT, draft and export (rejected by the dependency, so no log line); GET ok
                state["role"] = "viewer"
                r = await call("PUT", base, json={"tlp": "red"})
                assert r.status_code == 403, f"d put {r.status_code}"
                r = await call("POST", f"{base}/draft")
                assert r.status_code == 403, f"d draft {r.status_code}"
                r = await call("GET", f"{base}/export", params={"format": "pdf"})
                assert r.status_code == 403, f"d export {r.status_code}"
                r = await call("GET", base, "get", "ok")
                assert r.status_code == 200, f"d viewer get {r.status_code}"
                r = await call("GET", f"{base}/charts/lifecycle.svg", "chart", "ok")
                assert r.status_code == 200, f"d viewer chart {r.status_code}"
                state["role"] = "analyst"

                # e) cross-tenant: tenant B gets 404 on every route of tenant A's case
                state["tenant"] = tid2
                ai["provider"] = "openai"
                for method, url, op, kw in (
                        ("GET", base, "get", {}),
                        ("PUT", base, "put", {"json": {"tlp": "red"}}),
                        ("POST", f"{base}/draft", "draft", {}),
                        ("GET", f"{base}/charts/lifecycle.svg", "chart", {}),
                        ("GET", f"{base}/charts/timeline.svg", "chart", {}),
                        ("GET", f"{base}/export", "export", {"params": {"format": "pdf"}}),
                        ("GET", f"{base}/export", "export", {"params": {"format": "json"}})):
                    r = await call(method, url, op, "not_found", **kw)
                    assert r.status_code == 404, f"e {method} {url} {r.status_code}"
                assert ai["calls"] == [], "e AI called cross-tenant"
                ai["provider"] = None
                state["tenant"] = tid
                assert await report_row() is None, "e cross-tenant PUT wrote a row"

                # g) charts: SVG with the three headers, valid XML; unknown name 404
                for name in ("lifecycle", "timeline"):
                    r = await call("GET", f"{base}/charts/{name}.svg", "chart", "ok")
                    assert r.status_code == 200, f"g {name} {r.status_code}"
                    assert r.headers["content-type"].startswith("image/svg+xml"), f"g ct {r.headers}"
                    assert r.headers["content-security-policy"] == "sandbox", "g csp"
                    assert r.headers["x-content-type-options"] == "nosniff", "g nosniff"
                    assert r.headers["cache-control"] == "no-store", "g cache"
                    root = ET.fromstring(r.content)
                    assert root.tag.endswith("svg"), f"g root {root.tag}"
                r = await call("GET", f"{base}/charts/evil.svg", "chart", "not_found")
                assert r.status_code == 404, f"g unknown {r.status_code}"

                # h) PDF export: floor is red (stored tlp still the amber default) -> TLP-RED filename
                r = await call("GET", f"{base}/export", "export", "ok", params={"format": "pdf"})
                assert r.status_code == 200, f"h {r.status_code} {r.text[:200]}"
                assert r.headers["content-type"] == "application/pdf", f"h ct {r.headers}"
                assert r.content.startswith(b"%PDF"), "h not a PDF"
                sha = hashlib.sha256(r.content).hexdigest()
                assert r.headers["x-content-sha256"] == sha, "h header sha"
                assert r.headers["cache-control"] == "no-store", "h cache"
                assert r.headers["x-content-type-options"] == "nosniff", "h nosniff"
                m = FILENAME_RX.match(r.headers["content-disposition"])
                assert m, f"h filename {r.headers['content-disposition']}"
                assert m.group(1) == f"case-{cid:04d}-report-TLP-RED-{datetime.now(timezone.utc):%Y%m%d}.pdf", "h name"
                exp = await audit_rows(entity_type="case", entity_id=cid, action="export_report")
                assert len(exp) == 1, f"h audit rows {len(exp)}"
                assert exp[0].changes == {"format": "pdf", "full": False, "tlp": "red", "sha256": sha,
                                          "size_bytes": len(r.content)}, f"h audit {exp[0].changes}"
                assert exp[0].user_id == ids["users"][0], "h audit user"

                # j) render timeout -> 504
                import app.api.v1.reports as rep_mod

                async def boom(report):
                    raise RenderTimeout()
                orig_render = rep_mod.render_pdf_async
                monkeypatch.setattr(rep_mod, "render_pdf_async", boom)
                r = await call("GET", f"{base}/export", "export", "timeout", params={"format": "pdf"})
                assert r.status_code == 504 and r.json()["detail"] == "Report rendering timed out", \
                    f"j {r.status_code} {r.text}"
                assert len(await audit_rows(action="export_report")) == 1, "j audited a failed export"
                monkeypatch.setattr(rep_mod, "render_pdf_async", orig_render)

                # i) JSON export: schema, raw (not defanged) values, URL userinfo stripped, audit only when full
                r = await call("GET", f"{base}/export", "export", "ok", params={"format": "json"})
                assert r.status_code == 200, f"i {r.status_code} {r.text}"
                assert r.headers["content-type"] == "application/json", f"i ct {r.headers}"
                assert r.headers["x-content-sha256"] == hashlib.sha256(r.content).hexdigest(), "i sha"
                assert re.match(r'^attachment; filename="case-\d{4}-report-TLP-RED-\d{8}\.json"$',
                                r.headers["content-disposition"]), f"i filename {r.headers['content-disposition']}"
                js = json.loads(r.content)
                assert js["meta"]["schema"] == "sochub.incident-report/1", f"i schema {js['meta']}"
                assert js["report"]["tlp"] == "red", f"i tlp {js['report']}"
                assert "audit" not in js, "i audit present without full"
                values = {i["value"] for i in js["iocs"]}
                assert DOMAIN in values and "10.9.8.7" in values, f"i raw values {values}"
                assert not any("[.]" in v or "hxxp" in v for v in values), f"i defanged {values}"
                assert "ftp://evil.example/x" in values, f"i url {values}"
                assert "user:pw" not in r.content.decode(), "i credentials in JSON"
                r = await call("GET", f"{base}/export", "export", "ok", params={"format": "json", "full": "true"})
                assert r.status_code == 200 and "audit" in r.json(), f"i full {r.status_code}"
                assert any(a["action"] == "export_report" for a in r.json()["audit"]), "i full audit content"
                n_exp = len(await audit_rows(action="export_report"))
                r = await call("GET", f"{base}/export", "export", "invalid", params={"format": "json", "tlp": "green"})
                assert r.status_code == 422 and r.json()["detail"] == "TLP must be at least RED", f"i tlp {r.text}"
                assert len(await audit_rows(action="export_report")) == n_exp, "i rejected export audited"

                # c) PUT validation: TLP below floor, out-of-order milestones, naive datetime -> 422, nothing saved
                r = await call("PUT", base, "put", "invalid", json={"tlp": "amber"})
                assert r.status_code == 422 and r.json()["detail"] == "TLP must be at least RED", f"c tlp {r.text}"
                r = await call("PUT", base, "put", "invalid", json={
                    "tlp": "red", "detected_at": "2026-01-02T00:00:00Z", "contained_at": "2026-01-01T00:00:00Z"})
                assert r.status_code == 422, f"c order {r.status_code}"
                assert r.json()["detail"] == "Milestones must be in chronological order", f"c order {r.text}"
                r = await call("PUT", base, json={"tlp": "red", "detected_at": "2026-01-02T00:00:00"})
                assert r.status_code == 422 and "Milestone times must include a timezone" in r.text, f"c naive {r.text}"
                r = await call("PUT", base, json={"tlp": "red", "impact": "x" * 20001})
                assert r.status_code == 422, f"c too long {r.status_code}"
                assert await report_row() is None, "c invalid PUT wrote a row"

                # b) PUT saves and returns the fields; the audit row holds field names only
                body = {"executive_summary": SECRET_SUMMARY, "impact": SECRET_IMPACT,
                        "lessons_learned": SECRET_LESSONS, "tlp": "red",
                        "first_seen_at": "2026-01-01T00:00:00Z", "detected_at": "2026-01-01T03:00:00+03:00",
                        "contained_at": "2026-01-02T00:00:00Z", "recovered_at": None}
                r = await call("PUT", base, "put", "ok", json=body)
                assert r.status_code == 200, f"b {r.status_code} {r.text}"
                rep = r.json()["report"]
                assert rep["executive_summary"] == SECRET_SUMMARY and rep["impact"] == SECRET_IMPACT, f"b {rep}"
                assert rep["lessons_learned"] == SECRET_LESSONS and rep["tlp"] == "red", f"b {rep}"
                assert rep["updated_by_email"] == user.email and rep["updated_at"], f"b who {rep}"
                assert datetime.fromisoformat(rep["detected_at"]) == datetime(2026, 1, 1, tzinfo=timezone.utc), \
                    f"b detected {rep}"
                assert r.json()["milestones"]["detected"]["source"] == "override", "b override source"
                r = await call("GET", base, "get", "ok")
                assert r.json()["report"]["impact"] == SECRET_IMPACT, "b not persisted"
                row = await report_row()
                assert row is not None and row.tenant_id == tid and row.updated_by == ids["users"][0], "b row"
                aud = await audit_rows(entity_type="case_report", action="update")
                assert len(aud) == 1 and aud[0].entity_id == row.id, f"b audit {aud}"
                assert sorted(aud[0].changes["fields"]) == sorted(
                    ["executive_summary", "impact", "lessons_learned", "tlp", "first_seen_at", "detected_at",
                     "contained_at"]), f"b audit fields {aud[0].changes}"
                blob = json.dumps(aud[0].changes)
                assert all(s not in blob for s in (SECRET_SUMMARY, SECRET_IMPACT, SECRET_LESSONS)), "b text in audit"
                # a second identical PUT changes nothing: audited with an empty field list
                r = await call("PUT", base, "put", "ok", json=body)
                assert r.status_code == 200, f"b again {r.status_code}"
                aud = await audit_rows(entity_type="case_report", action="update")
                assert len(aud) == 2 and aud[1].changes == {"fields": []}, f"b second audit {aud[-1].changes}"

                # f) draft: 409 without AI; mocked JSON -> 3 fields, nothing saved; AIError -> 503 generic
                r = await call("POST", f"{base}/draft", "draft", "no_ai")
                assert r.status_code == 409 and r.json()["detail"] == "AI is not configured", f"f 409 {r.text}"
                assert ai["calls"] == [], "f called AI when not configured"
                ai["provider"] = "openai"
                ai["reply"] = "```json\n" + json.dumps({"executive_summary": AI_TEXT, "impact": 42,
                                                       "lessons_learned": "L" * 25000, "extra": "x"}) + "\n```"
                before = await report_row()
                r = await call("POST", f"{base}/draft", "draft", "ok")
                assert r.status_code == 200, f"f {r.status_code} {r.text}"
                d = r.json()
                assert set(d) == {"executive_summary", "impact", "lessons_learned"}, f"f keys {d}"
                assert d["executive_summary"] == AI_TEXT and d["impact"] == "42", f"f values {d}"
                assert d["lessons_learned"] == "L" * 20000, "f truncation"
                assert len(ai["calls"]) == 1 and ai["calls"][0]["json_mode"] is True, "f json mode"
                assert ai["calls"][0]["tenant_id"] == tid, "f tenant"
                prompt = json.dumps(ai["calls"][0]["messages"])
                assert "executive_summary" in prompt and "lessons_learned" in prompt, "f system prompt keys"
                assert "user:pw" not in prompt, "f credentials in prompt"
                after = await report_row()
                assert (after.executive_summary, after.impact, after.lessons_learned, after.updated_at) == (
                    before.executive_summary, before.impact, before.lessons_learned, before.updated_at), "f saved"
                ai["raise"] = True
                r = await call("POST", f"{base}/draft", "draft", "unavailable")
                assert r.status_code == 503 and r.json()["detail"] == "AI drafting is unavailable", f"f 503 {r.text}"
                assert "secret-host" not in r.text, "f provider reason exposed"
                ai["raise"] = False
                ai["reply"] = "not json at all"
                r = await call("POST", f"{base}/draft", "draft", "unavailable")
                assert r.status_code == 503 and r.json()["detail"] == "AI drafting is unavailable", f"f bad {r.text}"

                # GET after save reports ai_available true now that a provider is configured
                r = await call("GET", base, "get", "ok")
                assert r.json()["ai_available"] is True, "f ai_available"

            # k) narrative, AI text and IOC values never reach any log record
            msgs = [rec.getMessage() for rec in caplog.records]
            for s in (SECRET_SUMMARY, SECRET_IMPACT, SECRET_LESSONS, AI_TEXT, "user:pw", DOMAIN, "secret-host"):
                assert all(s not in m for m in msgs), f"k {s!r} logged"
            assert all(LOG_RX.match(m) for m in lines()), f"k malformed {lines()}"
        finally:
            app.dependency_overrides.clear()
            await db.rollback()
            cids, tids, uids = ids["cases"], ids["tenants"], ids["users"]
            if cids:
                await db.execute(delete(CaseReport).where(CaseReport.case_id.in_(cids)))
                await db.execute(delete(TimelineEvent).where(TimelineEvent.case_id.in_(cids)))
            if ids["iocs"]:
                await db.execute(delete(IOC).where(IOC.id.in_(ids["iocs"])))
            if tids:
                await db.execute(delete(AuditLog).where(AuditLog.tenant_id.in_(tids)))
            if cids:
                await db.execute(delete(Case).where(Case.id.in_(cids)))
            if uids:
                await db.execute(delete(User).where(User.id.in_(uids)))
            if tids:
                await db.execute(delete(Tenant).where(Tenant.id.in_(tids)))
            await db.commit()
            left = (await db.execute(select(func.count()).select_from(Tenant).where(
                Tenant.id.in_(tids or [-1])))).scalar()
            assert left == 0, f"leaked tenants {tids}"
    await engine.dispose()


def test_reports_api(monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    asyncio.run(_scenario(monkeypatch, caplog))
