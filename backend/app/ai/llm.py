"""Single entry point for LLM calls (LiteLLM), with provider-agnostic errors."""
import asyncio
import hashlib
import json
import logging
import os
import time
from typing import AsyncIterator, List, Optional, Sequence, Tuple
from urllib.parse import urlparse, urlunparse

# Use the bundled model cost map — never fetch it from GitHub at import time.
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

import httpx  # noqa: E402
import litellm  # noqa: E402
from litellm.llms.custom_httpx.http_handler import AsyncHTTPHandler  # noqa: E402
from openai import AsyncOpenAI  # noqa: E402

from app.ai import untrusted
from app.ai.aws import bedrock_credentials
from app.ai.errors import AIError, AISlotBusy, AIUnavailable, scrub
from app.ai.providers import PROVIDERS, SSRF_GUARDED, ConfigError, ProviderConfig, build_kwargs, missing_fields
from app.core.config import settings
from app.workflows.ssrf import HTTPS_ONLY, SSRFError, assert_url_allowed

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


def normalize_messages(messages: List[dict]) -> List[dict]:
    """Shape a chat for the strictest providers (Claude on Bedrock/Anthropic) without losing context.

    - one system message, first;
    - no assistant turn before the first user turn: the copilot's welcome briefing (or history cut
      off mid-conversation) is folded into the system prompt instead of being dropped;
    - consecutive same-role turns merged (a failed reply leaves user, user behind).
    """
    system = [m["content"] for m in messages if m.get("role") == "system" and m.get("content")]
    turns: List[dict] = []
    for m in messages:
        role, content = m.get("role"), m.get("content")
        if role == "system" or not content:
            continue
        if not turns and role == "assistant":
            system.append(f"Earlier in this conversation you said:\n{content}")
        elif turns and turns[-1]["role"] == role:
            turns[-1] = {"role": role, "content": f"{turns[-1]['content']}\n\n{content}"}
        else:
            turns.append({"role": role, "content": content})
    return ([{"role": "system", "content": "\n\n".join(system)}] if system else []) + turns


# Models that rejected `temperature` at runtime. litellm.drop_params only drops what its model map
# knows about; newer hosted models (e.g. recent Claude on Bedrock, inference profiles) refuse it anyway.
_NO_TEMPERATURE: set = set()


def _rejects_temperature(e: Exception) -> bool:
    return isinstance(e, litellm.BadRequestError) and "temperature" in str(e).lower()


async def _prepare(cfg: ProviderConfig, temperature: Optional[float], json_mode: bool, max_tokens: Optional[int],
                   timeout: Optional[float], allowlist: Sequence[str]) -> Tuple[dict, list, list, list]:
    """Everything before the provider call, shared by the plain and the streaming call: config validation, the
    SSRF check and IP pin, credentials, and the no-redirect client for a guarded tenant endpoint.
    Returns (LiteLLM kwargs, secrets to scrub, redirect flags, clients to close)."""
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
        if any(secrets) and urlparse(cfg.api_base).scheme.lower() != "https":
            raise _fail(AIUnavailable, f"AI endpoint not allowed: {HTTPS_ONLY}", "ConfigError")
    aws_creds = await bedrock_credentials(cfg) if cfg.provider == "bedrock" else None
    secrets += list((aws_creds or {}).values())
    try:
        kwargs = build_kwargs(cfg, aws_creds)
    except ConfigError as e:
        raise _fail(AIUnavailable, f"AI provider misconfigured: {e}", e) from None
    if guarded:
        kwargs["api_base"] = _pin(kwargs["api_base"], pinned_ip)
    if temperature is not None and (cfg.provider, cfg.model) not in _NO_TEMPERATURE:
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
    return kwargs, secrets, refused, to_close


