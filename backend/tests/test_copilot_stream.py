"""Streaming copilot replies (POST /copilot/chat/stream): Server-Sent Events over the same post-processing as
POST /copilot/chat, with the LLM stubbed by an async generator."""
import asyncio
import json
from types import SimpleNamespace

import litellm
import pytest

from app.ai import llm as llm_layer
from app.ai.errors import AIUnavailable
from app.ai.providers import ProviderConfig
from app.api.v1 import copilot as cp
from app.models.copilot_session import CopilotMessage
from app.services import ai_service
from app.services.ai_service import ActionFenceFilter
from tests.audit_fixtures import hdr
from tests.test_copilot_chat import CHAT, _messages, _session, _world, block, llm  # noqa: F401 (llm: fixture)

STREAM = "/api/v1/copilot/chat/stream"
ACTION = {"type": "add_artifact", "summary": "Add the IP", "params": {"value": "203.0.113.40", "artifact_type": "ip"}}


@pytest.fixture
def stream(monkeypatch):
    """Scripted streaming model: yields `stream.chunks`, then raises `stream.error` if set. Records whether
    the stream was closed before it ran out (the consumer went away)."""
    state = SimpleNamespace(chunks=["Sure."], error=None, calls=0, closed_early=False, gate=None)

    async def fake(messages, *, tenant_id, temperature=None):
        state.calls += 1
        done = False
        try:
            for chunk in state.chunks:
                await asyncio.sleep(0)
                yield chunk
                if state.gate:
                    await state.gate.wait()  # parked mid-reply until the test lets go (or cancels)
            if state.error:
                raise state.error
            done = True
        finally:
            state.closed_early = not done and not state.error
    monkeypatch.setattr(ai_service.llm, "stream", fake)
    return state


def events(response):
    assert response.status_code == 200, response.text
    out = []
    for raw in response.text.strip().split("\n\n"):
        name, data = raw.split("\n", 1)
        assert name.startswith("event: ") and data.startswith("data: ")
        out.append((name[7:], json.loads(data[6:])))
    return out


def _same(message: dict) -> dict:
    return {k: v for k, v in message.items() if k not in ("id", "session_id", "created_at")}


def test_stream_deltas_then_the_same_message_as_plain_chat(llm, stream):  # noqa: F811
    """The action block arrives cut across chunks: no delta carries any of it, and the terminal `message` is
    what POST /chat returns (and stores) for the same reply."""
    reply = "On it. " + block(ACTION).split("\n", 1)[1] + "\nAnything else?"
    cuts = [0, 5, 8, 9, 13, 30, 61, len(reply) - 19, len(reply) - 17, len(reply) - 4, len(reply)]

    async def body(c, w):
        h = hdr(w, "analyst")
        plain_sid, _ = await _session(w, w.case)
        stream_sid, _ = await _session(w, w.case)
        llm.replies = [reply]
        stream.chunks = [reply[a:b] for a, b in zip(cuts, cuts[1:])]
        ask = {"message": "hello there, 203.0.113.40 looks bad"}
        plain = await c.post(CHAT, headers=h, json={"session_id": plain_sid, **ask})
        r = await c.post(STREAM, headers=h, json={"session_id": stream_sid, **ask})
        assert r.headers["content-type"].startswith("text/event-stream") and r.headers["x-accel-buffering"] == "no"
        got = events(r)

        assert [name for name, _ in got[:-1]] == ["delta"] * (len(got) - 1) and len(got) > 2
        shown = "".join(d["text"] for _, d in got[:-1])
        assert "```" not in shown and "add_artifact" not in shown and "203.0.113.40" not in shown
        assert shown.split() == ["On", "it.", "Anything", "else?"]

        name, message = got[-1]
        assert name == "message" and _same(message) == _same(plain.json())
        assert message["action"]["params"] == ACTION["params"] and message["action"]["case_id"] == w.case
        stored, stored_plain = (await _messages(stream_sid))[-1], (await _messages(plain_sid))[-1]
        assert stored.id == message["id"]
        assert (stored.content, stored.action, stored.suggestions) == \
            (stored_plain.content, stored_plain.action, stored_plain.suggestions)
        assert [m.role for m in await _messages(stream_sid)] == ["user", "assistant"]
    _world(body)


