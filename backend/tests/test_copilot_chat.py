"""The copilot chat's action resolution and the propose -> confirm lifecycle, with the LLM stubbed."""
import asyncio
import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import delete, func, select

from app.ai.errors import AIUnavailable
from app.db.session import AsyncSessionLocal, engine
from app.models.artifact import Artifact
from app.models.case import Case, CaseStatus, TimelineEvent
from app.models.case_artifact import CaseArtifact
from app.models.copilot_session import CopilotMessage, CopilotSession
from app.models.membership import TenantMembership
from app.models.user import User
from app.services import ai_service
from tests.audit_fixtures import hdr, run
from tests.session_fixtures import make_web_token

CHAT, EXECUTE, DISMISS = "/api/v1/copilot/chat", "/api/v1/copilot/actions/execute", "/api/v1/copilot/actions/dismiss"
NO_ACTION = json.dumps({"type": None})


def block(action: dict, fence: str = "action") -> str:
    return f"On it.\n```{fence}\n{json.dumps(action)}\n```"


@pytest.fixture
def llm(monkeypatch):
    """Scripted model: `llm.replies` are returned in order (the last one repeats); calls are recorded."""
    class Calls(list):
        replies = ["Sure."]
        error = None
    calls = Calls()

    async def complete(messages, *, tenant_id, temperature=None, json_mode=False):
        calls.append({"messages": messages, "json_mode": json_mode})
        await asyncio.sleep(0.01)  # let concurrent requests interleave
        if calls.error:
            raise calls.error
        return calls.replies.pop(0) if len(calls.replies) > 1 else calls.replies[0]
    monkeypatch.setattr(ai_service.llm, "complete", complete)
    return calls


async def _session(w, case_id=None, messages=(), who="analyst"):
    """A copilot session (general unless case_id) seeded with messages a second apart; returns (id, message ids)."""
    t0 = datetime.now(timezone.utc) - timedelta(hours=1)
    async with AsyncSessionLocal() as db:
        s = CopilotSession(tenant_id=w.ta, user_id=w.users[who], case_id=case_id)
        db.add(s)
        await db.flush()
        rows = [CopilotMessage(session_id=s.id, created_at=t0 + timedelta(seconds=i),
                               **{"role": "assistant", "content": "x", **m}) for i, m in enumerate(messages)]
        db.add_all(rows)
        await db.commit()
        return s.id, [r.id for r in rows]


def _world(body):
    """Run `body(c, w)`; afterwards delete this run's copilot sessions and whatever its actions left on its cases."""
    async def wrapped(c, w):
        try:
            await body(c, w)
        finally:
            async with AsyncSessionLocal() as db:
                from app.models.case_triage import CaseTriageResult
                from app.models.notification import CaseFollower, Notification
                case_ids = list((await db.execute(select(Case.id).where(Case.tenant_id == w.ta))).scalars())
                extra = [i for i in case_ids if i != w.case]
                await db.execute(delete(CopilotSession).where(CopilotSession.tenant_id.in_([w.ta, w.tb])))
                aids = list((await db.execute(select(CaseArtifact.artifact_id).where(
                    CaseArtifact.case_id.in_(case_ids)))).scalars())
                for model in (CaseTriageResult, Notification, CaseFollower, CaseArtifact, TimelineEvent):
                    await db.execute(delete(model).where(model.case_id.in_(case_ids)))
                if aids:
                    await db.execute(delete(Artifact).where(Artifact.id.in_(aids)))
                await db.execute(delete(Case).where(Case.id.in_(extra)))
                await db.commit()
    run(wrapped)
    asyncio.run(engine.dispose())


async def _messages(session_id):
    async with AsyncSessionLocal() as db:
        return (await db.execute(select(CopilotMessage).where(CopilotMessage.session_id == session_id)
                                 .order_by(CopilotMessage.id))).scalars().all()


async def _notes(case_id):
    async with AsyncSessionLocal() as db:
        return (await db.execute(select(TimelineEvent.content).where(
            TimelineEvent.case_id == case_id, TimelineEvent.event_type == "comment"))).scalars().all()


