import asyncio
import logging
from types import SimpleNamespace

import litellm
import pytest

from app.ai import llm
from app.ai.errors import AIError, AIUnavailable
from app.ai.providers import ProviderConfig


def resp(text):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text))])


@pytest.fixture
def calls(monkeypatch):
    seen = []

    async def fake(**kw):
        seen.append(kw)
        return resp("hi")
    monkeypatch.setattr(llm.litellm, "acompletion", fake)
    monkeypatch.setattr(llm, "assert_url_allowed", lambda url, allow: None)
    return seen


def run(c):
    return asyncio.run(c)


OPENAI = ProviderConfig("openai", "gpt-4o-mini", "tenant", tenant_id=1, secrets={"api_key": "sk-test-1234567890"})


def test_lockdown_flags():
    assert litellm.telemetry is False
    assert litellm.drop_params is True
    assert litellm.callbacks == [] and litellm.success_callback == [] and litellm.failure_callback == []
    assert litellm.suppress_debug_info is True and litellm.turn_off_message_logging is True
    assert logging.getLogger("LiteLLM").level == logging.WARNING


def test_complete_returns_text_and_passes_options(calls):
    out = run(llm.complete_with(OPENAI, [{"role": "user", "content": "x"}], temperature=0, json_mode=True))
    assert out == "hi"
    kw = calls[0]
    assert kw["model"] == "openai/gpt-4o-mini" and kw["api_key"] == "sk-test-1234567890"
    assert kw["temperature"] == 0 and kw["response_format"] == {"type": "json_object"}
    assert kw["timeout"] == 120


def test_no_temperature_or_json_when_not_requested(calls):
    run(llm.complete_with(OPENAI, [{"role": "user", "content": "x"}]))
    assert "temperature" not in calls[0] and "response_format" not in calls[0]


@pytest.mark.parametrize("exc", ["AuthenticationError", "RateLimitError", "APIConnectionError", "Timeout",
                                 "ServiceUnavailableError", "NotFoundError"])
def test_provider_errors_become_unavailable_and_scrubbed(monkeypatch, exc):
    cls = getattr(litellm, exc)

    async def boom(**kw):
        raise cls(message="bad key sk-test-1234567890", llm_provider="openai", model="gpt-4o-mini")
    monkeypatch.setattr(llm.litellm, "acompletion", boom)
    with pytest.raises(AIUnavailable) as e:
        run(llm.complete_with(OPENAI, [{"role": "user", "content": "x"}]))
    assert "sk-test-1234567890" not in str(e.value)


def test_other_errors_become_ai_error(monkeypatch):
    async def boom(**kw):
        raise RuntimeError("weird")
    monkeypatch.setattr(llm.litellm, "acompletion", boom)
    with pytest.raises(AIError) as e:
        run(llm.complete_with(OPENAI, [{"role": "user", "content": "x"}]))
    assert type(e.value) is AIError


def test_misconfig_is_unavailable(calls):
    with pytest.raises(AIUnavailable, match="misconfigured"):
        run(llm.complete_with(ProviderConfig("openai", "m", "tenant"), [{"role": "user", "content": "x"}]))
    assert calls == []


def test_tenant_api_base_ssrf_checked_at_call(monkeypatch, calls):
    from app.workflows.ssrf import SSRFError

    def deny(url, allow):
        raise SSRFError("10.0.0.5 is private; add it to the HTTP allowlist")
    monkeypatch.setattr(llm, "assert_url_allowed", deny)
    cfg = ProviderConfig("ollama", "llama3", "tenant", tenant_id=1, api_base="http://10.0.0.5:11434")
    with pytest.raises(AIUnavailable, match="allowlist"):
        run(llm.complete_with(cfg, [{"role": "user", "content": "x"}]))
    assert calls == []


def test_deployment_api_base_not_ssrf_checked(monkeypatch, calls):
    def deny(url, allow):
        raise AssertionError("must not be called for deployment config")
    monkeypatch.setattr(llm, "assert_url_allowed", deny)
    cfg = ProviderConfig("ollama", "llama3", "deployment", api_base="http://ollama:11434")
    assert run(llm.complete_with(cfg, [{"role": "user", "content": "x"}])) == "hi"


def test_tenant_http_api_base_pinned_to_vetted_ip(monkeypatch, calls):
    monkeypatch.setattr(llm, "assert_url_allowed", lambda url, allow: "203.0.113.7")
    cfg = ProviderConfig("ollama", "llama3", "tenant", tenant_id=1, api_base="http://llm.example.com:11434/v1")
    run(llm.complete_with(cfg, [{"role": "user", "content": "x"}]))
    assert calls[0]["api_base"] == "http://203.0.113.7:11434/v1"


