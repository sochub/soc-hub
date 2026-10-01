"""Single entry point for LLM calls (LiteLLM), with provider-agnostic errors."""
import asyncio
import logging
import os
import time
from typing import List, Optional, Sequence, Tuple
from urllib.parse import urlparse, urlunparse

# Use the bundled model cost map — never fetch it from GitHub at import time.
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

import httpx  # noqa: E402
import litellm  # noqa: E402
from litellm.llms.custom_httpx.http_handler import AsyncHTTPHandler  # noqa: E402
from openai import AsyncOpenAI  # noqa: E402

from app.ai.aws import bedrock_credentials
from app.ai.errors import AIError, AIUnavailable, scrub
from app.ai.providers import PROVIDERS, SSRF_GUARDED, ConfigError, ProviderConfig, build_kwargs, missing_fields
from app.core.config import settings
from app.workflows.ssrf import SSRFError, assert_url_allowed

logger = logging.getLogger(__name__)

# Lockdown: prompts contain case data — nothing may be sent anywhere but the chosen provider.
litellm.telemetry = False
litellm.callbacks = []
litellm.success_callback = []
litellm.failure_callback = []
litellm.suppress_debug_info = True
litellm.turn_off_message_logging = True
litellm.drop_params = True  # providers without JSON mode / temperature just ignore them
logging.getLogger("LiteLLM").setLevel(logging.WARNING)

_UNAVAILABLE = tuple(
    getattr(litellm, name) for name in (
        "AuthenticationError", "PermissionDeniedError", "RateLimitError", "APIConnectionError",
        "Timeout", "ServiceUnavailableError", "NotFoundError", "BadRequestError",
        "ContextWindowExceededError", "InternalServerError",
    ) if hasattr(litellm, name)
)

_LOG_FMT = "ai_call provider=%s model=%s source=%s tenant=%s json=%s duration_ms=%d outcome=%s"


def _fail(cls, message: str, cause: BaseException | str) -> AIError:
    err = cls(message)
    # For the call log only: the cause's type name and HTTP status, never its text.
    err.cause = cause if isinstance(cause, str) else type(cause).__name__
    err.status_code = None if isinstance(cause, str) else getattr(cause, "status_code", None)
    return err


def _pin(url: str, ip: Optional[str]) -> str:
    """Point a plain-http URL at the SSRF-vetted IP so a second DNS answer can't rebind it."""
    p = urlparse(url)
    # ponytail: https keeps its hostname — TLS certificate verification already defeats DNS rebinding.
    if not ip or p.scheme != "http":
        return url
    userinfo, _, _ = p.netloc.rpartition("@")
    netloc = (f"[{ip}]" if ":" in ip else ip) + (f":{p.port}" if p.port else "")
    return urlunparse(p._replace(netloc=f"{userinfo}@{netloc}" if userinfo else netloc))


class _RedirectRefused(Exception):
    pass


def _no_redirect_client(cfg: ProviderConfig, kwargs: dict, refused: list) -> Tuple[object, List[httpx.AsyncClient]]:
    """Per-call client for a guarded tenant endpoint that never follows redirects: a 3xx could send the
    request on to a host the SSRF check never vetted. LiteLLM's default clients use follow_redirects=True."""
    async def flag(response: httpx.Response) -> None:
        if 300 <= response.status_code < 400:
            refused.append(response.status_code)

    async def flag_and_raise(response: httpx.Response) -> None:
        await flag(response)
        if refused:
            raise _RedirectRefused()

    if cfg.provider == "openai_compatible":
        # The openai SDK never follows redirects itself, and a 3xx is not retryable, so flagging suffices.
        http = httpx.AsyncClient(follow_redirects=False, event_hooks={"response": [flag]})
        return AsyncOpenAI(base_url=kwargs["api_base"], api_key=kwargs["api_key"], http_client=http), [http]
    # ollama_chat: LiteLLM's AsyncHTTPHandler retries connection errors on a fresh follow_redirects=True
    # client that inherits these event hooks, so the hook must raise to stop that client too.
    hooks = {"response": [flag_and_raise]}
    handler = AsyncHTTPHandler(event_hooks=hooks)
    owned = handler.client  # the follow_redirects=True default, replaced before any request
    handler.client = http = httpx.AsyncClient(follow_redirects=False, event_hooks=hooks)
    return handler, [owned, http]