# ---- what the model's reply turns into ------------------------------------------------------------

def test_model_action_shapes(llm):
    """Non-dict params are dropped (never stored, never a 500), flat params are accepted, a block the model
    left unterminated still counts, and a sample-JSON answer is displayed untouched."""
    sample = 'Here is a sample payload:\n```json\n{"title": "Suspicious login", "severity": "high"}\n```\nUse it as is.'

    async def body(c, w):
        h = hdr(w, "analyst")
        sid, _ = await _session(w, w.case)

        async def say(message, *replies):
            llm.replies = list(replies)
            del llm[:]
            r = await c.post(CHAT, headers=h, json={"session_id": sid, "case_id": w.case, "message": message})
            assert r.status_code == 200, r.text
            return r.json()

        out = await say("hello there", block({"type": "add_artifact", "params": "203.0.113.5"}))
        assert out["action"] is None and (await _messages(sid))[-1].action is None

        out = await say("hello again", block({"type": "add_artifact", "value": "203.0.113.6", "artifact_type": "ip"}))
        assert out["action"]["params"] == {"value": "203.0.113.6", "artifact_type": "ip"}
        assert out["action"]["case_id"] == w.case and out["action"]["case_title"] == "audit-case"
        assert out["content"] == "On it."

        unterminated = 'Adding it.\n```action\n' + json.dumps(
            {"type": "add_artifact", "summary": "s", "params": {"value": "203.0.113.7", "artifact_type": "ip"}})
        out = await say("and one more", unterminated)
        assert out["action"]["params"]["value"] == "203.0.113.7" and out["content"] == "Adding it."

        out = await say("show me a sample JSON payload for a webhook", sample)
        assert out["content"] == sample and out["action"] is None and len(llm) == 1
    _world(body)


def test_second_pass_trigger_and_history(llm):
    """The constrained second pass runs for a command the first reply left without a block — with the previous
    turns — and not for words that merely contain a keyword."""
    action = {"type": "add_artifact", "summary": "s", "params": {"value": "203.0.113.8", "artifact_type": "ip"}}

    async def body(c, w):
        h = hdr(w, "analyst")
        sid, _ = await _session(w, w.case, [{"role": "user", "content": "203.0.113.8 keeps beaconing"},
                                            {"content": "Want me to add 203.0.113.8 as an artifact?"}])
        for message in ("why was this blocked?", "the outage hit the staging markdown docs, as you denote"):
            del llm[:]
            llm.replies = ["It was a false positive."]
            r = await c.post(CHAT, headers=h, json={"session_id": sid, "message": message})
            assert r.status_code == 200 and r.json()["action"] is None and len(llm) == 1, (message, len(llm))

        del llm[:]
        llm.replies = ["I'll add it.", json.dumps(action)]
        r = await c.post(CHAT, headers=h, json={"session_id": sid, "message": "yes, add it"})
        assert r.json()["action"]["params"]["value"] == "203.0.113.8", r.text
        assert len(llm) == 2 and llm[1]["json_mode"] is True
        asked = llm[1]["messages"][-1]["content"]
        assert "assistant: It was a false positive." in asked and asked.endswith("Analyst request: yes, add it")
        assert "203.0.113.8 keeps beaconing" not in asked  # only the last few turns
    _world(body)


def test_provider_failure_is_an_error_not_a_message(llm):
    """An unavailable provider answers 503 and stores nothing (no "unavailable" turn replayed as history, no
    orphan user turn), and the second pass is not attempted."""
    async def body(c, w):
        sid, _ = await _session(w, w.case)
        llm.error = AIUnavailable("APIConnectionError: cannot reach http://10.1.2.3:11434")
        r = await c.post(CHAT, headers=hdr(w, "analyst"), json={"session_id": sid, "message": "add a tag please"})
        assert r.status_code == 503 and "unavailable" in r.json()["detail"].lower() and "10.1.2.3" not in r.text
        assert len(llm) == 1 and await _messages(sid) == []
    _world(body)


