import asyncio
from types import SimpleNamespace

from sqlalchemy import delete, func, select

from app.db.session import AsyncSessionLocal, engine
from app.models.artifact import Artifact
from app.models.case import Case, TimelineEvent
from app.models.case_artifact import CaseArtifact
from app.models.copilot_session import CopilotMessage, CopilotSession
from tests.audit_fixtures import hdr, run

TITLE = "copilot-state-test MFA push"


def test_general_chat_create_case_then_follow_up(monkeypatch):
    import app.tasks.triage as tt
    monkeypatch.setattr(tt.run_case_triage_task, "delay", lambda *a: None)

    async def body(c, w):
        ids = SimpleNamespace()
        async with AsyncSessionLocal() as db:
            s = CopilotSession(tenant_id=w.ta, user_id=w.users["analyst"], case_id=None)
            db.add(s)
            await db.flush()
            create = CopilotMessage(session_id=s.id, role="assistant", content="create it?", action={
                "type": "create_case", "summary": "", "params": {
                    "title": TITLE, "severity": "high",
                    "artifacts": [{"value": "198.51.100.42", "artifact_type": "ip"}]}})
            db.add(create)
            await db.flush()
            follow = CopilotMessage(session_id=s.id, role="assistant", content="add ip?", action={
                "type": "add_artifact", "summary": "", "params": {"value": "203.0.113.9", "artifact_type": "ip"}})
            db.add(follow)
            await db.commit()
            ids.session, ids.create, ids.follow = s.id, create.id, follow.id
        h = hdr(w, "analyst")
        try:
            body_ = {"type": "create_case", "params": {}, "message_id": ids.create}
            r1 = (await c.post("/api/v1/copilot/actions/execute", json=body_, headers=h)).json()
            r2 = (await c.post("/api/v1/copilot/actions/execute", json=body_, headers=h)).json()
            assert r1["case_id"] and r2 == r1 and "198.51.100.42" in r1["message"]  # second Confirm: stored result
            ids.case = r1["case_id"]
            # follow-up in the general chat lands on the case created above
            r3 = await c.post("/api/v1/copilot/actions/execute", headers=h,
                              json={"type": "add_artifact", "params": {}, "message_id": ids.follow})
            assert r3.status_code == 200 and r3.json()["case_id"] == ids.case
            # someone else's message can't be used
            r4 = await c.post("/api/v1/copilot/actions/execute", headers=hdr(w, "admin"),
                              json={"type": "create_case", "params": {}, "message_id": ids.create})
            assert r4.status_code == 404
            async with AsyncSessionLocal() as db:
                assert (await db.execute(select(func.count()).select_from(Case).where(
                    Case.tenant_id == w.ta, Case.title == TITLE))).scalar_one() == 1
                values = set((await db.execute(select(Artifact.value).join(CaseArtifact).where(
                    CaseArtifact.case_id == ids.case))).scalars())
                assert values == {"198.51.100.42", "203.0.113.9"}
                assert (await db.get(CopilotMessage, ids.create)).action["result"]["case_id"] == ids.case
        finally:
            async with AsyncSessionLocal() as db:
                from app.models.case_triage import CaseTriageResult
                from app.models.notification import CaseFollower, Notification
                case_ids = list((await db.execute(select(Case.id).where(
                    Case.tenant_id == w.ta, Case.title == TITLE))).scalars())
                aids = list((await db.execute(select(CaseArtifact.artifact_id).where(
                    CaseArtifact.case_id.in_(case_ids)))).scalars())
                for model in (CaseArtifact, TimelineEvent, CaseTriageResult, Notification, CaseFollower):
                    await db.execute(delete(model).where(model.case_id.in_(case_ids)))
                if aids:
                    await db.execute(delete(Artifact).where(Artifact.id.in_(aids)))
                await db.execute(delete(Case).where(Case.id.in_(case_ids)))
                await db.execute(delete(CopilotSession).where(CopilotSession.id == ids.session))
                await db.commit()
    run(body)
    asyncio.run(engine.dispose())
