"""Jinja2 sandboxed templating for workflow node configs and expressions.

Resource limits: templates and trigger filters are authored by tenant admins and run on the single shared
Celery worker, so every render is bounded (see `_LimitedSandbox`). Each render gets an operation budget
(loop iterations + calls + filters + intercepted operators) and a wall-clock budget; anything that builds a
string/sequence or a big int is size-checked *before* it is allocated where Python allows it, and every
produced value, the total emitted output and the final result are capped. Breaching a limit raises
TemplateError, like any other template error.
"""
import contextvars
import datetime
import decimal
import uuid
from collections.abc import ItemsView, KeysView, ValuesView
import functools
import inspect
import re
import string
import time
from contextlib import contextmanager
from typing import Any, List

from celery.exceptions import SoftTimeLimitExceeded
from jinja2 import StrictUndefined, Undefined, nodes, pass_context
from jinja2.runtime import LoopContext
from jinja2.sandbox import ImmutableSandboxedEnvironment
from jinja2.utils import Cycler, Joiner, Namespace, _PassArg  # Cycler/Joiner repr is short
from jinja2.visitor import NodeTransformer

_SINGLE_EXPR = re.compile(r"^\s*\{\{(?P<expr>(?:(?!\{\{|\}\}).)+)\}\}\s*$", re.S)

MAX_RANGE = 10_000            # items one range() may produce
MAX_SIZE = 1_000_000          # chars/items of any produced value, and of the rendered output
MAX_EMITTED = 4 * MAX_SIZE    # all output emitted during one render (macros/set-blocks included)
MAX_INT_BITS = 4096           # results of * and ** on ints
MAX_OPS = 100_000             # loop iterations + calls + filters + intercepted operators per render
MAX_SECONDS = 1.0             # wall clock per render
_MAX_FMT_DIGITS = 6           # widths/precisions in %-format and str.format specs


class TemplateError(Exception):
    pass


class TemplateLimitError(TemplateError):
    pass


def _limit(what: str):
    raise TemplateLimitError(f"template limit exceeded: {what}")


# ---------------------------------------------------------------------------------------------------------
# Per-render budget

class _Budget:
    __slots__ = ("ops", "emitted", "deadline")

    def __init__(self):
        self.ops = 0
        self.emitted = 0
        self.deadline = time.monotonic() + MAX_SECONDS


_BUDGET: "contextvars.ContextVar[_Budget | None]" = contextvars.ContextVar("wf_template_budget", default=None)


@contextmanager
def _budget():
    token = _BUDGET.set(_Budget())
    try:
        yield
    finally:
        _BUDGET.reset(token)


def _tick(n: int = 1) -> None:
    b = _BUDGET.get()
    if b is None:  # every entry point below opens a budget; size caps apply regardless
        return
    b.ops += n
    if b.ops > MAX_OPS:
        _limit(f"more than {MAX_OPS} operations")
    if time.monotonic() > b.deadline:
        _limit(f"render took longer than {MAX_SECONDS:g}s")


def _emit(n: int) -> None:
    b = _BUDGET.get()
    if b is None:
        return
    b.emitted += n
    if b.emitted > MAX_EMITTED:
        _limit("template output too large")


# ---------------------------------------------------------------------------------------------------------
# Size accounting

_OPAQUE_TYPES = (LoopContext, Cycler, Joiner, datetime.date, datetime.time, datetime.timedelta, decimal.Decimal, uuid.UUID, complex)
_SCALAR_COST = {float: 24, bool: 5, type(None): 4}
_OPAQUE_COST = 64          # datetime/Decimal/UUID, iterators, secret refs: short str()/repr()
_CALLABLE_COST = 128       # functions, macros, bound methods
_CONTAINER_ITEM_COST = 4   # repr punctuation per item: quotes, ", ", ": "
_MAX_DEPTH = 64