def test_declarative_chip_only_for_findings(llm):
    async def body(c, w):
        h = hdr(w, "analyst")
        sid, _ = await _session(w, w.case)
        llm.replies = ["Noted.", NO_ACTION]
        for message, chip in [
            ("Update the severity to high and close the case please", False),
            ("Please summarize what we know so far about this incident", False),
            ("We isolated the host and reset the user credentials this morning.", True),
        ]:
            r = await c.post(CHAT, headers=h, json={"session_id": sid, "message": message})
            got = [s["type"] for s in r.json()["suggestions"] or []]
            assert (got == ["add_timeline_note"]) is chip, (message, got)
    _world(body)


def test_note_requests_are_deterministic_or_composed(llm):
    """Literal note text comes from the analyst's message, whatever the model says; a compose-style request
    goes to the model-composition path and never saves the instruction itself."""
    async def body(c, w):
        h = hdr(w, "analyst")
        sid, _ = await _session(w, w.case)
        llm.replies = [block({"type": "add_timeline_note", "params": {"content": "ng server restarted"}})]
        r = await c.post(CHAT, headers=h, json={
            "session_id": sid, "message": "Add a note that the syslog-ng server on host-1 was restarted"})
        assert r.json()["action"]["params"]["content"] == "the syslog-ng server on host-1 was restarted"

        del llm[:]
        llm.replies = ["Sure.", "Phishing mail from billing@evil.example was reported and quarantined."]
        r = await c.post(CHAT, headers=h, json={
            "session_id": sid, "message": "Write a comment summarizing the phishing analysis"})
        assert r.json()["action"]["params"]["content"] == \
            "Phishing mail from billing@evil.example was reported and quarantined."
        assert len(llm) == 2  # chat + composition
    _world(body)


def test_create_case_takes_the_newest_indicators(llm):
    async def body(c, w):
        old = [{"role": "user", "content": " ".join(f"10.9.{i}.{j}" for j in range(1, 9))} for i in range(6)]
        sid, _ = await _session(w, None, old)
        llm.replies = [block({"type": "create_case", "params": {"title": "copilot-chat-test beacon", "severity": "high"}})]
        r = await c.post(CHAT, headers=hdr(w, "analyst"), json={
            "session_id": sid, "message": "open a case for 198.51.100.77 beaconing to evil-c2.support"})
        values = [a["value"] for a in r.json()["action"]["params"]["artifacts"]]
        assert values[:2] == ["198.51.100.77", "evil-c2.support"] and len(values) == 10, values
        assert not any(v.startswith(("10.9.0.", "10.9.1.", "10.9.2.")) for v in values), values  # only the last turns
    _world(body)


# ---- context sent to the model ---------------------------------------------------------------------

def test_long_timeline_sends_newest_and_pinned(llm):
    async def body(c, w):
        t0 = datetime.now(timezone.utc) - timedelta(days=2)
        async with AsyncSessionLocal() as db:
            db.add_all([TimelineEvent(case_id=w.case, event_type="comment", content=f"entry-{i:02d}-end",
                                      created_at=t0 + timedelta(minutes=i), pinned=(i == 2)) for i in range(40)])
            await db.commit()
        sid, _ = await _session(w, w.case)
        r = await c.post(CHAT, headers=hdr(w, "analyst"), json={"session_id": sid, "message": "what happened last?"})
        assert r.status_code == 200, r.text
        system = "\n".join(m["content"] for m in llm[0]["messages"] if m["role"] == "system")
        shown = [i for i in range(40) if f"entry-{i:02d}-end" in system]
        assert shown == [2, *range(10, 40)], shown  # pinned + the 30 most recent, oldest first
        assert system.index("entry-10-end") < system.index("entry-39-end")
        assert "9 earliest entries are omitted" in system
        assert system.index("## Taking Actions") < system.index("--- CASE CONTEXT ---")  # guide before the data
    _world(body)


