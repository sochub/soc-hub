import asyncio
import json

import pytest

from app.ai.errors import AIUnavailable
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
    assert "Case" in run(svc.generate_welcome_briefing({"title": "T", "severity": "high"}))  # static briefing
    assert run(svc.generate_general_welcome({})) is not None
    assert "unavailable" in run(svc.chat([{"role": "user", "content": "hi"}])).lower()
    assert "unavailable" in run(svc.analyze_case({"title": "T"})).lower()
    assert run(svc.generate_note_content([], "add note")) is None
    assert run(svc.force_action_extraction("x")) is None
    assert run(svc.generate_triage({"title": "t"})) is None


def test_no_ollama_specific_code_left():
    src = open(ai_service.__file__).read()
    assert "_call_ollama" not in src and "_check_ollama_available" not in src and "api/chat" not in src