def test_tenant_http_api_base_pinned_ipv6(monkeypatch, calls):
    monkeypatch.setattr(llm, "assert_url_allowed", lambda url, allow: "2001:db8::1")
    cfg = ProviderConfig("openai_compatible", "m", "tenant", tenant_id=1, api_base="http://llm.example.com/v1")
    run(llm.complete_with(cfg, [{"role": "user", "content": "x"}]))
    assert calls[0]["api_base"] == "http://[2001:db8::1]/v1"


def test_tenant_https_api_base_keeps_hostname(monkeypatch, calls):
    monkeypatch.setattr(llm, "assert_url_allowed", lambda url, allow: "203.0.113.7")
    cfg = ProviderConfig("openai_compatible", "m", "tenant", tenant_id=1, api_base="https://llm.example.com:8443/v1")
    run(llm.complete_with(cfg, [{"role": "user", "content": "x"}]))
    assert calls[0]["api_base"] == "https://llm.example.com:8443/v1"


def test_allowlisted_api_base_unchanged(monkeypatch, calls):
    monkeypatch.setattr(llm, "assert_url_allowed", lambda url, allow: None)
    cfg = ProviderConfig("ollama", "llama3", "tenant", tenant_id=1, api_base="http://ollama.lan:11434")
    run(llm.complete_with(cfg, [{"role": "user", "content": "x"}]))
    assert calls[0]["api_base"] == "http://ollama.lan:11434"


def test_bedrock_misconfig_never_hits_sts(monkeypatch, calls):
    async def creds(cfg, **kw):
        raise AssertionError("STS must not be called for an invalid config")
    monkeypatch.setattr(llm, "bedrock_credentials", creds)
    cfg = ProviderConfig("bedrock", "anthropic.claude-v2", "tenant", tenant_id=1,
                         auth_mode="role", role_arn="arn:aws:iam::1:role/r")  # no region
    with pytest.raises(AIUnavailable, match="region"):
        run(llm.complete_with(cfg, [{"role": "user", "content": "x"}]))
    assert calls == []


def test_aws_creds_scrubbed_from_errors(monkeypatch):
    async def creds(cfg, **kw):
        return {"aws_access_key_id": "TEMPKEYID", "aws_secret_access_key": "TEMPSECRETVALUE",
                "aws_session_token": "TEMPSESSIONTOKEN"}

    async def boom(**kw):
        raise RuntimeError("signature TEMPSECRETVALUE token TEMPSESSIONTOKEN id TEMPKEYID")
    monkeypatch.setattr(llm, "bedrock_credentials", creds)
    monkeypatch.setattr(llm.litellm, "acompletion", boom)
    cfg = ProviderConfig("bedrock", "m", "tenant", tenant_id=1, region="us-east-1",
                         auth_mode="role", role_arn="arn:aws:iam::1:role/r")
    with pytest.raises(AIError) as e:
        run(llm.complete_with(cfg, [{"role": "user", "content": "x"}]))
    for v in ("TEMPSECRETVALUE", "TEMPSESSIONTOKEN", "TEMPKEYID"):
        assert v not in str(e.value)


def test_bedrock_role_creds_passed(monkeypatch, calls):
    async def creds(cfg, **kw):
        return {"aws_access_key_id": "ASIA1", "aws_secret_access_key": "S", "aws_session_token": "T"}
    monkeypatch.setattr(llm, "bedrock_credentials", creds)
    cfg = ProviderConfig("bedrock", "anthropic.claude-v2", "tenant", tenant_id=1, region="us-east-1",
                         auth_mode="role", role_arn="arn:aws:iam::1:role/r")
    run(llm.complete_with(cfg, [{"role": "user", "content": "x"}]))
    assert calls[0]["aws_session_token"] == "T" and calls[0]["aws_region_name"] == "us-east-1"


def test_test_connection(calls):
    ok, msg = run(llm.test_connection(OPENAI))
    assert ok and "openai" in msg
    assert calls[0]["max_tokens"] == 5 and calls[0]["timeout"] == 30
    assert calls[0]["messages"] == [{"role": "user", "content": "Reply with OK"}]