def test_prompt_budget_drops_old_turns_then_timeline(llm):
    svc = ai_service.AIService(tenant_id=1)
    history = [{"role": "user" if i % 2 == 0 else "assistant", "content": f"turn-{i:02d} " + "x" * 3000}
               for i in range(21)]
    events = [{"type": "comment", "content": f"entry-{i:02d} " + "y" * 400, "created_at": "t"} for i in range(30)]
    asyncio.run(svc.chat(history, {"title": "T", "timeline_events": events}))
    sent = llm[0]["messages"]
    assert sum(len(m["content"]) for m in sent) <= ai_service.PROMPT_CHAR_BUDGET
    assert sent[-1]["content"].startswith("turn-20") and not any("turn-00" in m["content"] for m in sent)
    assert "## Taking Actions" in sent[0]["content"]

    del llm[:]
    asyncio.run(svc.chat([{"role": "user", "content": "z" * 9000}], {"title": "T", "timeline_events": events * 2}))
    system = llm[0]["messages"][0]["content"]
    assert "entry-29" in system and "earliest entries are omitted" in system  # timeline shortened, newest kept


# ---- which case a proposal targets -----------------------------------------------------------------

def test_general_session_target_case(llm, monkeypatch):
    import app.tasks.triage as tt
    monkeypatch.setattr(tt.run_case_triage_task, "delay", lambda *a: None)
    add = {"type": "add_artifact", "summary": "s", "params": {"value": "203.0.113.9", "artifact_type": "ip"}}

    async def body(c, w):
        h = hdr(w, "analyst")
        async with AsyncSessionLocal() as db:
            gone = Case(title="copilot-chat-test gone", tenant_id=w.ta)
            other = Case(title="copilot-chat-test other tenant", tenant_id=w.tb)
            db.add_all([gone, other])
            await db.commit()
            gone_id, other_id = gone.id, other.id
        try:
            sid, (mid,) = await _session(w, None, [{"action": {**add, "case_id": gone_id}}])
            llm.replies = [block(add)]

            async def propose(message):
                r = await c.post(CHAT, headers=h, json={"session_id": sid, "message": message})
                assert r.status_code == 200, r.text
                return r.json()

            # no case named and none created here: the proposal has no target, and Confirm is refused
            out = await propose("add 203.0.113.9 as an artifact")
            assert "case_id" not in out["action"] or out["action"]["case_id"] is None
            r = await c.post(EXECUTE, headers=h, json={"type": "add_artifact", "params": {}, "message_id": out["id"]})
            assert r.status_code == 400 and "case" in r.json()["detail"].lower()

            # another tenant's case is not a target either
            out = await propose(f"add 203.0.113.9 as an artifact on case #{other_id}")
            assert out["action"].get("case_id") is None

            # a named case is stored on the proposal with its title, and is where the write lands
            out = await propose(f"add 203.0.113.9 as an artifact to case #{w.case}")
            assert out["action"]["case_id"] == w.case and out["action"]["case_title"] == "audit-case"
            r = await c.post(EXECUTE, headers=h, json={"type": "add_artifact", "params": {}, "message_id": out["id"]})
            assert r.status_code == 200 and r.json()["case_id"] == w.case, r.text

            # a case created from this conversation becomes the default target of what follows
            llm.replies = [block({"type": "create_case", "params": {"title": "copilot-chat-test created"}})]
            created = await propose("open a case for this")
            r = await c.post(EXECUTE, headers=h, json={"type": "create_case", "params": {}, "message_id": created["id"]})
            new_id = r.json()["case_id"]
            llm.replies = [block(add)]
            out = await propose("add 203.0.113.9 as an artifact")
            assert out["action"]["case_id"] == new_id and out["action"]["case_title"] == "copilot-chat-test created"

            # a target deleted since: the session read says so, and Confirm is a clean 404
            async with AsyncSessionLocal() as db:
                await db.execute(delete(Case).where(Case.id == gone_id))
                await db.commit()
            read = (await c.get("/api/v1/copilot/sessions/general", headers=h)).json()
            stale = next(m for m in read["messages"] if m["id"] == mid)["action"]
            assert stale["case_missing"] is True and stale["case_title"] is None
            r = await c.post(EXECUTE, headers=h, json={"type": "add_artifact", "params": {}, "message_id": mid})
            assert r.status_code == 404
        finally:
            async with AsyncSessionLocal() as db:
                await db.execute(delete(Case).where(Case.id.in_([gone_id, other_id])))
                await db.commit()
    _world(body)


