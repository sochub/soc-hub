"""Jinja2 sandboxed templating for workflow node configs and expressions."""
import re
from typing import Any, List

from jinja2 import StrictUndefined, Undefined
from jinja2.sandbox import SandboxedEnvironment

_env = SandboxedEnvironment(undefined=StrictUndefined, autoescape=False)
_SINGLE_EXPR = re.compile(r"^\s*\{\{(?P<expr>(?:(?!\{\{|\}\}).)+)\}\}\s*$", re.S)


class TemplateError(Exception):
    pass


def _force(value: Any) -> Any:
    # A sandbox-blocked or missing attribute comes back as an Undefined; make it raise.
    if isinstance(value, Undefined):
        value._fail_with_undefined_error()
    return value


def eval_expr(expr: str, ctx: dict) -> Any:
    try:
        return _force(_env.compile_expression(expr, undefined_to_none=False)(**ctx))
    except TemplateError:
        raise
    except Exception as e:  # jinja errors, SecurityError, TypeError on None comparisons, ...
        raise TemplateError(f"{type(e).__name__}: {e}") from e


def _render_str(s: str, ctx: dict) -> Any:
    if "{{" not in s and "{%" not in s:
        return s
    m = _SINGLE_EXPR.match(s)
    if m:
        return eval_expr(m.group("expr"), ctx)
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