async def _acompletion(cfg: ProviderConfig, messages: List[dict], kwargs: dict):
    model_key = (cfg.provider, cfg.model)
    try:
        return await litellm.acompletion(messages=normalize_messages(messages), **kwargs)
    except Exception as e:
        if "temperature" not in kwargs or not _rejects_temperature(e):
            raise
        logger.info("AI model %s/%s rejects temperature; retrying without it", *model_key)
        _NO_TEMPERATURE.add(model_key)
        kwargs.pop("temperature")
        return await litellm.acompletion(messages=normalize_messages(messages), **kwargs)


def _provider_error(e: Exception, refused: list, secrets: list) -> AIError:
    # Raised `from None` by the callers: the raw provider exception may carry keys; only the scrubbed text survives.
    if refused:
        return _fail(AIUnavailable, "AI endpoint redirect refused", "RedirectRefused")
    if isinstance(e, _UNAVAILABLE):
        return _fail(AIUnavailable, scrub(f"{type(e).__name__}: {e}", secrets), e)
    return _fail(AIError, scrub(f"{type(e).__name__}: {e}", secrets), e)


async def _call(cfg: ProviderConfig, messages: List[dict], temperature: Optional[float], json_mode: bool,
                max_tokens: Optional[int], timeout: Optional[float], allowlist: Sequence[str]) -> str:
    kwargs, secrets, refused, to_close = await _prepare(cfg, temperature, json_mode, max_tokens, timeout, allowlist)
    try:
        resp = await _acompletion(cfg, messages, kwargs)
        if _empty_without_thinking(resp, kwargs):
            resp = await _acompletion(cfg, messages, _with_thinking(kwargs))
    except Exception as e:
        raise _provider_error(e, refused, secrets) from None
    finally:
        for c in to_close:
            await c.aclose()
    if refused:
        raise _fail(AIUnavailable, "AI endpoint redirect refused", "RedirectRefused")
    try:
        return resp.choices[0].message.content or ""
    except (AttributeError, IndexError, TypeError):
        raise _fail(AIError, "AI provider returned an empty response", "EmptyResponse") from None


def _with_thinking(kwargs: dict) -> dict:
    return {k: v for k, v in kwargs.items() if k != "reasoning_effort"}


def _empty_without_thinking(resp, kwargs: dict) -> bool:
    """OLLAMA_THINKING=false and the model said nothing. Some reasoning models (lfm3) answer an "act on this"
    prompt with an empty message when thinking is off; the caller then asks once more with thinking on, so the
    setting costs time on those turns instead of the answer."""
    if kwargs.get("reasoning_effort") != "none":
        return False
    try:
        return not (resp.choices[0].message.content or "").strip()
    except (AttributeError, IndexError, TypeError):
        return False


async def _stream_call(cfg: ProviderConfig, messages: List[dict], temperature: Optional[float],
                       timeout: Optional[float], allowlist: Sequence[str]) -> AsyncIterator[str]:
    """_call, yielding the reply's text as it is generated. Same preparation and error mapping. The timeout is
    wait for the response to start and then for each chunk, not a cap on the whole reply."""
    kwargs, secrets, refused, to_close = await _prepare(cfg, temperature, False, None, timeout, allowlist)
    resp = None
    try:
        try:
            attempts = [kwargs, _with_thinking(kwargs)] if kwargs.get("reasoning_effort") == "none" else [kwargs]
            for attempt in attempts:  # the second only when the first said nothing (see _empty_without_thinking)
                said = False
                resp = await _acompletion(cfg, messages, {**attempt, "stream": True})
                if not hasattr(resp, "__aiter__"):
                    # A provider that can't stream answers whole: one chunk.
                    text = resp.choices[0].message.content or ""
                    if text.strip():
                        said = True
                        yield text
                else:
                    parts = resp.__aiter__()
                    while True:
                        try:
                            # Enforced here too: not every provider transport times out a stream that stalls.
                            part = await asyncio.wait_for(parts.__anext__(), kwargs["timeout"])
                        except StopAsyncIteration:
                            break
                        except asyncio.TimeoutError:
                            raise _fail(AIUnavailable, "AI provider stopped answering", "Timeout") from None
                        text = part.choices[0].delta.content if part.choices else None
                        if text:
                            said = said or bool(text.strip())
                            yield text
                    if not said and hasattr(resp, "aclose"):
                        await resp.aclose()
                if said:
                    break
        except AIError:
            raise
        except Exception as e:
            raise _provider_error(e, refused, secrets) from None
        if refused:
            raise _fail(AIUnavailable, "AI endpoint redirect refused", "RedirectRefused")
    finally:
        # Also when the consumer stops early (client disconnect): closing the response ends the generation.
        try:
            if hasattr(resp, "aclose"):
                await resp.aclose()
        finally:
            for c in to_close:
                await c.aclose()