def test_chat_takes_case_from_session(llm):
    async def body(c, w):
        h = hdr(w, "analyst")
        case_sid, _ = await _session(w, w.case)
        general_sid, _ = await _session(w, None)
        for sid, sent_case in ((case_sid, w.case + 1), (general_sid, w.case)):
            r = await c.post(CHAT, headers=h, json={"session_id": sid, "case_id": sent_case, "message": "hi"})
            assert r.status_code == 400, r.text
            assert await _messages(sid) == [] and len(llm) == 0
        # case_id left out: the session's case is used
        r = await c.post(CHAT, headers=h, json={"session_id": case_sid, "message": "hi"})
        assert r.status_code == 200 and "--- CASE CONTEXT ---" in llm[0]["messages"][0]["content"]
    _world(body)


def test_concurrent_session_reads_create_one_session(llm):
    async def body(c, w):
        h = hdr(w, "analyst")
        a, b = await asyncio.gather(c.get("/api/v1/copilot/sessions/general", headers=h),
                                    c.get("/api/v1/copilot/sessions/general", headers=h))
        assert a.status_code == b.status_code == 200 and a.json()["id"] == b.json()["id"]
        assert len(a.json()["messages"]) == len(b.json()["messages"]) == 1
        async with AsyncSessionLocal() as db:
            assert (await db.execute(select(func.count()).select_from(CopilotSession).where(
                CopilotSession.user_id == w.users["analyst"]))).scalar_one() == 1
    _world(body)


# ---- executing proposals ----------------------------------------------------------------------------

def test_execute_permissions_and_tenant_scope():
    async def body(c, w):
        async with AsyncSessionLocal() as db:
            viewer = User(email=f"wf-test-{w.h}-viewer@example.com", hashed_password="x", is_active=True)
            db.add(viewer)
            await db.flush()
            db.add(TenantMembership(user_id=viewer.id, tenant_id=w.ta, role="viewer"))
            await db.commit()
            w.users["viewer"] = viewer.id  # removed with the world's other users
            w.tokens["viewer"] = await make_web_token(None, viewer, w.ta)
        note = {"type": "add_timeline_note", "summary": "", "params": {"content": "copilot-chat-test note"}}
        sid, (mid,) = await _session(w, w.case, [{"action": note}])
        viewer_sid, (viewer_mid,) = await _session(w, w.case, [{"action": note}], who="viewer")

        r = await c.post(EXECUTE, headers=hdr(w, "viewer"), json={**note, "case_id": w.case})
        assert r.status_code == 403
        r = await c.post(EXECUTE, headers=hdr(w, "viewer"), json={**note, "message_id": viewer_mid})
        assert r.status_code == 403
        assert "state" not in (await _messages(viewer_sid))[0].action  # the refused claim was released
        # another tenant: neither the case nor the message exists for them
        r = await c.post(EXECUTE, headers=hdr(w, "admin_b"), json={**note, "case_id": w.case})
        assert r.status_code == 404
        for path in (EXECUTE, DISMISS):
            r = await c.post(path, headers=hdr(w, "admin_b"), json={**note, "message_id": mid})
            assert r.status_code == 404, path
        r = await c.post(EXECUTE, headers=hdr(w, "admin"), json={**note, "message_id": mid})  # same tenant, not theirs
        assert r.status_code == 404
        assert await _notes(w.case) == []
    _world(body)


