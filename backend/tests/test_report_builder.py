import asyncio
import json
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, func, select

import app.db.base  # noqa: F401  (registers all mappers)
from app.db.session import AsyncSessionLocal, engine
from app.models.artifact import Artifact, ArtifactType
from app.models.audit_log import AuditLog
from app.models.case import Alert, Case, CaseSeverity, CaseStatus, TimelineEvent
from app.models.case_artifact import CaseArtifact
from app.models.case_attachment import CaseAttachment
from app.models.case_report import CaseReport
from app.models.case_task import CaseTask
from app.models.enrichment_result import EnrichmentResult
from app.models.ioc import IOC
from app.models.tenant import Tenant
from app.models.user import User
from app.reports import builder

BASE = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
HOSTILE = "<img src=x>"


async def _scenario():
    await engine.dispose()
    h = secrets.token_hex(3)
    ids = {k: [] for k in ("enr", "att", "audit", "tl", "task", "alert", "ca", "art", "ioc", "rep", "case",
                           "user", "tenant")}
    try:
        async with AsyncSessionLocal() as db:
            t = Tenant(name="wf-test", slug=f"wf-test-{h}-report")
            db.add(t)
            await db.flush()
            ids["tenant"].append(t.id)
            u = User(email=f"wf-test-{h}-rep@example.com", hashed_password="x", full_name="R")
            db.add(u)
            await db.flush()
            ids["user"].append(u.id)
            case = Case(title=HOSTILE, severity=CaseSeverity.HIGH, status=CaseStatus.OPEN, tags=["phish"],
                        tenant_id=t.id, owner_id=u.id, created_at=BASE)
            db.add(case)
            await db.flush()
            ids["case"].append(case.id)

            dom = f"evil{h}.wfreport.net"
            raw_dom = f"Evil{h}.WFReport.net"  # stored raw; normalise() lower-cases it
            mal = IOC(tenant_id=t.id, case_id=case.id, ioc_type="domain", value=raw_dom, threat_level="low",
                      tlp="amber", first_seen=BASE - timedelta(days=2))
            red = IOC(tenant_id=t.id, case_id=case.id, ioc_type="ip_address", value="10.9.8.7",
                      threat_level="critical", tlp="red", first_seen=BASE - timedelta(days=1))
            db.add_all([mal, red])
            art = Artifact(tenant_id=t.id, artifact_type=ArtifactType.URL,
                           value=f"http://user:s3cr3t@x{h}.wfreport.net/a")
            db.add(art)
            await db.flush()
            ids["ioc"] += [mal.id, red.id]
            ids["art"].append(art.id)
            ca = CaseArtifact(case_id=case.id, artifact_id=art.id)
            db.add(ca)
            await db.flush()
            ids["ca"].append(ca.id)

            evs = [TimelineEvent(case_id=case.id, user_id=u.id, event_type="comment", content=HOSTILE + "y" * 400,
                                 created_at=BASE + timedelta(hours=1)),
                   TimelineEvent(case_id=case.id, user_id=u.id, event_type="artifact_added", content="added url",
                                 created_at=BASE + timedelta(hours=2)),
                   TimelineEvent(case_id=case.id, user_id=u.id, event_type="attachment_deleted",
                                 content="deleted f", created_at=BASE + timedelta(hours=4))]
            db.add_all(evs)
            alert = Alert(source="EDR", external_id=f"wf-{h}", title="Beacon", tenant_id=t.id, case_id=case.id,
                          created_at=BASE - timedelta(hours=1))
            db.add(alert)
            done = CaseTask(case_id=case.id, tenant_id=t.id, phase="containment", title="Isolate host",
                            status="done", completed_at=BASE + timedelta(hours=3), completed_by=u.id)
            todo = CaseTask(case_id=case.id, tenant_id=t.id, phase="recovery", title="Reimage", status="todo")
            db.add_all([done, todo])
            live = CaseAttachment(tenant_id=t.id, case_id=case.id, filename="live.eml", size_bytes=10,
                                  sha256="a" * 64, storage_key=f"wf-test/{h}/1", uploaded_by=u.id,
                                  created_at=BASE + timedelta(minutes=30))
            gone = CaseAttachment(tenant_id=t.id, case_id=case.id, filename="gone.bin", size_bytes=5,
                                  sha256="b" * 64, storage_key=f"wf-test/{h}/2", uploaded_by=u.id,
                                  deleted_at=BASE + timedelta(hours=4), deleted_by=u.id)
            db.add_all([live, gone])
            enr = EnrichmentResult(tenant_id=t.id, indicator_type="domain", indicator_value=dom, source="vt",
                                   status="ok", verdict="malicious", score="9/70",
                                   fetched_at=BASE + timedelta(hours=2, minutes=30))
            err = EnrichmentResult(tenant_id=t.id, indicator_type="domain", indicator_value=dom, source="otx",
                                   status="error", verdict="malicious", error="boom",
                                   fetched_at=BASE + timedelta(hours=2, minutes=40))
            # Isolation: another tenant with the same indicator and a malicious ok verdict.
            t2 = Tenant(name="wf-test", slug=f"wf-test-{h}-report2")
            db.add_all([enr, err, t2])
            await db.flush()
            ids["tenant"].append(t2.id)
            ids["enr"].append(err.id)
            other_ioc = IOC(tenant_id=t2.id, ioc_type="domain", value=dom, tlp="white", threat_level="info")
            other_enr = EnrichmentResult(tenant_id=t2.id, indicator_type="domain", indicator_value=dom, source="xf",
                                         status="ok", verdict="malicious", score="99",
                                         fetched_at=BASE + timedelta(hours=1, minutes=10))
            db.add_all([other_ioc, other_enr])
            await db.flush()
            ids["ioc"].append(other_ioc.id)
            ids["enr"].append(other_enr.id)
            ids["tl"] += [e.id for e in evs]
            ids["alert"].append(alert.id)
            ids["task"] += [done.id, todo.id]
            ids["att"] += [live.id, gone.id]
            ids["enr"].append(enr.id)
            a1 = AuditLog(entity_type="case", entity_id=case.id, action="update", changes={"x": 1},
                          user_id=u.id, tenant_id=t.id, created_at=BASE + timedelta(hours=1))
            a2 = AuditLog(entity_type="attachment", entity_id=gone.id, action="delete", user_id=u.id,
                          tenant_id=t.id, created_at=BASE + timedelta(hours=4))
            db.add_all([a1, a2])
            await db.flush()
            ids["audit"] += [a1.id, a2.id]
            await db.commit()

            case = (await db.execute(select(Case).where(Case.id == case.id))).scalar_one()
            assert await builder.min_tlp(db, case) == "red"
            assert await builder.report_row(db, case) is None

            key = await builder.build_report(db, case, full=False, generated_by=u)
            full = await builder.build_report(db, case, full=True, generated_by=u)

            kinds_key = [e["kind"] for e in key["timeline"]]
            kinds_full = [e["kind"] for e in full["timeline"]]
            # R5: deletions of evidence stay in key mode (chain of custody).
            assert "attachment_deleted" in kinds_key and "attachment_deleted" in kinds_full
            enr_events = [e for e in full["timeline"] if e["kind"] == "enrichment"]
            assert [(e["actor"], e["at"]) for e in enr_events] == [("vt", BASE + timedelta(hours=2, minutes=30))]
            lanes = {e["kind"]: e["lane"] for e in full["timeline"]}
            assert lanes == {"comment": "analyst", "artifact_added": "indicators", "attachment_deleted": "evidence",
                             "case_created": "detection", "alert": "detection", "task_completed": "analyst",
                             "enrichment": "indicators"}, lanes
            for tl in (key["timeline"], full["timeline"]):
                assert [e["n"] for e in tl] == list(range(1, len(tl) + 1))
                assert [e["at"] for e in tl] == sorted(e["at"] for e in tl)
            texts = {e["kind"]: e["text"] for e in full["timeline"]}
            assert texts["comment"].startswith(HOSTILE) and len(texts["comment"]) == 300
            assert texts["alert"] == "Alert received: Beacon"
            assert texts["task_completed"] == "Task completed: Isolate host"
            assert texts["enrichment"] == "vt flagged domain as malicious" and dom not in texts["enrichment"]

            iocs = key["iocs"]
            assert iocs[0]["value"] == raw_dom and iocs[0]["verdicts"] == [
                {"source": "vt", "verdict": "malicious", "score": "9/70"}]
            assert iocs[1]["value"] == "10.9.8.7" and iocs[1]["tlp"] == "red"
            assert {i["type"] for i in iocs} == {"domain", "ip", "url"} and len(iocs) == 3
            url = next(i for i in iocs if i["type"] == "url")
            assert url["value"] == f"http://x{h}.wfreport.net/a" and "s3cr3t" not in json.dumps(
                builder.to_jsonable(full))
            builder.ENRICH_BATCH = 1
            try:
                batched = await builder.build_report(db, case, full=False, generated_by=u)
            finally:
                builder.ENRICH_BATCH = 1000
            assert batched["iocs"] == iocs

            assert [e["filename"] for e in key["evidence"]] == ["live.eml"]
            assert "audit" not in key
            assert [a["action"] for a in full["audit"]] == ["update", "delete"]
            assert set(full["audit"][0]) == {"at", "actor_email", "entity", "action"}
            assert full["audit"][0]["actor_email"] == u.email

            m = key["milestones"]
            assert m["contained"] == {"at": BASE + timedelta(hours=3), "source": "computed"}
            assert m["first_seen"]["at"] == BASE - timedelta(days=2)
            assert m["recovered"]["source"] == "unknown" and m["ttr_seconds"] is None
            assert "out_of_order" in m

            assert key["case"]["title"] == HOSTILE and key["case"]["owner"] == u.email
            assert key["case"]["sla"]["response"] in ("met", "breached", "on_track", "at_risk", "not_tracked")
            assert key["actions"] == {"containment": [{"title": "Isolate host", "completed_at": BASE + timedelta(hours=3),
                                                       "completed_by": u.email}]}
            assert [o["title"] for o in key["outstanding"]] == ["Reimage"]
            assert key["meta"]["schema"] == "sochub.incident-report/1" and key["meta"]["full"] is False
            assert key["truncated"] == {}

            def walk(o):
                if isinstance(o, dict):
                    for v in o.values():
                        walk(v)
                elif isinstance(o, list):
                    for v in o:
                        walk(v)
                elif isinstance(o, datetime):
                    assert o.tzinfo is not None
            walk(full)
            js = builder.to_jsonable(full)
            json.dumps(js)
            assert js["milestones"]["contained"]["at"] == "2026-01-01T15:00:00+00:00"
    finally:
        async with AsyncSessionLocal() as db:
            for model, k in ((EnrichmentResult, "enr"), (CaseAttachment, "att"), (AuditLog, "audit"),
                             (TimelineEvent, "tl"), (CaseTask, "task"), (Alert, "alert"), (CaseArtifact, "ca"),
                             (Artifact, "art"), (IOC, "ioc"), (CaseReport, "rep"), (Case, "case"), (User, "user"),
                             (Tenant, "tenant")):
                if ids[k]:
                    await db.execute(delete(model).where(model.id.in_(ids[k])))
            await db.commit()
        async with AsyncSessionLocal() as db:
            n = (await db.execute(select(func.count()).select_from(Tenant).where(Tenant.slug.like("wf-test-%")))).scalar()
            assert n == 0, "leftover tenants"
        await engine.dispose()


def test_build_report():
    asyncio.run(_scenario())


def test_truncated_counts():
    rep = {"timeline": [0] * 501, "iocs": [0] * 3, "evidence": [], "audit": [0] * 2001}
    assert builder.compute_truncated(rep) == {"timeline": 1, "audit": 1}
