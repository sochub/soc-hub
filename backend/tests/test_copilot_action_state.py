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
            await db.commit()
            ids.session, ids.create = s.id, create.id
        h = hdr(w, "analyst")
        try:
            body_ = {"type": "create_case", "params": {}, "message_id": ids.create}
            r1 = (await c.post("/api/v1/copilot/actions/execute", json=body_, headers=h)).json()
            r2 = await c.post("/api/v1/copilot/actions/execute", json=body_, headers=h)
            assert r1["case_id"] and "198.51.100.42" in r1["message"]
            assert r2.status_code == 409  # second Confirm: refused, the case is not created twice
            ids.case = r1["case_id"]
            # a follow-up proposed after that (the chat resolves its target when it proposes) lands on that case
            async with AsyncSessionLocal() as db:
                follow = CopilotMessage(session_id=ids.session, role="assistant", content="add ip?", action={
                    "type": "add_artifact", "summary": "", "case_id": ids.case,
                    "params": {"value": "203.0.113.9", "artifact_type": "ip"}})
                db.add(follow)
                await db.commit()
                ids.follow = follow.id
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


def test_copilot_llm_calls_hold_no_db_connection(monkeypatch):
    """Chat, the session briefing and analyze release their DB connection before awaiting the LLM (up to
    AI_TIMEOUT_SECONDS), so a few slow provider calls can't drain the pool."""
    from app.services.ai_service import AIService
    held = []

    def fake(reply):
        async def call(self, *a, **k):
            held.append(engine.sync_engine.pool.checkedout())
            return reply
        return call
    monkeypatch.setattr(AIService, "chat", fake("hi there"))
    monkeypatch.setattr(AIService, "generate_general_welcome", fake("welcome"))
    monkeypatch.setattr(AIService, "generate_welcome_briefing", fake("briefing"))
    monkeypatch.setattr(AIService, "analyze_case", fake("analysis"))

    async def body(c, w):
        h = hdr(w, "analyst")
        sids = []
        try:
            r = await c.get("/api/v1/copilot/sessions/general", headers=h)
            assert r.status_code == 200, r.text
            sids.append(r.json()["id"])
            r = await c.post("/api/v1/copilot/chat", headers=h, json={"session_id": sids[0], "message": "hello"})
            assert r.status_code == 200 and r.json()["content"] == "hi there", r.text
            r = await c.post(f"/api/v1/copilot/sessions/{w.case}/restart", headers=h)
            assert r.status_code == 200 and r.json()["messages"][0]["content"] == "briefing", r.text
            sids.append(r.json()["id"])
            r = await c.post("/api/v1/copilot/analyze", headers=h, json={"case_id": w.case})
            assert r.status_code == 200 and r.json()["analysis"] == "analysis", r.text
            assert len(held) == 4 and held == [0, 0, 0, 0], held
            async with AsyncSessionLocal() as db:
                roles = (await db.execute(select(CopilotMessage.role).where(CopilotMessage.session_id == sids[0])
                                          .order_by(CopilotMessage.created_at, CopilotMessage.id))).scalars().all()
                assert roles == ["assistant", "user", "assistant"]
        finally:
            async with AsyncSessionLocal() as db:
                await db.execute(delete(CopilotSession).where(CopilotSession.id.in_(sids)))
                await db.commit()
    run(body)
    asyncio.run(engine.dispose())