def test_proposal_lifecycle():
    """Cancel/dismiss are stored and final, a write runs once even when two Confirms race, and a read keeps its
    stored result."""
    note = {"type": "add_timeline_note", "summary": "", "params": {"content": "copilot-chat-test once"}}
    chip = {"type": "add_timeline_note", "summary": "", "params": {"content": "copilot-chat-test chip"}}

    async def body(c, w):
        h = hdr(w, "analyst")
        sid, (cancelled, twice, related) = await _session(w, w.case, [
            {"action": note, "suggestions": [chip, chip]}, {"action": note},
            {"action": {"type": "find_related", "summary": "", "params": {"value": "203.0.113.200"}}}])

        r = await c.post(DISMISS, headers=h, json={"message_id": cancelled, "state": "cancelled"})
        assert r.status_code == 200 and r.json()["state"] == "cancelled"
        r = await c.post(DISMISS, headers=h, json={"message_id": cancelled, "suggestion_index": 1})
        assert r.json()["state"] == "dismissed"
        for index in (None, 1):
            r = await c.post(EXECUTE, headers=h, json={**note, "message_id": cancelled, "suggestion_index": index})
            assert r.status_code == 409, r.text
        read = (await c.get(f"/api/v1/copilot/sessions/{w.case}", headers=h)).json()["messages"][0]
        assert read["action"]["state"] == "cancelled"
        assert [s["state"] for s in read["suggestions"]] == [None, "dismissed"]
        assert await _notes(w.case) == []

        confirm = {**note, "message_id": twice}
        a, b = await asyncio.gather(c.post(EXECUTE, headers=h, json=confirm), c.post(EXECUTE, headers=h, json=confirm))
        assert sorted([a.status_code, b.status_code]) == [200, 409], (a.text, b.text)
        assert (await c.post(EXECUTE, headers=h, json=confirm)).status_code == 409
        assert await _notes(w.case) == ["copilot-chat-test once"]
        stored = (await _messages(sid))[1].action
        assert stored["result"]["ok"] is True and "state" not in stored
        # dismissing something that already ran changes nothing
        assert (await c.post(DISMISS, headers=h, json={"message_id": twice})).json().get("state") is None

        find = {"type": "find_related", "params": {}, "message_id": related}
        first = await c.post(EXECUTE, headers=h, json=find)
        assert first.status_code == 200 and (await _messages(sid))[2].action["result"]["message"] == first.json()["message"]
        assert (await c.post(EXECUTE, headers=h, json=find)).json() == first.json()
    _world(body)


def test_stored_malformed_action_does_not_break_the_session():
    async def body(c, w):
        h = hdr(w, "analyst")
        sid, (bad, good) = await _session(w, w.case, [
            {"action": {"type": "add_artifact", "summary": "", "params": "203.0.113.5"}},
            {"action": {"type": "add_artifact", "summary": "", "params": {"value": "203.0.113.5"}}}])
        r = await c.get(f"/api/v1/copilot/sessions/{w.case}", headers=h)
        assert r.status_code == 200, r.text
        assert [m["action"] is None for m in r.json()["messages"]] == [True, False]
        r = await c.post(EXECUTE, headers=h, json={"type": "add_artifact", "params": {}, "message_id": bad})
        assert r.status_code == 404
    _world(body)


def test_update_case_merges_tags_and_collects_closure():
    async def body(c, w):
        h = hdr(w, "analyst")
        async with AsyncSessionLocal() as db:
            (await db.get(Case, w.case)).tags = ["phishing", "vip"]
            await db.commit()
        sid, (one, many, bad, close) = await _session(w, w.case, [
            {"action": {"type": "update_case", "summary": "", "params": {"tags": "urgent"}}},
            {"action": {"type": "update_case", "summary": "", "params": {"tags": ["vip", " exec ", "urgent"]}}},
            {"action": {"type": "update_case", "summary": "", "params": {"tags": [1, 2]}}},
            {"action": {"type": "update_case", "summary": "", "params": {"status": "closed"}}}])

        async def confirm(mid, **extra):
            return await c.post(EXECUTE, headers=h, json={"type": "update_case", "params": {}, "message_id": mid, **extra})

        async def case():
            async with AsyncSessionLocal() as db:
                return await db.get(Case, w.case)

        assert (await confirm(one)).status_code == 200
        assert (await case()).tags == ["phishing", "vip", "urgent"]  # one tag added, not u-r-g-e-n-t
        assert (await confirm(many)).status_code == 200
        assert (await case()).tags == ["phishing", "vip", "urgent", "exec"]
        assert (await confirm(bad)).status_code == 400

        r = await confirm(close)
        assert r.status_code == 422 and "outcome" in r.text.lower()
        assert (await confirm(close, outcome="nonsense", closing_note="x")).status_code == 400
        assert (await case()).status != CaseStatus.CLOSED
        # each refusal handed the proposal back, so the analyst can fill the fields in and confirm
        r = await confirm(close, outcome="false_positive", closing_note="Benign scanner, allow-listed.")
        assert r.status_code == 200, r.text
        closed = await case()
        assert (closed.status, closed.outcome, closed.closing_note) == \
            (CaseStatus.CLOSED, "false_positive", "Benign scanner, allow-listed.")
    _world(body)


