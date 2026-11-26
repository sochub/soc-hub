"""Editing a proposal before Confirm, defanged indicators in chat, and the expiry of a stuck "running" claim."""
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.db.session import AsyncSessionLocal
from app.models.artifact import Artifact
from app.models.case import Case
from app.models.case_artifact import CaseArtifact
from app.models.membership import TenantMembership
from app.models.user import User
from app.schemas.copilot_action import CLAIM_TTL, live_state
from tests.audit_fixtures import hdr
from tests.session_fixtures import make_web_token
from tests.test_copilot_chat import (  # noqa: F401 (llm: fixture)
    CHAT, DISMISS, EXECUTE, NO_ACTION, _messages, _notes, _session, _world, block, llm,
)

NOTE = {"type": "add_timeline_note", "summary": "", "params": {"content": "copilot-edit-test proposed"}}


async def _artifacts(case_id):
    async with AsyncSessionLocal() as db:
        return {(a.value, a.artifact_type.value, a.description) for a in (await db.execute(
            select(Artifact).join(CaseArtifact).where(CaseArtifact.case_id == case_id))).scalars()}


def test_edits_are_applied_validated_and_stored(monkeypatch):
    import app.tasks.triage as tt
    monkeypatch.setattr(tt.run_case_triage_task, "delay", lambda *a: None)

    async def body(c, w):
        h = hdr(w, "analyst")
        async with AsyncSessionLocal() as db:
            (await db.get(Case, w.case)).tags = ["vip"]
            await db.commit()
        sid, (note, artifact, create, tags, untouched) = await _session(w, w.case, [
            {"action": NOTE},
            {"action": {"type": "add_artifact", "summary": "", "params": {"value": "203.0.113.50", "artifact_type": "ip"}}},
            {"action": {"type": "create_case", "summary": "", "params": {
                "title": "copilot-edit-test proposed", "severity": "low",
                "artifacts": [{"value": "203.0.113.51", "artifact_type": "ip"}]}}},
            {"action": {"type": "update_case", "summary": "", "params": {"tags": ["wrong"], "severity": "high"}}},
            {"action": NOTE}])

        async def confirm(mid, edits=None, who="analyst", **extra):
            payload = {"type": "x", "params": {}, "message_id": mid, **extra}
            return await c.post(EXECUTE, headers=hdr(w, who), json=payload if edits is None else {**payload, "edits": edits})

        async def stored(mid):
            return next(m.action for m in await _messages(sid) if m.id == mid)

        # refused edits change nothing and leave the proposal confirmable: unknown key, wrong type, bad enum, blank
        for mid, edits in ((note, {"case_id": w.case + 1}), (note, {"type": "create_case"}), (note, {"content": 5}),
                           (note, {"content": "   "}), (note, {"content": "x" * 16_001}),
                           (artifact, {"artifact_type": "weapon"}), (artifact, {"value": ""}),
                           (create, {"severity": "catastrophic"}), (create, {"artifacts": []}),
                           (tags, {"status": "closed"}), (tags, {"tags": "one"}), (tags, {"tags": [1]})):
            r = await confirm(mid, edits)
            assert r.status_code == 422, (edits, r.status_code, r.text)
            assert "state" not in await stored(mid) and "result" not in await stored(mid)
        assert await _notes(w.case) == [] and await _artifacts(w.case) == set()
        r = await c.post(EXECUTE, headers=h, json={**NOTE, "case_id": w.case, "edits": {"content": "no proposal"}})
        assert r.status_code == 422  # edits only apply over a stored proposal

        # a human's edit is taken as written, even text the model would not be allowed to propose
        r = await confirm(note, {"content": "  Added a note  "})
        assert r.status_code == 200, r.text
        assert await _notes(w.case) == ["Added a note"]
        after = await stored(note)
        assert after["params"] == {"content": "Added a note"} and after["edited"] is True and after["result"]["ok"]

        r = await confirm(artifact, {"value": "evil-c2.example", "artifact_type": "DOMAIN", "description": "C2 host"})
        assert r.status_code == 200 and await _artifacts(w.case) == {("evil-c2.example", "domain", "C2 host")}

        r = await confirm(create, {"title": "copilot-edit-test final", "severity": "critical", "description": ""})
        new_id = r.json()["case_id"]
        async with AsyncSessionLocal() as db:
            made = await db.get(Case, new_id)
            assert (made.title, made.severity.value) == ("copilot-edit-test final", "critical")
        assert {a[0] for a in await _artifacts(new_id)} == {"203.0.113.51"}  # the attached indicators stay
        assert (await stored(create))["params"]["title"] == "copilot-edit-test final"

        r = await confirm(tags, {"tags": ["phishing", " exec "]})
        async with AsyncSessionLocal() as db:
            case = await db.get(Case, w.case)
            assert case.tags == ["vip", "phishing", "exec"] and case.severity.value == "high"  # the rest as proposed

        # no edits: stored exactly as proposed, not marked edited
        assert (await confirm(untouched)).status_code == 200
        assert "edited" not in await stored(untouched)
        read = (await c.get(f"/api/v1/copilot/sessions/{w.case}", headers=h)).json()["messages"]
        assert [m["action"]["edited"] for m in read] == [True, True, True, True, False]
    _world(body)