async def _call(cfg: ProviderConfig, messages: List[dict], temperature: Optional[float], json_mode: bool,
                max_tokens: Optional[int], timeout: Optional[float], allowlist: Sequence[str]) -> str:
    secrets = list((cfg.secrets or {}).values())
    # Validate before anything touches the network (SSRF DNS lookup, STS).
    if cfg.provider not in PROVIDERS:
        raise _fail(AIUnavailable, f"AI provider misconfigured: unknown provider '{cfg.provider}'", "ConfigError")
    missing = missing_fields(cfg)
    if missing:
        raise _fail(AIUnavailable, "AI provider misconfigured: missing " + ", ".join(missing), "ConfigError")
    pinned_ip = None
    guarded = cfg.source == "tenant" and cfg.provider in SSRF_GUARDED and cfg.api_base
    if guarded:
        try:
            pinned_ip = await asyncio.to_thread(assert_url_allowed, cfg.api_base, list(allowlist))
        except SSRFError as e:
            raise _fail(AIUnavailable, f"AI endpoint not allowed: {e}", e) from None
    aws_creds = await bedrock_credentials(cfg) if cfg.provider == "bedrock" else None
    secrets += list((aws_creds or {}).values())
    try:
        kwargs = build_kwargs(cfg, aws_creds)
    except ConfigError as e:
        raise _fail(AIUnavailable, f"AI provider misconfigured: {e}", e) from None
    if guarded:
        kwargs["api_base"] = _pin(kwargs["api_base"], pinned_ip)
    if temperature is not None:
        kwargs["temperature"] = temperature
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    if max_tokens is not None:
        kwargs["max_tokens"] = max_tokens
    kwargs["timeout"] = timeout if timeout is not None else settings.AI_TIMEOUT_SECONDS
    refused: list = []
    to_close: List[httpx.AsyncClient] = []
    if guarded:
        kwargs["client"], to_close = _no_redirect_client(cfg, kwargs, refused)
    # `from None` throughout: the raw provider exception may carry keys; only the scrubbed text survives.
    try:
        resp = await litellm.acompletion(messages=messages, **kwargs)
    except Exception as e:
        if refused:
            raise _fail(AIUnavailable, "AI endpoint redirect refused", "RedirectRefused") from None
        if isinstance(e, _UNAVAILABLE):
            raise _fail(AIUnavailable, scrub(f"{type(e).__name__}: {e}", secrets), e) from None
        raise _fail(AIError, scrub(f"{type(e).__name__}: {e}", secrets), e) from None
    finally:
        for c in to_close:
            await c.aclose()
    if refused:
        raise _fail(AIUnavailable, "AI endpoint redirect refused", "RedirectRefused")
    try:
        return resp.choices[0].message.content or ""
    except (AttributeError, IndexError, TypeError):
        raise _fail(AIError, "AI provider returned an empty response", "EmptyResponse") from None


async def complete_with(cfg: ProviderConfig, messages: List[dict], *, temperature: Optional[float] = None,
                        json_mode: bool = False, max_tokens: Optional[int] = None,
                        timeout: Optional[float] = None, allowlist: Sequence[str] = ()) -> str:
    """Run one chat completion. Logs exactly one `ai_call` line: ids, names, timing, outcome and
    exception type/HTTP status only — never prompts, responses, error text or secrets."""
    start = time.monotonic()

    def log(level: int, outcome: str, exc: Optional[BaseException] = None) -> None:
        fmt, args = _LOG_FMT, [cfg.provider, cfg.model, cfg.source, cfg.tenant_id, json_mode,
                               int((time.monotonic() - start) * 1000), outcome]
        if exc is not None:
            fmt += " type=%s"
            args.append(getattr(exc, "cause", None) or type(exc).__name__)
            status = getattr(exc, "status_code", None)
            if isinstance(status, int):
                fmt += " status=%d"
                args.append(status)
        logger.log(level, fmt, *args)

    try:
        out = await _call(cfg, messages, temperature, json_mode, max_tokens, timeout, allowlist)
    except AIUnavailable as e:
        log(logging.WARNING, "unavailable", e)
        raise
    except asyncio.CancelledError as e:
        log(logging.WARNING, "cancelled", e)
        raise
    except Exception as e:  # AIError and anything unexpected escaping _call
        log(logging.ERROR, "error", e)
        raise
    log(logging.INFO, "ok")
    return out


async def test_connection(cfg: ProviderConfig, *, allowlist: Sequence[str] = ()) -> Tuple[bool, str]:
    try:
        await complete_with(cfg, [{"role": "user", "content": "Reply with OK"}],
                            max_tokens=5, timeout=30, allowlist=allowlist)
    except AIError as e:
        return False, str(e)
    return True, f"Connected — {cfg.provider} / {cfg.model} replied."


async def complete(messages: List[dict], *, tenant_id: Optional[int], temperature: Optional[float] = None,
                   json_mode: bool = False) -> str:
    """Resolve the tenant's (or deployment's) provider and run one completion. No fallback between them."""
    from app.ai.config import resolve_config, tenant_allowlist
    from app.db.session import AsyncSessionLocal
    try:
        async with AsyncSessionLocal() as db:
            cfg = await resolve_config(db, tenant_id)
            allow = await tenant_allowlist(db, tenant_id) if cfg.source == "tenant" else []
    except AIError:
        raise
    except Exception:
        raise AIUnavailable("AI configuration lookup failed") from None
    return await complete_with(cfg, messages, temperature=temperature, json_mode=json_mode, allowlist=allow)


async def describe(db, tenant_id: Optional[int]) -> dict:
    from app.ai.config import resolve_config
    try:
        cfg = await resolve_config(db, tenant_id)
    except AIUnavailable:
        return {"provider": None, "model": None, "source": "tenant"}
    return {"provider": cfg.provider, "model": cfg.model, "source": cfg.source}