def test_silent_model_with_an_action_is_not_called_no_response(llm):
    """A model that answers an "act on this" turn with no text still gets a card; the card's summary is the reply.
    With nothing to show at all, the reply says so."""
    async def body(c, w):
        h = hdr(w, "analyst")
        sid, _ = await _session(w, w.case)
        llm.replies = [""]
        r = await c.post(CHAT, headers=h, json={"session_id": sid, "case_id": w.case,
                                                "message": "Add a note that the finance mailbox was purged"})
        assert r.status_code == 200, r.text
        m = r.json()
        assert m["action"]["params"]["content"] == "the finance mailbox was purged"
        assert m["content"] == "Add this note to the case timeline" and "No response" not in m["content"]
        r = await c.post(CHAT, headers=h, json={"session_id": sid, "case_id": w.case, "message": "hello there"})
        assert r.json()["content"] == ai_service.NO_REPLY and r.json().get("action") is None
    _world(body)


def test_tag_request_does_not_touch_status_or_severity(llm):
    """The model over-fills update_case on "tag this ..." (an invented status, a severity nobody asked for): the
    proposal keeps the tags only, and confirming it leaves status and severity alone."""
    async def body(c, w):
        h = hdr(w, "analyst")
        sid, _ = await _session(w, w.case)
        async with AsyncSessionLocal() as db:
            case = await db.get(Case, w.case)
            before = (case.status, case.severity)
        llm.replies = [block({"type": "update_case", "summary": "",
                              "params": {"status": "exfiltration_tagged", "severity": "medium", "tags": ["data_exfiltration"]}})]
        r = await c.post(CHAT, headers=h, json={"session_id": sid, "case_id": w.case,
                                                "message": "tag this case a data exfiltration"})
        assert r.status_code == 200, r.text
        m = r.json()
        assert m["action"]["params"] == {"tags": ["data_exfiltration"]}
        r = await c.post(EXECUTE, headers=h, json={"type": "update_case", "params": {}, "message_id": m["id"]})
        assert r.status_code == 200, r.text
        async with AsyncSessionLocal() as db:
            case = await db.get(Case, w.case)
            assert (case.status, case.severity) == before and "data_exfiltration" in case.tags
        # an invented search value is dropped, so find-related correlates the case's own indicators
        llm.replies = [block({"type": "find_related", "summary": "s", "params": {"value": "phishing campaign"}})]
        r = await c.post(CHAT, headers=h, json={"session_id": sid, "case_id": w.case, "message": "find related cases"})
        assert r.json()["action"]["type"] == "find_related" and r.json()["action"]["params"] == {}
    _world(body)


def test_explicit_instruction_survives_a_silent_model(llm):
    """The model returns nothing (reply and second pass): a stated severity change still gets its card."""
    async def body(c, w):
        h = hdr(w, "analyst")
        sid, _ = await _session(w, w.case)
        llm.replies = [""]
        r = await c.post(CHAT, headers=h, json={"session_id": sid, "case_id": w.case, "message": "raise the severity to high"})
        m = r.json()
        assert m["action"]["type"] == "update_case" and m["action"]["params"] == {"severity": "high"}, m
        assert "No response" not in m["content"]
        r = await c.post(CHAT, headers=h, json={"session_id": sid, "case_id": w.case,
                                                "message": "should we raise the severity to high?"})
        assert r.json().get("action") is None
    _world(body)
