"""Jinja2 sandboxed templating for workflow node configs and expressions."""
import functools
import re
from typing import Any, List

from jinja2 import StrictUndefined, Undefined
from jinja2.sandbox import ImmutableSandboxedEnvironment

# Immutable: templates can't mutate context lists/dicts (e.g. case.tags.append).
_env = ImmutableSandboxedEnvironment(undefined=StrictUndefined, autoescape=False)
_SINGLE_EXPR = re.compile(r"^\s*\{\{(?P<expr>(?:(?!\{\{|\}\}).)+)\}\}\s*$", re.S)


class TemplateError(Exception):
    pass


def _secret_classes():
    from app.secrets.refs import SecretRef, SecretsNamespace  # lazy: refs imports TemplateError from here
    return SecretRef, SecretsNamespace


def _guard_secrets(fn):
    """Wrap a filter/test so it refuses SecretRef arguments (secrets can't be transformed)."""
    @functools.wraps(fn)  # also copies jinja_pass_arg (pass_context/pass_environment markers)
    def wrapper(*args, **kwargs):
        ref = _secret_classes()[0]
        if any(isinstance(a, ref) for a in args) or any(isinstance(v, ref) for v in kwargs.values()):
            raise TemplateError("secrets can only be inserted, not transformed")
        return fn(*args, **kwargs)
    return wrapper


def _harden(env: ImmutableSandboxedEnvironment) -> ImmutableSandboxedEnvironment:
    # Wraps the filters/tests present now; anything registered later must be wrapped too
    # (test_secret_refs asserts every filter/test is guarded).
    env.filters = {k: _guard_secrets(f) for k, f in env.filters.items()}
    env.tests = {k: _guard_secrets(f) for k, f in env.tests.items()}
    return env


_harden(_env)


def _force(value: Any) -> Any:
    # A sandbox-blocked or missing attribute comes back as an Undefined; make it raise.
    if isinstance(value, Undefined):
        value._fail_with_undefined_error()
    return value


def _holds_secret(value: Any) -> bool:
    if isinstance(value, _secret_classes()):
        return True
    if isinstance(value, dict):
        return any(_holds_secret(v) for v in value.values())
    if isinstance(value, (list, tuple, set, frozenset)):
        return any(_holds_secret(v) for v in value)
    return False


def eval_expr(expr: str, ctx: dict) -> Any:
    """Evaluate a bare expression (condition, for_each items). Secret objects may not escape it."""
    value = _eval(expr, ctx)
    if _holds_secret(value):
        raise TemplateError("secrets can only be inserted, not transformed")
    return value


def _eval(expr: str, ctx: dict) -> Any:
    try:
        return _force(_env.compile_expression(expr, undefined_to_none=False)(**ctx))
    except TemplateError:
        raise
    except Exception as e:  # jinja errors, SecurityError, TypeError on None comparisons, ...
        raise TemplateError(f"{type(e).__name__}: {e}") from e


def _placeholders(value: Any) -> Any:
    """A single-expression render can return a SecretRef (or containers of them): emit placeholders."""
    ref, ns = _secret_classes()
    if isinstance(value, ref):
        return str(value)
    if isinstance(value, ns):
        raise TemplateError("secrets can only be inserted, not transformed")
    if isinstance(value, dict):
        return {k: _placeholders(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_placeholders(v) for v in value]
    if isinstance(value, (set, frozenset)) and _holds_secret(value):
        raise TemplateError("secrets can only be inserted, not transformed")
    return value


def _render_str(s: str, ctx: dict) -> Any:
    if "{{" not in s and "{%" not in s:
        return s
    m = _SINGLE_EXPR.match(s)
    if m:
        return _placeholders(_eval(m.group("expr"), ctx))
    try:
        return _env.from_string(s).render(**ctx)
    except Exception as e:
        raise TemplateError(f"{type(e).__name__}: {e}") from e


def render(value: Any, ctx: dict) -> Any:
    if isinstance(value, str):
        return _render_str(value, ctx)
    if isinstance(value, dict):
        return {k: render(v, ctx) for k, v in value.items()}
    if isinstance(value, list):
        return [render(v, ctx) for v in value]
    return value


def slack_escape(text: str) -> str:
    """Escape the three characters Slack mrkdwn treats as control characters."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# Same sandbox, but every {{ value }} is escaped for Slack mrkdwn; the author's literal template
# text (e.g. <https://ok.example|link> or <!here>) is left alone.
_slack_env = _harden(ImmutableSandboxedEnvironment(undefined=StrictUndefined, autoescape=False,
                                                   finalize=lambda v: slack_escape(str(v))))


def render_slack(value: Any, ctx: dict) -> Any:
    """Render a Slack mrkdwn text field: always returns a string for templated strings."""
    if isinstance(value, str):
        if "{{" not in value and "{%" not in value:
            return value
        try:
            return _slack_env.from_string(value).render(**ctx)
        except Exception as e:
            raise TemplateError(f"{type(e).__name__}: {e}") from e
    if isinstance(value, dict):
        return {k: render_slack(v, ctx) for k, v in value.items()}
    if isinstance(value, list):
        return [render_slack(v, ctx) for v in value]
    return value


def syntax_errors(value: Any, raw: bool = False) -> List[str]:
    errors: List[str] = []
    if isinstance(value, str):
        try:
            if raw:
                _env.compile_expression(value)
            else:
                _env.parse(value)
        except Exception as e:
            errors.append(str(e))
    elif isinstance(value, dict):
        for v in value.values():
            errors += syntax_errors(v, raw)
    elif isinstance(value, list):
        for v in value:
            errors += syntax_errors(v, raw)
    return errors