def _call_log(cfg: ProviderConfig, json_mode: bool):
    """The one `ai_call` line per call: ids, names, timing, outcome and exception type/HTTP status only —
    never prompts, responses, error text or secrets."""
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
    return log


async def complete_with(cfg: ProviderConfig, messages: List[dict], *, temperature: Optional[float] = None,
                        json_mode: bool = False, max_tokens: Optional[int] = None,
                        timeout: Optional[float] = None, allowlist: Sequence[str] = ()) -> str:
    """Run one chat completion. Logs exactly one `ai_call` line: ids, names, timing, outcome and
    exception type/HTTP status only — never prompts, responses, error text or secrets."""
    log = _call_log(cfg, json_mode)
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


async def stream_with(cfg: ProviderConfig, messages: List[dict], *, temperature: Optional[float] = None,
                      timeout: Optional[float] = None, allowlist: Sequence[str] = ()) -> AsyncIterator[str]:
    """complete_with as a stream of text chunks, with the same single `ai_call` log line (a consumer that
    stops early is logged as cancelled)."""
    log = _call_log(cfg, False)
    try:
        async for chunk in _stream_call(cfg, messages, temperature, timeout, allowlist):
            yield chunk
    except AIUnavailable as e:
        log(logging.WARNING, "unavailable", e)
        raise
    except (asyncio.CancelledError, GeneratorExit) as e:
        log(logging.WARNING, "cancelled", e)
        raise
    except Exception as e:
        log(logging.ERROR, "error", e)
        raise
    log(logging.INFO, "ok")


async def test_connection(cfg: ProviderConfig, *, allowlist: Sequence[str] = ()) -> Tuple[bool, str]:
    try:
        await complete_with(cfg, [{"role": "user", "content": "Reply with OK"}],
                            max_tokens=5, timeout=30, allowlist=allowlist)
    except AIError as e:
        return False, str(e)
    return True, f"Connected — {cfg.provider} / {cfg.model} replied."


async def _resolve(tenant_id: Optional[int]) -> Tuple[ProviderConfig, list]:
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
    return cfg, allow


async def stream(messages: List[dict], *, tenant_id: Optional[int],
                 temperature: Optional[float] = None) -> AsyncIterator[str]:
    """complete(), as text chunks while the model generates. Deliberately outside the answer cache and the
    in-flight dedup of _shared: a cached answer has nothing to stream, and each reader needs its own stream.
    No background slot either — only interactive (API) callers stream."""
    cfg, allow = await _resolve(tenant_id)
    async for chunk in stream_with(cfg, untrusted.with_rule(messages), temperature=temperature, allowlist=allow):
        yield chunk


async def complete(messages: List[dict], *, tenant_id: Optional[int], temperature: Optional[float] = None,
                   json_mode: bool = False) -> str:
    """Resolve the tenant's (or deployment's) provider and run one completion. No fallback between them."""
    cfg, allow = await _resolve(tenant_id)
    messages = untrusted.with_rule(messages)
    key = _cache_key(cfg, messages, temperature, json_mode)
    return await _shared(cfg, key, lambda: complete_with(cfg, messages, temperature=temperature,
                                                         json_mode=json_mode, allowlist=allow))


