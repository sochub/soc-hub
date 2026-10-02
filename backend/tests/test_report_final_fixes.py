"""Final-review fix wave: timeline text scrubbing/defanging, fail-closed TLP, bounded AI draft input,
task caps and axis seconds."""
import asyncio
import json
import re
import secrets
import time
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from pydantic import ValidationError
from sqlalchemy import delete, func, select

import app.db.base  # noqa: F401  (registers all mappers)
from app.api import deps
from app.api.v1.reports import DRAFT_MAX_CHARS, _draft_input
from app.db.session import AsyncSessionLocal, engine
from app.main import app
from app.models.artifact import Artifact, ArtifactType
from app.models.audit_log import AuditLog
from app.models.case import Case, TimelineEvent
from app.models.case_artifact import CaseArtifact
from app.models.ioc import IOC
from app.models.tenant import Tenant
from app.models.user import User
from app.reports import builder, charts, tlp
from app.reports.defang import scrub_userinfo
from app.reports.pdf import render_html
from app.schemas.ioc import IOC as IOC_OUT, IOCCreate, IOCUpdate
from app.services.case_service import add_artifact_to_case
from tests.test_report_render import make_report

T0 = datetime(2026, 10, 1, tzinfo=timezone.utc)
CRED_URL = "http://admin:S3cret@intranet.example/x"


# --- I1: userinfo scrub + PDF defang of timeline text ---

@pytest.mark.parametrize("text,want", [
    ("Added artifact: http://admin:S3cret@intranet.example/x (url)", "Added artifact: http://intranet.example/x (url)"),
    ("see ftp://u:p@h.example/f and HTTPS://a:b@c.example", "see ftp://h.example/f and HTTPS://c.example"),
    ("mail bob@corp.example about http://h.example/p@x", "mail bob@corp.example about http://h.example/p@x"),
    ("no urls here", "no urls here"),
])
def test_scrub_userinfo(text, want):
    assert scrub_userinfo(text) == want


def test_scrub_userinfo_basics_and_linear_time():
    assert scrub_userinfo("bob@corp.example") == "bob@corp.example"
    assert scrub_userinfo("http://host/a@b") == "http://host/a@b"
    assert scrub_userinfo("http://u:p@h/x") == "http://h/x"
    start = time.perf_counter()
    scrub_userinfo("a." * 100_000)
    assert time.perf_counter() - start < 0.5  # C1: was quadratic (ReDoS)


def test_pdf_defangs_urls_in_timeline_text():
    for full in (False, True):  # key: section 3 table; full: Appendix A (R7: section 3 points there)
        r = make_report(full=full)
        r["timeline"][2]["text"] = "Added artifact: http://intranet.example/x (url) <b>"
        html = render_html(r)
        tables = re.sub(r"<svg.*?</svg>", "", html, flags=re.S)  # chart tooltips keep scrubbed raw text
        assert tables.count("hxxp://intranet[.]example/x") == 1
        assert "http://intranet.example/x" not in tables and "&lt;b&gt;" in tables
        assert "URLs in timeline text are defanged" in html


# --- I2: TLP normalisation, fail-closed floor ---

def test_tlp_normalize_and_floor():
    assert tlp.normalize(" TLP:Amber ") == "amber" and tlp.normalize("clear") == "white"
    assert tlp.normalize("TLP: RED") == "red" and tlp.normalize(None) is None and tlp.normalize("") is None
    assert tlp.normalize("purple") is None
    assert tlp.floor(["RED", "amber"]) == "red"
    assert tlp.floor(["clear"]) == "white"
    assert tlp.floor(["TLP:RED"]) == "red"
    assert tlp.floor(["weird"]) == "red"
    assert tlp.floor([None, ""]) == "white"
    assert tlp.at_least("RED", "amber") and tlp.at_least("amber", "TLP:GREEN")
    assert not tlp.at_least("weird", "green") and not tlp.at_least("red", "weird")