def _size(v: Any, budget: int = MAX_SIZE, depth: int = 0) -> int:
    """Estimated length of str()/repr()/tojson of a value (stops counting once past `budget`).

    Aliasing makes this matter: `[x] * 1000` is tiny in memory but its text repeats x 1000 times, so every
    value is charged by the text it can expand to. Unknown types are charged pessimistically."""
    if isinstance(v, (str, bytes, bytearray)):
        return len(v)
    if isinstance(v, Undefined):
        return 1  # rendering it raises
    if _is_int(v):
        return v.bit_length() * 3 // 10 + 2
    cost = _SCALAR_COST.get(type(v))
    if cost is not None:
        return cost
    if depth > _MAX_DEPTH:
        return budget + 1
    if isinstance(v, (dict, list, tuple, set, frozenset, KeysView, ValuesView, ItemsView)):
        items = v.items() if isinstance(v, dict) else v
        total = 2 + len(v) * _CONTAINER_ITEM_COST
        for x in items:
            if total > budget:
                break
            if isinstance(v, dict):
                total += _size(x[0], budget - total, depth + 1) + _size(x[1], budget - total, depth + 1)
            else:
                total += _size(x, budget - total, depth + 1)
        return total
    if isinstance(v, Namespace):
        return 16 + _size(object.__getattribute__(v, "_Namespace__attrs"), budget, depth + 1)
    if isinstance(v, range):
        return 32 + len(v)
    if isinstance(v, _OPAQUE_TYPES) or isinstance(v, _secret_classes()) or hasattr(type(v), "__next__"):
        return _OPAQUE_COST
    if callable(v):
        return _CALLABLE_COST
    return budget + 1  # unknown type: assume the worst


def _check(v: Any) -> Any:
    if isinstance(v, int) and not isinstance(v, bool) and v.bit_length() > MAX_INT_BITS:
        _limit("integer too large")
    if _size(v) > MAX_SIZE:
        _limit(f"value larger than {MAX_SIZE} characters/items")
    return v


def _need(estimate: int) -> None:
    if estimate > MAX_SIZE:
        _limit(f"value larger than {MAX_SIZE} characters/items")


def _is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


_PCT_SPEC = re.compile(r"%(?:\([^)]*\))?[-#0 +]*(\*|\d*)(?:\.(\*|\d*))?[hlL]?([diouxXeEfFgGcrsa%])")
MAX_FMT_FIELDS = 1000  # replacement fields in one format string


def _arg_values(args: Any) -> list:
    if isinstance(args, dict):
        return list(args.values())
    if isinstance(args, tuple):
        return list(args)
    return [args]


def _format_estimate(fmt_len: int, n_fields: int, widths: int, values: list) -> None:
    """Every field may repeat the largest argument: len(fmt) + fields x max(arg) + widths."""
    if n_fields > MAX_FMT_FIELDS:
        _limit(f"more than {MAX_FMT_FIELDS} format fields")
    biggest = max((_size(v) for v in values), default=0)
    _need(fmt_len + n_fields * biggest + widths)


def _spec_digits(part: str) -> int:
    if len(part) > _MAX_FMT_DIGITS:
        _limit("format width too large")
    return int(part)


def _check_pct_format(fmt: str, args: Any) -> None:
    """`fmt % args` / `fmt|format(...)`: bound fields x args and widths before Python allocates them."""
    values = _arg_values(args)
    n_fields, widths, star = 0, 0, False
    for width, prec, conv in _PCT_SPEC.findall(fmt):
        if conv == "%":
            continue
        n_fields += 1
        for part in (width, prec):
            if part == "*":
                star = True
            elif part:
                widths += _spec_digits(part)
    if star:
        widths += sum(v for v in values if _is_int(v) and v > 0)
    _format_estimate(len(fmt), n_fields, widths, values)


def _check_brace_format(fmt: str, args: tuple, kwargs: dict, mapping: Any = None) -> None:
    n_fields, widths = 0, 0
    for _literal, field, spec, _conv in string.Formatter().parse(fmt):
        if field is None:
            continue
        n_fields += 1
        if spec:
            if "{" in spec:
                _limit("nested format specifications are not allowed")
            widths += sum(_spec_digits(d) for d in re.findall(r"\d+", spec))
    values = list(args) + list(kwargs.values())
    if isinstance(mapping, dict):
        values += list(mapping.values())
    _format_estimate(len(fmt), n_fields, widths, values)


def _safe_range(*args: int) -> range:
    rng = range(*args)
    if len(rng) > MAX_RANGE:
        _limit(f"range() larger than {MAX_RANGE}")
    return rng


# ---------------------------------------------------------------------------------------------------------
# Amplifying str methods and filters: estimate the result size before running them