# Set by app.worker: every LLM call made in the Celery worker is background work (triage, workflows).
BACKGROUND = False
# How long a background call waits for the shared Ollama slot before giving up with AISlotBusy.
SLOT_WAIT_SECONDS = 5
CACHE_TTL = 600  # identical prompt within 10 min -> reuse the answer (the key covers the whole prompt)


def _redis():
    # Per call, not a module global: Celery tasks run each job in a fresh asyncio.run() loop.
    import redis.asyncio as aioredis
    return aioredis.from_url(settings.REDIS_URL, socket_timeout=2, socket_connect_timeout=2,
                              decode_responses=True)


def _cache_key(cfg: ProviderConfig, messages: List[dict], temperature: Optional[float], json_mode: bool) -> str:
    blob = json.dumps([cfg.provider, cfg.model, cfg.api_base, cfg.source, cfg.tenant_id,
                       messages, temperature, json_mode], sort_keys=True, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()


async def _shared(cfg: ProviderConfig, key: str, run) -> str:
    """Answer cache + in-flight dedup + one background slot per local Ollama.

    - a cached answer for the exact same prompt (same tenant/provider/model) is returned without a call;
    - if the same prompt is already in flight (two analysts opening the same case), wait for its answer;
    - worker calls to Ollama take a Redis lock first, so interactive copilot requests (API process, no
      lock) queue behind at most one background call instead of the whole triage backlog.
    Redis trouble never blocks AI: every Redis step degrades to "just make the call". Errors aren't cached.
    """
    from redis.exceptions import LockError, RedisError
    hold = int(settings.AI_TIMEOUT_SECONDS) + 30
    ans_key, run_key = f"llm:ans:{key}", f"llm:run:{key}"
    r = _redis()
    owner = False
    try:
        try:
            hit = await r.get(ans_key)
            if hit is not None:
                return hit
            owner = bool(await r.set(run_key, 1, nx=True, ex=hold))
            if not owner:
                deadline = time.monotonic() + hold
                while time.monotonic() < deadline and await r.exists(run_key):
                    await asyncio.sleep(1)
                    hit = await r.get(ans_key)
                    if hit is not None:
                        return hit
                # The first caller failed or timed out (nothing cached): make the call ourselves.
        except (RedisError, OSError):
            logger.warning("ai cache unavailable; calling the provider directly")
        lock = None
        if BACKGROUND and cfg.provider == "ollama":
            # ponytail: one background call per Ollama endpoint; make it N (a counter) if a GPU box wants more.
            lock = r.lock(f"llm:bg:{cfg.api_base or 'default'}", timeout=hold)
            try:
                acquired = await lock.acquire(blocking_timeout=SLOT_WAIT_SECONDS)
            except (RedisError, OSError):
                acquired, lock = True, None
            if not acquired:
                # Waiting here would park a worker process for up to `hold` seconds per queued call; the
                # caller (a Celery task) retries later instead.
                raise AISlotBusy("background AI slot busy")
        try:
            out = await run()
        finally:
            if lock is not None:
                try:
                    await lock.release()
                except (LockError, RedisError, OSError):
                    pass  # expired or Redis gone: the TTL already freed it
        try:
            await r.set(ans_key, out, ex=CACHE_TTL)
        except (RedisError, OSError):
            pass
        return out
    finally:
        if owner:
            try:
                await r.delete(run_key)  # also on failure, so waiters stop waiting
            except (RedisError, OSError):
                pass
        await r.aclose()


async def describe(db, tenant_id: Optional[int]) -> dict:
    from app.ai.config import resolve_config
    try:
        cfg = await resolve_config(db, tenant_id)
    except AIUnavailable:
        return {"provider": None, "model": None, "source": "tenant"}
    return {"provider": cfg.provider, "model": cfg.model, "source": cfg.source}