def test_ioc_schema_tlp():
    base = {"ioc_type": "ip", "value": "1.2.3.4"}
    assert IOCCreate(**base, tlp="TLP:AMBER").tlp == "amber"
    assert IOCCreate(**base, tlp="CLEAR").tlp == "white"
    assert IOCCreate(**base).tlp == "amber"
    with pytest.raises(ValidationError, match="tlp must be one of white/clear, green, amber, red"):
        IOCCreate(**base, tlp="purple")
    assert IOCUpdate(tlp="Red").tlp == "red" and IOCUpdate().tlp is None
    with pytest.raises(ValidationError):
        IOCUpdate(tlp="purple")
    # responses never re-validate: a legacy stored value must not break reads
    assert IOC_OUT(id=1, tenant_id=1, created_at=T0, ioc_type="ip", value="x", tlp="weird").tlp == "weird"


# --- I3: bounded AI draft input ---

def _big_rep(n_events=5000, n_iocs=3000, n_tasks=500):
    return {
        "case": {"id": 1, "title": "t"},
        "milestones": {k: {"at": T0, "source": "computed"} for k in ("first_seen", "detected", "contained", "recovered")},
        "timeline": [{"n": i, "at": T0 + timedelta(seconds=i), "lane": "analyst", "kind": "comment",
                      "text": "x" * 300, "actor": None} for i in range(1, n_events + 1)],
        "iocs": [{"value": f"{i}.example.com", "type": "domain", "tlp": "amber", "threat_level": None,
                  "verdicts": []} for i in range(n_iocs)],
        "actions": {"containment": [{"title": f"a{i}", "completed_at": T0, "completed_by": None}
                                    for i in range(n_tasks)],
                    "recovery": [{"title": f"r{i}", "completed_at": T0, "completed_by": None} for i in range(10)]},
        "outstanding": [{"title": f"o{i}", "phase": "recovery"} for i in range(n_tasks)],
        "evidence": [{"filename": f"f{i}", "sha256": "a" * 64} for i in range(n_tasks)],
    }


def test_draft_input_is_bounded():
    out = _draft_input(_big_rep())
    assert len(json.dumps(out, ensure_ascii=False)) <= DRAFT_MAX_CHARS
    tr = out["truncated"]
    assert tr["timeline"] == 5000 - len(out["timeline"]) and tr["iocs"] == 3000 - len(out["iocs"])
    assert out["timeline"][0]["n"] == 1 and out["timeline"][-1]["n"] == 5000
    assert len(out["iocs"]) <= 200 and len(out["outstanding"]) <= 200 and len(out["evidence"]) <= 200
    assert sum(len(v) for v in out["actions"].values()) <= 200
    assert tr["outstanding"] == 300 and tr["evidence"] == 300 and tr["actions"] == 310


def test_draft_input_small_is_untouched():
    out = _draft_input(_big_rep(n_events=10, n_iocs=5, n_tasks=3))
    assert "truncated" not in out and len(out["timeline"]) == 10 and len(out["iocs"]) == 5


# --- M1 / ticks ---

def test_task_caps_in_pdf():
    r = make_report()
    r["actions"] = {"containment": [{"title": f"task-{i}", "completed_at": T0, "completed_by": None}
                                    for i in range(1002)]}
    r["outstanding"] = [{"title": f"todo-{i}", "phase": "recovery"} for i in range(1005)]
    assert builder.compute_truncated(r) == {"actions": 2, "outstanding": 5}
    html = render_html(r)
    assert "task-999<" in html and "task-1000<" not in html and "todo-1004<" not in html
    assert "+2 more — see JSON export" in html and "+5 more — see JSON export" in html


def _ticks(events):
    import xml.etree.ElementTree as ET
    root = ET.fromstring(charts.timeline_svg(events))
    ns = "{http://www.w3.org/2000/svg}"
    return [t.text for t in root.iter(ns + "text") if t.get("font-family", "").startswith("Roboto Mono")]


def test_axis_ticks_show_seconds_for_short_spans():
    short = [{"n": 1, "at": T0, "lane": "analyst", "text": "a"},
             {"n": 2, "at": T0 + timedelta(seconds=120), "lane": "analyst", "text": "b"}]
    assert _ticks(short) == ["00:00:00", "00:00:30", "00:01:00", "00:01:30", "00:02:00"]
    long = [{"n": 1, "at": T0, "lane": "analyst", "text": "a"},
            {"n": 2, "at": T0 + timedelta(hours=4), "lane": "analyst", "text": "b"}]
    assert _ticks(long)[0] == "10-01 00:00" and _ticks(long)[-1] == "10-01 04:00"