def test_test_connection_failure(monkeypatch):
    async def boom(**kw):
        raise litellm.AuthenticationError(message="invalid", llm_provider="openai", model="m")
    monkeypatch.setattr(llm.litellm, "acompletion", boom)
    ok, msg = run(llm.test_connection(OPENAI))
    assert not ok and msg


def _log_text(records):
    parts = []
    for r in records:
        parts.append(r.getMessage())
        parts.append(repr(r.args))
    return "\n".join(parts)


def test_logging_one_line_per_call_without_content(monkeypatch, caplog):
    prompt = "PROMPT-CANARY-7f3a hostname=dc01.corp"
    system = "SYSTEM-CANARY-91bd"
    response = "RESPONSE-CANARY-c0de"
    secret = "sk-secretcanary-abcdef1234567890"
    cfg = ProviderConfig("openai", "gpt-4o-mini", "tenant", tenant_id=7, secrets={"api_key": secret})
    msgs = [{"role": "system", "content": system}, {"role": "user", "content": prompt}]

    async def ok(**kw):
        return resp(response)

    async def unavailable(**kw):
        raise litellm.AuthenticationError(message=f"bad key {secret} for {prompt}",
                                          llm_provider="openai", model="gpt-4o-mini")

    async def broken(**kw):
        raise RuntimeError(f"exploded with key {secret}")

    async def echoing(**kw):
        raise litellm.BadRequestError(message=f"cannot process: {prompt}", llm_provider="openai",
                                      model="gpt-4o-mini")

    async def echoing_error(**kw):
        err = RuntimeError(f"provider body echoed: {prompt}")
        err.status_code = 500
        raise err

    caplog.set_level(logging.DEBUG)
    monkeypatch.setattr(llm.litellm, "acompletion", ok)
    assert run(llm.complete_with(cfg, msgs, json_mode=True)) == response
    monkeypatch.setattr(llm.litellm, "acompletion", unavailable)
    with pytest.raises(AIUnavailable):
        run(llm.complete_with(cfg, msgs))
    monkeypatch.setattr(llm.litellm, "acompletion", broken)
    with pytest.raises(AIError):
        run(llm.complete_with(cfg, msgs))
    monkeypatch.setattr(llm.litellm, "acompletion", echoing)
    with pytest.raises(AIUnavailable):
        run(llm.complete_with(cfg, msgs))
    monkeypatch.setattr(llm.litellm, "acompletion", echoing_error)
    with pytest.raises(AIError):
        run(llm.complete_with(cfg, msgs))

    ours = [r for r in caplog.records if r.name == "app.ai.llm"]
    assert [r.levelno for r in ours] == [logging.INFO, logging.WARNING, logging.ERROR, logging.WARNING,
                                         logging.ERROR]
    assert ours[4].getMessage().endswith("outcome=error type=RuntimeError status=500")
    assert ours[2].getMessage().endswith("outcome=error type=RuntimeError")
    assert all(r.getMessage().startswith("ai_call provider=openai model=gpt-4o-mini source=tenant tenant=7 ")
               for r in ours)
    assert "json=True" in ours[0].getMessage() and "outcome=ok" in ours[0].getMessage()
    assert "outcome=unavailable" in ours[1].getMessage() and "AuthenticationError" in ours[1].getMessage()
    assert "outcome=error" in ours[2].getMessage() and "RuntimeError" in ours[2].getMessage()

    text = _log_text(caplog.records)
    for canary in (prompt, "PROMPT-CANARY", system, response, secret):
        assert canary not in text


def test_unexpected_and_cancelled_still_log_once(monkeypatch, caplog):
    caplog.set_level(logging.DEBUG)

    def weird(url, allow):
        raise ValueError("unexpected")
    monkeypatch.setattr(llm, "assert_url_allowed", weird)
    cfg = ProviderConfig("ollama", "llama3", "tenant", tenant_id=3, api_base="http://h:11434")
    with pytest.raises(ValueError):
        run(llm.complete_with(cfg, [{"role": "user", "content": "x"}]))

    async def cancelled(**kw):
        raise asyncio.CancelledError()
    monkeypatch.setattr(llm.litellm, "acompletion", cancelled)
    with pytest.raises(asyncio.CancelledError):
        run(llm.complete_with(OPENAI, [{"role": "user", "content": "x"}]))

    ours = [r for r in caplog.records if r.name == "app.ai.llm"]
    assert [r.levelno for r in ours] == [logging.ERROR, logging.WARNING]
    assert ours[0].getMessage().endswith("outcome=error type=ValueError")
    assert ours[1].getMessage().endswith("outcome=cancelled type=CancelledError")