def test_edits_do_not_bypass_role_or_lifecycle():
    async def body(c, w):
        async with AsyncSessionLocal() as db:
            viewer = User(email=f"wf-test-{w.h}-viewer@example.com", hashed_password="x", is_active=True)
            db.add(viewer)
            await db.flush()
            db.add(TenantMembership(user_id=viewer.id, tenant_id=w.ta, role="viewer"))
            await db.commit()
            w.users["viewer"] = viewer.id  # removed with the world's other users
            w.tokens["viewer"] = await make_web_token(None, viewer, w.ta)
        edit = {"type": "x", "params": {}, "edits": {"content": "copilot-edit-test edited"}}
        viewer_sid, (viewer_mid,) = await _session(w, w.case, [{"action": NOTE}], who="viewer")
        r = await c.post(EXECUTE, headers=hdr(w, "viewer"), json={**edit, "message_id": viewer_mid})
        assert r.status_code == 403
        assert (await _messages(viewer_sid))[0].action == NOTE  # handed back as proposed, edits not kept

        sid, (dismissed, done) = await _session(w, w.case, [{"action": NOTE}, {"action": NOTE}])
        h = hdr(w, "analyst")
        await c.post(DISMISS, headers=h, json={"message_id": dismissed, "state": "cancelled"})
        assert (await c.post(EXECUTE, headers=h, json={**edit, "message_id": dismissed})).status_code == 409
        assert (await c.post(EXECUTE, headers=h, json={"type": "x", "params": {}, "message_id": done})).status_code == 200
        assert (await c.post(EXECUTE, headers=h, json={**edit, "message_id": done})).status_code == 409
        assert await _notes(w.case) == ["copilot-edit-test proposed"]
    _world(body)


def test_defanged_indicators_in_chat(llm):  # noqa: F811
    said = "the C2 is hxxp://evil-c2[.]example[.]com/gate and 10[.]0[.]0[.]5, sender bob[at]evil-mail[.]net"
    real = ["http://evil-c2.example.com/gate", "bob@evil-mail.net", "10.0.0.5"]

    async def body(c, w):
        h = hdr(w, "analyst")
        sid, _ = await _session(w, w.case)
        llm.replies = ["Noted.", NO_ACTION]
        r = await c.post(CHAT, headers=h, json={"session_id": sid, "message": said})
        suggestions = r.json()["suggestions"]
        assert [s["params"]["value"] for s in suggestions] == real, suggestions
        r = await c.post(EXECUTE, headers=h, json={"type": "x", "params": {}, "message_id": r.json()["id"],
                                                   "suggestion_index": 2})
        assert r.status_code == 200 and {a[:2] for a in await _artifacts(w.case)} == {("10.0.0.5", "ip")}

        general_sid, _ = await _session(w, None)
        llm.replies = [block({"type": "create_case", "params": {"title": "copilot-edit-test c2"}})]
        r = await c.post(CHAT, headers=h, json={"session_id": general_sid, "message": "open a case: " + said})
        assert [a["value"] for a in r.json()["action"]["params"]["artifacts"]] == real
    _world(body)


def test_refang_is_shared_with_the_slack_extractor():
    from app.services import war_room_extract
    from app.utils.copilot_heuristics import detect_indicators, refang
    assert refang("hxxps://evil[.]com 1(.)2{.}3[dot]4 a[@]b[.]io [:]") == "https://evil.com 1.2.3.4 a@b.io :"
    assert war_room_extract.refang is refang
    assert [d["value"] for d in detect_indicators(refang("10[.]0[.]0[.]5"))] == ["10.0.0.5"]
    assert detect_indicators("10[.]0[.]0[.]5") == []  # detection itself is unchanged


def test_stuck_running_claim_expires():
    old = (datetime.now(timezone.utc) - CLAIM_TTL - timedelta(seconds=5)).isoformat()
    fresh = datetime.now(timezone.utc).isoformat()
    assert live_state({"state": "running", "claimed_at": fresh}) == "running"
    assert live_state({"state": "running", "claimed_at": old}) is None
    assert live_state({"state": "running"}) is None  # a claim from before claims were timestamped
    assert live_state({"state": "cancelled", "claimed_at": old}) == "cancelled"

    async def body(c, w):
        h = hdr(w, "analyst")
        sid, (stuck, busy, gone) = await _session(w, w.case, [
            {"action": {**NOTE, "state": "running", "claimed_at": old}},
            {"action": {**NOTE, "state": "running", "claimed_at": fresh}},
            {"action": {**NOTE, "state": "running", "claimed_at": old}}])
        read = (await c.get(f"/api/v1/copilot/sessions/{w.case}", headers=h)).json()["messages"]
        assert [m["action"]["state"] for m in read] == [None, "running", None]  # the card stops showing "Working…"

        confirm = {"type": "x", "params": {}}
        assert (await c.post(EXECUTE, headers=h, json={**confirm, "message_id": busy})).status_code == 409
        assert (await c.post(DISMISS, headers=h, json={"message_id": busy})).json()["state"] == "running"
        r = await c.post(EXECUTE, headers=h, json={**confirm, "message_id": stuck})
        assert r.status_code == 200 and await _notes(w.case) == ["copilot-edit-test proposed"]
        done = (await _messages(sid))[0].action
        assert done["result"]["ok"] and "state" not in done and "claimed_at" not in done
        assert (await c.post(EXECUTE, headers=h, json={**confirm, "message_id": stuck})).status_code == 409

        r = await c.post(DISMISS, headers=h, json={"message_id": gone, "state": "cancelled"})
        assert r.json()["state"] == "cancelled" and "claimed_at" not in (await _messages(sid))[2].action
    _world(body)
