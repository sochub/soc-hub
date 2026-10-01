import asyncio
import json

import pytest

from app.ai.errors import AIError, AIUnavailable
from app.services import ai_service
from app.services.ai_service import AIService


@pytest.fixture
def fake(monkeypatch):
    class Calls(list):
        reply = "answer"  # tests set `fake.reply` to change what the model returns
    calls = Calls()

    async def complete(messages, *, tenant_id, temperature=None, json_mode=False):
        calls.append({"messages": messages, "tenant_id": tenant_id, "temperature": temperature, "json_mode": json_mode})
        return calls.reply
    monkeypatch.setattr(ai_service.llm, "complete", complete)
    return calls


def run(c):
    return asyncio.run(c)


def test_chat_uses_tenant_and_temperature(fake):
    out = run(AIService(tenant_id=5).chat([{"role": "user", "content": "hi"}]))
    assert out == "answer"
    assert fake[0]["tenant_id"] == 5 and fake[0]["temperature"] == 0.3
    assert fake[0]["messages"][0]["role"] == "system"


def test_triage_uses_json_mode(fake):
    fake.reply = json.dumps({"severity": "high", "tags": ["x"], "next_steps": "do"})
    res = run(AIService(tenant_id=5).generate_triage({"title": "t"}))
    assert fake[0]["json_mode"] is True and fake[0]["temperature"] == 0
    assert res is not None


def test_force_action_uses_json_mode(fake):
    fake.reply = json.dumps({"type": None})
    assert run(AIService(tenant_id=1).force_action_extraction("hello")) is None
    assert fake[0]["json_mode"] is True


@pytest.fixture
def down(monkeypatch):
    async def complete(messages, **kw):
        raise AIUnavailable("AuthenticationError: invalid key")
    monkeypatch.setattr(ai_service.llm, "complete", complete)


def test_fallbacks_when_unavailable(down):
    svc = AIService(tenant_id=1)
    ctx = {"title": "T", "severity": "high"}
    assert run(svc.generate_welcome_briefing(ctx)) == svc._static_welcome_briefing(ctx)
    assert run(svc.generate_welcome_briefing(ctx)).startswith("**Case**: T — **Severity**: HIGH")
    assert run(svc.generate_general_welcome({})) == svc._static_general_welcome({})
    assert run(svc.generate_general_welcome({})).startswith("**Queue overview** — 0 open case(s).")
    assert "unavailable" in run(svc.chat([{"role": "user", "content": "hi"}])).lower()
    assert "unavailable" in run(svc.analyze_case({"title": "T"})).lower()
    assert run(svc.generate_note_content([], "add note")) is None
    assert run(svc.force_action_extraction("x")) is None
    assert run(svc.generate_triage({"title": "t"})) is None


def test_no_ollama_specific_code_left():
    src = open(ai_service.__file__).read()
    assert "_call_ollama" not in src and "_check_ollama_available" not in src and "api/chat" not in src


def test_plain_ai_error_reaches_static_fallback(monkeypatch):
    async def complete(messages, **kw):
        raise AIError("BadRequest: weird")
    monkeypatch.setattr(ai_service.llm, "complete", complete)
    svc = AIService(tenant_id=1)
    ctx = {"title": "T", "severity": "low"}
    assert run(svc.generate_welcome_briefing(ctx)) == svc._static_welcome_briefing(ctx)
    assert run(svc.generate_general_welcome({})) == svc._static_general_welcome({})
    assert run(svc.generate_triage({"title": "t"})) is None


def test_unavailable_text_hides_provider_reason(monkeypatch):
    async def complete(messages, **kw):
        err = AIUnavailable("APIConnectionError: cannot reach http://10.1.2.3:11434 account 123456789012")
        err.cause = "APIConnectionError"
        raise err
    monkeypatch.setattr(ai_service.llm, "complete", complete)
    svc = AIService(tenant_id=1)
    for out in (run(svc.chat([{"role": "user", "content": "hi"}])), run(svc.analyze_case({"title": "T"}))):
        assert out == "AI Assistant unavailable (APIConnectionError). Ask an admin to check the AI provider settings."
        assert "10.1.2.3" not in out and "123456789012" not in out


TRIAGE_JSON = {"severity": "high", "tags": ["x"], "next_steps": "do"}
ACTION_JSON = {"type": "add_artifact", "summary": "s", "params": {"value": "1.2.3.4"}}


@pytest.mark.parametrize("wrap", [
    lambda d: "```json\n" + json.dumps(d) + "\n```",
    lambda d: "Sure! Here is the JSON you asked for: " + json.dumps(d) + " Let me know if you need more.",
], ids=["fenced", "chatty"])
def test_non_strict_json_replies_are_parsed(fake, wrap):
    svc = AIService(tenant_id=1)
    fake.reply = wrap(TRIAGE_JSON)
    assert run(svc.generate_triage({"title": "t"})) == {"severity": "high", "tags": ["x"], "next_steps_text": "do"}
    fake.reply = wrap(ACTION_JSON)
    assert run(svc.force_action_extraction("add 1.2.3.4")) == ACTION_JSON


def test_unparseable_json_reply_is_none(fake):
    fake.reply = "no json here {broken"
    svc = AIService(tenant_id=1)
    assert run(svc.generate_triage({"title": "t"})) is None
    assert run(svc.force_action_extraction("x")) is None


def test_triage_task_passes_triage_tenant(monkeypatch):
    """Wiring: the Celery triage path calls the LLM with the triage row's tenant_id."""
    from types import SimpleNamespace
    from app.tasks import triage as triage_task

    triage_row = SimpleNamespace(id=9, case_id=7, tenant_id=42, status="pending", error_message=None)
    case_row = SimpleNamespace(id=7, title="t", description="d", tags=[])
    firsts = [triage_row, case_row]

    class Result:
        def scalars(self):
            return self

        def first(self):
            return firsts.pop(0)

        def all(self):
            return []

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def execute(self, *a, **k):
            return Result()

        async def commit(self):
            pass

    seen = []

    async def complete(messages, *, tenant_id, **kw):
        seen.append(tenant_id)
        return "not json"
    monkeypatch.setattr(ai_service.llm, "complete", complete)
    monkeypatch.setattr(triage_task, "AsyncSessionLocal", Session)
    run(triage_task._run_with_session(9))
    assert seen == [42]
    assert triage_row.status == "failed"


def test_tenant_id_is_required_keyword():
    with pytest.raises(TypeError):
        AIService()
    with pytest.raises(TypeError):
        AIService(5)
    assert AIService(tenant_id=None).tenant_id is None  # deployment-only use is explicit