def test_fence_filter():
    def shown(*chunks):
        f = ActionFenceFilter()
        return "".join(f.feed(c) for c in chunks) + f.flush()

    action = "```action\n" + json.dumps(ACTION) + "\n```"
    sample = '```json\n{"title": "x", "severity": "high"}\n```'
    for size in (1, 2, 3, 7, 1000):  # every way of cutting the reply, down to one character at a time
        def cut(text):
            return [text[i:i + size] for i in range(0, len(text), size)]
        assert shown(*cut(f"Sure. {action} Done.")) == "Sure.  Done."
        assert shown(*cut(f"Here:\n{sample}\nUse it.")) == f"Here:\n{sample}\nUse it."  # not an action: shown
        assert shown(*cut("Adding it.\n" + action[:-3])) == "Adding it.\n"             # left unterminated
        assert shown(*cut("Use `code` and ``more`` here`")) == "Use `code` and ``more`` here`"
    f = ActionFenceFilter()
    assert f.feed("Sure. ``") == "Sure. " and f.feed("`action\n{") == ""  # nothing of an open fence leaks


def test_stream_provider_failure_is_an_error_event_and_stores_nothing(stream):
    async def body(c, w):
        h = hdr(w, "analyst")
        sid, _ = await _session(w, w.case)
        timeout = AIUnavailable("Timeout: http://10.1.2.3:11434 did not answer")
        timeout.cause = "Timeout"
        for error, code, chunks in ((AIUnavailable("APIConnectionError: cannot reach http://10.1.2.3:11434"),
                                     "unavailable", ["Let me"]), (timeout, "timeout", [])):
            stream.error, stream.chunks = error, chunks
            r = await c.post(STREAM, headers=h, json={"session_id": sid, "message": "add a tag please"})
            got = events(r)
            assert [name for name, _ in got] == ["delta"] * len(chunks) + ["error"]
            assert got[-1][1]["code"] == code and "unavailable" in got[-1][1]["detail"].lower()
            assert "10.1.2.3" not in r.text
            assert await _messages(sid) == []  # neither an assistant turn nor the user's
    _world(body)


def test_stream_scoping_matches_plain_chat(llm, stream):  # noqa: F811
    async def body(c, w):
        sid, _ = await _session(w, w.case)
        general_sid, _ = await _session(w, None)
        cases = [
            ("admin", {"session_id": sid, "message": "hi"}, 404),          # same tenant, someone else's session
            ("admin_b", {"session_id": sid, "message": "hi"}, 404),        # another tenant
            ("analyst", {"session_id": sid, "case_id": w.case + 1, "message": "hi"}, 400),
            ("analyst", {"session_id": general_sid, "case_id": w.case, "message": "hi"}, 400),
            ("analyst", {"message": "hi"}, 400),
            ("analyst", {"session_id": sid, "message": "x" * 16_001}, 422),
            (None, {"session_id": sid, "message": "hi"}, 401),
        ]
        for who, payload, status in cases:
            for path in (CHAT, STREAM):
                r = await c.post(path, headers=hdr(w, who) if who else {}, json=payload)
                assert r.status_code == status, (path, who, r.status_code, r.text)
        assert stream.calls == 0 and len(llm) == 0 and await _messages(sid) == []
    _world(body)


def test_abandoned_stream_stops_and_stores_nothing(stream):
    """The client going away (Stop, closed tab) reaches the generator as a close or a cancellation. Driven on
    the generator itself: the ASGI test client reads a response whole, so it cannot hang up mid-stream."""
    async def body(c, w):
        sid, _ = await _session(w, w.case)

        async def turn():
            from app.db.session import AsyncSessionLocal
            async with AsyncSessionLocal() as db:
                row = CopilotMessage(session_id=sid, role="user", content="what happened?")
                db.add(row)
                await db.commit()
                return cp._Turn(sid, w.case, row.content, row.id, {"title": "T"},
                                [{"role": "user", "content": row.content}])

        async def settled():
            await asyncio.gather(*cp._ABANDONED)
            return await _messages(sid)

        stream.chunks = ["one ", "two ", "three"]
        gen = cp._chat_events(await turn(), w.ta)
        assert json.loads((await anext(gen)).split("data: ")[1])["text"] == "one "
        await gen.aclose()  # closed between events
        assert await settled() == [] and stream.closed_early

        stream.closed_early, stream.gate = False, asyncio.Event()
        gen = cp._chat_events(await turn(), w.ta)

        async def consume():
            async for _ in gen:
                pass
        task = asyncio.create_task(consume())
        await asyncio.sleep(0.05)  # parked inside the model's stream
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert await settled() == [] and stream.closed_early
    _world(body)


# ---- the LLM layer's streaming call ------------------------------------------------------------------------

OPENAI = ProviderConfig("openai", "gpt-4o-mini", "tenant", tenant_id=1, secrets={"api_key": "sk-test-1234567890"})


