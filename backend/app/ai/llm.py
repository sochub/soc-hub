"""Single entry point for LLM calls (LiteLLM), with provider-agnostic errors."""
import asyncio
import logging
import time
from typing import List, Optional, Sequence, Tuple

import litellm

from app.ai.aws import bedrock_credentials
from app.ai.errors import AIError, AIUnavailable, scrub
from app.ai.providers import SSRF_GUARDED, ConfigError, ProviderConfig, build_kwargs
from app.core.config import settings
from app.workflows.ssrf import SSRFError, assert_url_allowed

logger = logging.getLogger(__name__)

# Lockdown: prompts contain case data — nothing may be sent anywhere but the chosen provider.
litellm.telemetry = False
litellm.callbacks = []
litellm.success_callback = []
litellm.failure_callback = []
litellm.suppress_debug_info = True
litellm.drop_params = True  # providers without JSON mode / temperature just ignore them

_UNAVAILABLE = tuple(
    getattr(litellm, name) for name in (
        "AuthenticationError", "PermissionDeniedError", "RateLimitError", "APIConnectionError",
        "Timeout", "ServiceUnavailableError", "NotFoundError", "BadRequestError",
        "ContextWindowExceededError", "InternalServerError",
    ) if hasattr(litellm, name)
)

_LOG_FMT = "ai_call provider=%s model=%s source=%s tenant=%s json=%s duration_ms=%d outcome=%s"


def _fail(cls, message: str, cause: str) -> AIError:
    err = cls(message)
    err.cause = cause  # exception type name only, for the call log
    return err


async def _call(cfg: ProviderConfig, messages: List[dict], temperature: Optional[float], json_mode: bool,
                max_tokens: Optional[int], timeout: Optional[float], allowlist: Sequence[str]) -> str:
    secrets = list((cfg.secrets or {}).values())
    if cfg.source == "tenant" and cfg.provider in SSRF_GUARDED and cfg.api_base:
        try:
            await asyncio.to_thread(assert_url_allowed, cfg.api_base, list(allowlist))
        except SSRFError as e:
            raise _fail(AIUnavailable, f"AI endpoint not allowed: {e}", "SSRFError") from None
    aws_creds = await bedrock_credentials(cfg) if cfg.provider == "bedrock" else None
    try:
        kwargs = build_kwargs(cfg, aws_creds)
    except ConfigError as e:
        raise _fail(AIUnavailable, f"AI provider misconfigured: {e}", "ConfigError") from None
    if temperature is not None:
        kwargs["temperature"] = temperature
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    if max_tokens is not None:
        kwargs["max_tokens"] = max_tokens
    kwargs["timeout"] = timeout if timeout is not None else settings.AI_TIMEOUT_SECONDS
    # `from None` throughout: the raw provider exception may carry keys; only the scrubbed text survives.
    try:
        resp = await litellm.acompletion(messages=messages, **kwargs)
    except _UNAVAILABLE as e:
        raise _fail(AIUnavailable, scrub(f"{type(e).__name__}: {e}", secrets), type(e).__name__) from None
    except Exception as e:
        raise _fail(AIError, scrub(f"{type(e).__name__}: {e}", secrets), type(e).__name__) from None
    try:
        return resp.choices[0].message.content or ""
    except (AttributeError, IndexError, TypeError):
        raise _fail(AIError, "AI provider returned an empty response", "EmptyResponse") from None


async def complete_with(cfg: ProviderConfig, messages: List[dict], *, temperature: Optional[float] = None,
                        json_mode: bool = False, max_tokens: Optional[int] = None,
                        timeout: Optional[float] = None, allowlist: Sequence[str] = ()) -> str:
    """Run one chat completion. Logs exactly one `ai_call` line — never prompts, responses or secrets."""
    start = time.monotonic()
    head = (cfg.provider, cfg.model, cfg.source, cfg.tenant_id, json_mode)
    try:
        out = await _call(cfg, messages, temperature, json_mode, max_tokens, timeout, allowlist)
    except AIUnavailable as e:
        ms = int((time.monotonic() - start) * 1000)
        logger.warning(_LOG_FMT + " type=%s", *head, ms, "unavailable", getattr(e, "cause", type(e).__name__))
        raise
    except AIError as e:
        ms = int((time.monotonic() - start) * 1000)
        logger.error(_LOG_FMT + " type=%s reason=%s", *head, ms, "error",
                     getattr(e, "cause", type(e).__name__), scrub(str(e), (cfg.secrets or {}).values()))
        raise
    logger.info(_LOG_FMT, *head, int((time.monotonic() - start) * 1000), "ok")
    return out


async def test_connection(cfg: ProviderConfig, *, allowlist: Sequence[str] = ()) -> Tuple[bool, str]:
    try:
        await complete_with(cfg, [{"role": "user", "content": "Reply with OK"}],
                            max_tokens=5, timeout=30, allowlist=allowlist)
    except AIError as e:
        return False, str(e)
    return True, f"Connected — {cfg.provider} / {cfg.model} replied."