# --- DB-backed: credentials never leave the builder; legacy TLP casing; IOC create normalises ---

async def _scenario():
    await engine.dispose()
    h = secrets.token_hex(3)
    ids = {"tenant": [], "user": [], "case": [], "art": [], "ioc": []}
    try:
        async with AsyncSessionLocal() as db:
            t = Tenant(name="wf-test", slug=f"wf-test-{h}-rpfix")
            u = User(email=f"wf-test-{h}-rpfix@example.com", hashed_password="x")
            db.add_all([t, u])
            await db.flush()
            ids["tenant"].append(t.id)
            ids["user"].append(u.id)
            case = Case(title="fix", tenant_id=t.id, owner_id=u.id)
            db.add(case)
            await db.flush()
            ids["case"].append(case.id)
            art = await add_artifact_to_case(db, case=case, artifact_type=ArtifactType.URL, value=CRED_URL,
                                             description=None, user_id=u.id)
            ids["art"].append(art.id)
            legacy = IOC(tenant_id=t.id, case_id=case.id, ioc_type="ip_address", value="10.1.1.1", tlp="RED")
            db.add(legacy)
            await db.flush()
            ids["ioc"].append(legacy.id)
            await db.commit()
            case = (await db.execute(select(Case).where(Case.id == case.id))).scalar_one()

            assert await builder.min_tlp(db, case) == "red"
            rep = await builder.build_report(db, case, full=False, generated_by=u)
            assert any(e["kind"] == "artifact_added" for e in rep["timeline"])
            assert "S3cret" not in repr(rep)
            assert "S3cret" not in json.dumps(builder.to_jsonable(rep))
            assert "S3cret" not in charts.timeline_svg(rep["timeline"])
            assert "S3cret" not in json.dumps(_draft_input(rep))
            html = render_html(rep)
            assert "hxxp://intranet[.]example/x" in html and "S3cret" not in html

        app.dependency_overrides[deps.get_current_active_user] = lambda: u
        app.dependency_overrides[deps.get_effective_tenant_id] = lambda: t.id
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as client:
            r = await client.post("/api/v1/iocs/", json={"ioc_type": "ip_address", "value": "10.2.2.2",
                                                         "tlp": "TLP:AMBER", "case_id": case.id})
            assert r.status_code == 200, r.text
            ids["ioc"].append(r.json()["id"])
            assert r.json()["tlp"] == "amber"
            r = await client.post("/api/v1/iocs/", json={"ioc_type": "ip_address", "value": "10.3.3.3",
                                                         "tlp": "purple", "case_id": case.id})
            assert r.status_code == 422 and "tlp must be one of" in r.text
        async with AsyncSessionLocal() as db:
            stored = (await db.execute(select(IOC.tlp).where(IOC.id == ids["ioc"][-1]))).scalar_one()
            assert stored == "amber"
    finally:
        app.dependency_overrides.clear()
        async with AsyncSessionLocal() as db:
            if ids["case"]:
                await db.execute(delete(TimelineEvent).where(TimelineEvent.case_id.in_(ids["case"])))
                await db.execute(delete(CaseArtifact).where(CaseArtifact.case_id.in_(ids["case"])))
            if ids["tenant"]:
                await db.execute(delete(AuditLog).where(AuditLog.tenant_id.in_(ids["tenant"])))
            for model, k in ((IOC, "ioc"), (Artifact, "art"), (Case, "case"), (User, "user"), (Tenant, "tenant")):
                if ids[k]:
                    await db.execute(delete(model).where(model.id.in_(ids[k])))
            await db.commit()
            n = (await db.execute(select(func.count()).select_from(Tenant).where(
                Tenant.id.in_(ids["tenant"] or [-1])))).scalar()
            assert n == 0, "leaked tenants"
        await engine.dispose()


def test_builder_scrubs_credentials_and_legacy_tlp():
    asyncio.run(_scenario())
