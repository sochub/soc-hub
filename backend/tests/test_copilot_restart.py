import asyncio

from sqlalchemy import func, select

from app.api.v1 import copilot as cp
from app.db.session import engine
from app.models.copilot_session import CopilotMessage, CopilotSession
from tests.audit_fixtures import hdr, run


def test_restart_clears_general_and_case_sessions(monkeypatch):
    async def fake_general(db, tenant_id, user_id):
        s = CopilotSession(tenant_id=tenant_id, user_id=user_id, case_id=None)
        db.add(s)
        await db.flush()
        db.add(CopilotMessage(session_id=s.id, role="assistant", content="welcome"))
        await db.commit()
        return (await db.execute(select(CopilotSession).options(cp.selectinload(CopilotSession.messages))
                                 .where(CopilotSession.id == s.id))).scalars().one()
    monkeypatch.setattr(cp, "_create_general_session_with_briefing", fake_general)

    async def body(c, w):
        h = hdr(w, "analyst")
        first = (await c.get("/api/v1/copilot/sessions/general", headers=h)).json()
        r = await c.post("/api/v1/copilot/sessions/general/restart", headers=h)  # was a 500: messages orphaned
        assert r.status_code == 200 and r.json()["id"] != first["id"]
        from app.db.session import AsyncSessionLocal
        async with AsyncSessionLocal() as db:
            left = (await db.execute(select(func.count()).select_from(CopilotMessage)
                                     .where(CopilotMessage.session_id == first["id"]))).scalar_one()
            assert left == 0
            await db.execute(cp.delete(CopilotMessage).where(CopilotMessage.session_id == r.json()["id"]))
            await db.execute(cp.delete(CopilotSession).where(CopilotSession.id == r.json()["id"]))
            await db.commit()
    run(body)
    asyncio.run(engine.dispose())