def _str_method_precheck(s, name: str, args: tuple, kwargs: dict):
    """Returns possibly-materialized args (str.join consumes an iterable)."""
    if name in ("center", "ljust", "rjust", "zfill"):
        width = args[0] if args else kwargs.get("width", 0)
        if _is_int(width):
            _need(max(len(s), width))
    elif name == "expandtabs":
        tab = args[0] if args else kwargs.get("tabsize", 8)
        if _is_int(tab):
            _need(len(s) + s.count("\t" if isinstance(s, str) else b"\t") * max(tab, 0))
    elif name == "replace":
        old = args[0] if args else kwargs.get("old")
        new = args[1] if len(args) > 1 else kwargs.get("new")
        count = args[2] if len(args) > 2 else kwargs.get("count", -1)
        if isinstance(old, (str, bytes)) and isinstance(new, (str, bytes)):
            hits = len(s) + 1 if not old else s.count(old)
            if _is_int(count) and count >= 0:
                hits = min(hits, count)
            _need(len(s) + hits * max(len(new) - len(old), 0))
    elif name == "join" and args:
        items = _materialize(args[0])
        _need(sum(_size(i) for i in items) + len(s) * len(items))
        return (items,) + tuple(args[1:]), kwargs
    elif name == "format" and isinstance(s, str):
        _check_brace_format(s, args, kwargs)
    elif name == "format_map" and isinstance(s, str):
        _check_brace_format(s, (), {}, mapping=args[0] if args else None)
    elif name in ("translate", "maketrans"):
        _limit(f"str.{name}() is not allowed in templates")
    return args, kwargs


def _f_replace(a):
    s, old, new, count = str(a["s"]), str(a["old"]), str(a["new"]), a["count"]
    hits = len(s) + 1 if not old else s.count(old)
    if _is_int(count) and count >= 0:
        hits = min(hits, count)
    _need(len(s) + hits * max(len(new) - len(old), 0))


def _f_center(a):
    if _is_int(a["width"]):
        _need(max(len(str(a["value"])), a["width"]))


def _f_indent(a):
    s, width = str(a["s"]), a["width"]
    w = width if _is_int(width) else len(str(width))
    _need(len(s) + (s.count("\n") + 1) * max(w, 0))


_MAX_WRAP_LINES = 20_000