def _part(text):
    return SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=text))])


async def _collect(agen):
    return [chunk async for chunk in agen]


def test_stream_with_yields_chunks_through_the_same_call_setup(monkeypatch, caplog):
    seen = []

    async def parts():
        for text in ("Hel", None, "lo", ""):
            yield _part(text)
        yield SimpleNamespace(choices=[])  # a trailing usage chunk

    async def fake(**kw):
        seen.append(kw)
        return parts()
    monkeypatch.setattr(llm_layer.litellm, "acompletion", fake)
    with caplog.at_level("INFO", logger="app.ai.llm"):
        out = asyncio.run(_collect(llm_layer.stream_with(OPENAI, [{"role": "user", "content": "x"}], temperature=0.3)))
    assert out == ["Hel", "lo"]
    kw = seen[0]
    assert kw["stream"] is True and kw["model"] == "openai/gpt-4o-mini" and kw["api_key"] == "sk-test-1234567890"
    assert kw["temperature"] == 0.3 and kw["timeout"] == 120
    lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith("ai_call")]
    assert len(lines) == 1 and "outcome=ok" in lines[0] and "Hel" not in lines[0]


def test_stream_with_falls_back_to_one_chunk(monkeypatch):
    async def fake(**kw):
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="whole reply"))])
    monkeypatch.setattr(llm_layer.litellm, "acompletion", fake)
    assert asyncio.run(_collect(llm_layer.stream_with(OPENAI, [{"role": "user", "content": "x"}]))) == ["whole reply"]


@pytest.mark.parametrize("midway", [False, True])
def test_stream_with_maps_errors_like_complete(monkeypatch, midway):
    def boom():
        return litellm.Timeout(message="slow sk-test-1234567890", llm_provider="openai", model="gpt-4o-mini")

    async def parts():
        yield _part("partial")
        raise boom()

    async def fake(**kw):
        if not midway:
            raise boom()
        return parts()
    monkeypatch.setattr(llm_layer.litellm, "acompletion", fake)
    got = []

    async def go():
        async for chunk in llm_layer.stream_with(OPENAI, [{"role": "user", "content": "x"}]):
            got.append(chunk)
    with pytest.raises(AIUnavailable) as e:
        asyncio.run(go())
    assert e.value.cause == "Timeout" and "sk-test-1234567890" not in str(e.value)
    assert got == (["partial"] if midway else [])


def test_stream_with_runs_the_ssrf_check(monkeypatch):
    def refuse(url, allow):
        raise llm_layer.SSRFError("private address")

    async def never(**kw):
        raise AssertionError("the provider must not be called")
    monkeypatch.setattr(llm_layer, "assert_url_allowed", refuse)
    monkeypatch.setattr(llm_layer.litellm, "acompletion", never)
    cfg = ProviderConfig("ollama", "llama3", "tenant", tenant_id=1, api_base="http://10.0.0.5:11434")
    with pytest.raises(AIUnavailable, match="not allowed"):
        asyncio.run(_collect(llm_layer.stream_with(cfg, [{"role": "user", "content": "x"}])))


def test_stream_skips_cache_and_adds_the_untrusted_rule(monkeypatch):
    from app.ai import untrusted
    seen = {}

    async def resolve(tenant_id):
        return OPENAI, []

    async def fake_stream_with(cfg, messages, **kw):
        seen["messages"] = messages
        yield "x"

    def no_redis():
        raise AssertionError("streaming must not touch the answer cache")
    monkeypatch.setattr(llm_layer, "_resolve", resolve)
    monkeypatch.setattr(llm_layer, "stream_with", fake_stream_with)
    monkeypatch.setattr(llm_layer, "_redis", no_redis)
    fenced = untrusted.fence("case data")
    assert asyncio.run(_collect(llm_layer.stream([{"role": "user", "content": fenced}], tenant_id=1))) == ["x"]
    assert seen["messages"][0] == {"role": "system", "content": untrusted.RULE}


def test_stream_with_times_out_a_stalled_stream(monkeypatch):
    async def parts():
        yield _part("partial")
        await asyncio.sleep(30)

    async def fake(**kw):
        return parts()
    monkeypatch.setattr(llm_layer.litellm, "acompletion", fake)
    got = []

    async def go():
        async for chunk in llm_layer.stream_with(OPENAI, [{"role": "user", "content": "x"}], timeout=0.05):
            got.append(chunk)
    with pytest.raises(AIUnavailable) as e:
        asyncio.run(go())
    assert e.value.cause == "Timeout" and got == ["partial"]