def _f_wordwrap(a):
    s, width = str(a["s"]), a["width"]
    wrap = a["wrapstring"] if a["wrapstring"] is not None else "\n"
    w = width if _is_int(width) and width > 0 else 1
    if len(s) // w > _MAX_WRAP_LINES:  # textwrap is slow per line, in one uninterruptible call
        _limit(f"wordwrap would produce more than {_MAX_WRAP_LINES} lines")
    _need(len(s) + (2 * len(s) // w + s.count("\n") + 1) * len(str(wrap)))


def _f_format(a):
    args, kwargs = a.get("args", ()), a.get("kwargs", {})
    if isinstance(a["value"], str):
        _check_pct_format(a["value"], kwargs if kwargs else tuple(args))


def _f_join(a):
    items = _materialize(a["value"])
    _need(sum(_size(i) for i in items) + len(str(a["d"])) * len(items))
    a["value"] = items


def _f_batch(a):
    if _is_int(a["linecount"]) and a["linecount"] > MAX_RANGE:
        _limit(f"batch size larger than {MAX_RANGE}")


def _f_slice(a):
    if _is_int(a["slices"]) and a["slices"] > MAX_RANGE:
        _limit(f"slice count larger than {MAX_RANGE}")


def _f_sum(a):
    if isinstance(a["start"], (str, list, tuple)):
        items = _materialize(a["iterable"])
        _need(_size(a["start"]) + sum(_size(i) for i in items))
        a["iterable"] = items


def _f_tojson(a):
    indent = a["indent"]
    if indent is not None and (indent if _is_int(indent) else len(str(indent))) > 16:
        _limit("tojson indent too large")


_FILTER_PRECHECKS = {"replace": _f_replace, "center": _f_center, "indent": _f_indent, "wordwrap": _f_wordwrap,
                     "format": _f_format, "join": _f_join, "batch": _f_batch, "slice": _f_slice, "sum": _f_sum,
                     "tojson": _f_tojson}


def _is_lazy_iterable(v: Any) -> bool:
    return (hasattr(v, "__iter__") and not hasattr(v, "__len__")
            and not isinstance(v, (str, bytes, bytearray, dict, Undefined)))


def _accumulate(iterable):
    """Yield items, ticking the budget and failing once their running size passes MAX_SIZE, so a filter that
    materializes a generator (list, join, sum, sort, unique, batch, ...) never builds a huge list first."""
    total = 0
    for item in iterable:
        _tick()
        total += _size(item) + 1
        if total > MAX_SIZE:
            _limit(f"value larger than {MAX_SIZE} characters/items")
        yield item


def _materialize(iterable) -> list:
    if _is_lazy_iterable(iterable):
        return list(_accumulate(iterable))
    items = list(iterable)
    _tick(len(items))
    return items


# Filters that build the text of their (possibly non-str) input: str(), repr(), json, pprint, escaping.
_STRINGIFYING_FILTERS = frozenset([
    "string", "pprint", "tojson", "upper", "lower", "title", "capitalize", "trim", "striptags", "wordcount",
    "truncate", "center", "indent", "replace", "wordwrap", "urlize", "escape", "e", "forceescape", "safe",
    "urlencode", "xmlattr", "int", "float", "format",
])


def _limit_filter(name: str, fn):
    """Tick the budget, bound lazy inputs, pre-check known amplifiers, and size-check every result."""
    precheck = _FILTER_PRECHECKS.get(name)
    sig = inspect.signature(fn) if precheck else None
    value_pos = 0 if _PassArg.from_obj(fn) is None else 1  # skip context/eval_ctx/environment
    stringifies = name in _STRINGIFYING_FILTERS

    @functools.wraps(fn)  # keeps jinja_pass_arg (pass_context/pass_environment markers)
    def wrapper(*args, **kwargs):
        _tick()
        if len(args) > value_pos:
            value = args[value_pos]
            if _is_lazy_iterable(value):
                args = args[:value_pos] + (_accumulate(value),) + args[value_pos + 1:]
            elif stringifies and not isinstance(value, str):
                _check(value)  # its text is built in one call: estimate it first
        if precheck:
            try:
                bound = sig.bind(*args, **kwargs)
            except TypeError:
                return fn(*args, **kwargs)  # let the filter raise its own argument error
            bound.apply_defaults()
            precheck(bound.arguments)
            args, kwargs = bound.args, bound.kwargs
        return _check(fn(*args, **kwargs))
    return wrapper


# ---------------------------------------------------------------------------------------------------------
# The environment

_INTERNAL_FILTERS = ("wf:iter", "wf:cap", "wf:emit", "wf:static")


class _BudgetIter:
    """Wraps a for-loop iterable: one budget tick per item; keeps len() for loop.length/revindex."""
    __slots__ = ("_it",)

    def __init__(self, iterable):
        self._it = iterable

    def __iter__(self):
        for item in self._it:
            _tick()
            yield item

    def __len__(self):
        return len(self._it)


@pass_context
def _wf_iter(_ctx, iterable):
    return _BudgetIter(iterable)


@pass_context
def _wf_cap(_ctx, value):
    return _check(value)


@pass_context
def _wf_emit(_ctx, value):
    if isinstance(value, Undefined):
        return value  # rendering it raises the usual undefined error
    _emit(_size(value, MAX_EMITTED))  # estimated before Jinja stringifies it
    return value


@pass_context
def _wf_static(_ctx, n):
    if _is_int(n) and n > 0:
        _emit(n)
    return ""


def _wrap(node: nodes.Node, name: str) -> nodes.Filter:
    return nodes.Filter(node, name, [], [], None, None, lineno=node.lineno)


class _Instrument(NodeTransformer):
    """Budget hooks: every for-loop item ticks, every `~` result is size-checked, all output is counted."""

    def visit_For(self, node):
        self.generic_visit(node)
        node.iter = _wrap(node.iter, "wf:iter")
        return node

    def visit_Concat(self, node):
        self.generic_visit(node)
        node.nodes = [_wrap(n, "wf:cap") for n in node.nodes]  # each operand, before it is stringified
        return _wrap(node, "wf:cap")

    def visit_Output(self, node):
        self.generic_visit(node)
        out = []
        for child in node.nodes:
            if isinstance(child, nodes.TemplateData):
                # literal template text is emitted as-is (no finalize/escaping); count it separately
                out += [child, _wrap(nodes.Const(len(child.data), lineno=child.lineno), "wf:static")]
            else:
                out.append(_wrap(child, "wf:emit"))
        node.nodes = out
        return node


class _LimitedSandbox(ImmutableSandboxedEnvironment):
    # `*`, `**`, `+`, `%` go through call_binop (and are never constant-folded at compile time).
    intercepted_binops = frozenset(["*", "**", "+", "%"])

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.globals["range"] = _safe_range
        self.globals.pop("lipsum", None)  # lipsum(n) generates n paragraphs: no workflow use, easy bomb
        self.filters = {k: _limit_filter(k, f) for k, f in self.filters.items()}

    def _generate(self, source, name, filename, defer_init=False):
        source = _Instrument().visit(source)
        source.set_environment(self)
        return super()._generate(source, name, filename, defer_init=defer_init)

    def call_filter(self, name, *args, **kwargs):
        if isinstance(name, str) and name.startswith("wf:"):  # map('wf:...'), select(...) by name
            raise TemplateError(f"No filter named {name!r}.")
        return super().call_filter(name, *args, **kwargs)

    def call_test(self, name, *args, **kwargs):
        if isinstance(name, str) and name.startswith("wf:"):
            raise TemplateError(f"No test named {name!r}.")
        return super().call_test(name, *args, **kwargs)

    def call_binop(self, context, op, left, right):
        _tick()
        if op == "**":
            if _is_int(left) and _is_int(right) and right > 0 and abs(left) > 1:
                if right > MAX_INT_BITS or right * left.bit_length() > MAX_INT_BITS:
                    _limit("exponent too large")
        elif op == "*":
            for seq, n in ((left, right), (right, left)):
                if isinstance(seq, (str, bytes, list, tuple)) and _is_int(n):
                    _need(max(n, 0) * max(_size(seq), 1))
            if _is_int(left) and _is_int(right) and left.bit_length() + right.bit_length() > MAX_INT_BITS:
                _limit("integer too large")
        elif op == "+":
            if isinstance(left, (str, bytes, list, tuple)) and isinstance(right, (str, bytes, list, tuple)):
                _need(_size(left) + _size(right))
        elif op == "%" and isinstance(left, str):
            _check_pct_format(left, right)
        return _check(super().call_binop(context, op, left, right))

    def call(__self, __context, __obj, *args, **kwargs):  # noqa: N805 (mirrors jinja's signature)
        _tick()
        target = getattr(__obj, "__wrapped__", __obj)  # str.format is wrapped by the sandbox
        owner = getattr(target, "__self__", None)
        if isinstance(owner, (str, bytes)):
            args, kwargs = _str_method_precheck(owner, getattr(target, "__name__", ""), args, kwargs)
        return _check(super().call(__context, __obj, *args, **kwargs))


# Immutable: templates can't mutate context lists/dicts (e.g. case.tags.append).
_env = _LimitedSandbox(undefined=StrictUndefined, autoescape=False)


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
    wrapper._secret_guarded = True
    return wrapper


def _harden(env: ImmutableSandboxedEnvironment) -> ImmutableSandboxedEnvironment:
    # Wraps the filters/tests present now; anything registered later must be wrapped too
    # (test_secret_refs asserts every filter/test is guarded).
    env.filters = {k: _guard_secrets(f) for k, f in env.filters.items()}
    env.tests = {k: _guard_secrets(f) for k, f in env.tests.items()}
    return env


def _add_internal_filters(env: ImmutableSandboxedEnvironment) -> ImmutableSandboxedEnvironment:
    # Budget hooks inserted by _Instrument. Deliberately NOT secret-guarded: they return their argument
    # unchanged (or "" for _wf_static), so a secret passing through is still only inserted.
    env.filters.update({"wf:iter": _wf_iter, "wf:cap": _wf_cap, "wf:emit": _wf_emit, "wf:static": _wf_static})
    return env


_add_internal_filters(_harden(_env))


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
        with _budget():
            return _check(_force(_env.compile_expression(expr, undefined_to_none=False)(**ctx)))
    except (TemplateError, SoftTimeLimitExceeded):
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
    return _render_template(_env, s, ctx)


def _render_template(env: ImmutableSandboxedEnvironment, s: str, ctx: dict) -> str:
    try:
        with _budget():
            out = env.from_string(s).render(**ctx)
    except (TemplateLimitError, SoftTimeLimitExceeded):
        raise
    except Exception as e:
        raise TemplateError(f"{type(e).__name__}: {e}") from e
    if len(out) > MAX_SIZE:
        _limit(f"rendered output larger than {MAX_SIZE} characters")
    return out


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
_slack_env = _add_internal_filters(_harden(_LimitedSandbox(undefined=StrictUndefined, autoescape=False,
                                                            finalize=lambda v: slack_escape(str(v)))))


def render_slack(value: Any, ctx: dict) -> Any:
    """Render a Slack mrkdwn text field: always returns a string for templated strings."""
    if isinstance(value, str):
        if "{{" not in value and "{%" not in value:
            return value
        return _render_template(_slack_env, value, ctx)
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
