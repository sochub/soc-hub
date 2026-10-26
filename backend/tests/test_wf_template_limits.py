"""Resource limits on workflow templates: a tenant's template or trigger filter must fail fast instead of
stalling the shared worker (unbounded range/repetition/exponentiation/loops/output)."""
import time
import tracemalloc

import pytest

from app.workflows.events import trigger_matches
from app.workflows import templating
from app.workflows.templating import TemplateError, TemplateLimitError, eval_expr, render, render_slack

CTX = {"case": {"id": 7, "title": "Phish", "tags": ["phishing", "vip"]}, "items": list(range(50))}

# NB: Jinja's ** is left-associative, so `9 ** 9 ** 9` is (9**9)**9 (257 bits, harmless); the bomb is 9 ** (9 ** 9).
BOMBS = [
    "{{ range(10**9)|list }}",
    "{{ 'x' * 10**9 }}",
    "{{ 9 ** (9 ** 9) }}",
    "{{ 2 ** 100000 }}",
    "{{ [1] * 10**9 }}",
    "{{ ['x' * 100000] * 100000 }}",
    # nested loops: each range is small, the product is not
    "{% for a in range(10000) %}{% for b in range(10000) %}{% endfor %}{% endfor %}",
    "{% for a in range(1000) %}{% for b in range(1000) %}{% for c in range(1000) %}x{% endfor %}{% endfor %}{% endfor %}",
    # output amplification
    "{% for a in range(10000) %}{{ 'y' * 900000 }}{% endfor %}",
    "{% set ns = namespace(s='x') %}{% for i in range(60) %}{% set ns.s = ns.s ~ ns.s %}{% endfor %}{{ ns.s|length }}",
    "{% macro f(n) %}{% if n %}{{ f(n - 1) }}{{ f(n - 1) }}{% endif %}{% endmacro %}{{ f(40) }}",
    "{% macro d(s, n) %}{% if n %}{{ d(s ~ s, n - 1) }}{% else %}{{ s|length }}{% endif %}{% endmacro %}{{ d('x', 60) }}",
    # padding / replace / format amplifiers
    "{{ 'x'.ljust(10**9) }}",
    "{{ 'x'|center(10**9) }}",
    "{{ ('x' * 1000)|replace('x', 'y' * 100000) }}",
    "{{ ('x' * 1000).replace('x', 'y' * 100000) }}",
    "{{ ('a\n' * 400000)|indent(1000) }}",
    "{{ '%999999999s'|format(1) }}",
    "{{ '%999999999s' % 1 }}",
    "{{ '{:>999999999}'.format(1) }}",
    "{{ lipsum(100000) }}",
]


@pytest.mark.parametrize("tpl", BOMBS)
def test_bombs_fail_fast(tpl):
    start = time.monotonic()
    with pytest.raises(TemplateError):
        render(tpl, CTX)
    assert time.monotonic() - start < 5.0, tpl


@pytest.mark.parametrize("expr", ["range(10**9)|list", "'x' * 10**9", "9 ** (9 ** 9)",
                                  "(range(10000)|list) * 10000"])
def test_expression_bombs_fail_fast(expr):
    start = time.monotonic()
    with pytest.raises(TemplateError):
        eval_expr(expr, CTX)
    assert time.monotonic() - start < 5.0


def test_slack_render_is_limited_too():
    start = time.monotonic()
    with pytest.raises(TemplateError):
        render_slack("{% for a in range(10000) %}{% for b in range(10000) %}{% endfor %}{% endfor %}", CTX)
    with pytest.raises(TemplateError):
        render_slack("{{ 'x' * 10**9 }}", CTX)
    assert time.monotonic() - start < 5.0


def test_trigger_filter_bomb_is_no_match_and_fast():
    start = time.monotonic()
    assert trigger_matches("('x' * 10**9)|length > 1", CTX) is False
    assert trigger_matches("9 ** (9 ** 9) > 1", CTX) is False
    assert time.monotonic() - start < 5.0


def test_normal_templates_unchanged():
    assert render("Case #{{ case.id }}: {{ case.title }}", CTX) == "Case #7: Phish"
    assert render("{{ case.tags|join(', ') }}", CTX) == "phishing, vip"
    assert render("{% for t in case.tags %}[{{ t|upper }}]{% endfor %}", CTX) == "[PHISHING][VIP]"
    assert render("{% for i in items %}{{ loop.index }}{% if not loop.last %},{% endif %}{% endfor %}", CTX) \
        == ",".join(str(i) for i in range(1, 51))
    assert render("{{ range(3)|list }}", CTX) == [0, 1, 2]
    assert render("{{ 2 ** 10 }}", CTX) == 1024
    assert render("{{ 9 ** 9 ** 9 }}", CTX) == (9 ** 9) ** 9
    assert render("{{ 'ab' * 3 }}", CTX) == "ababab"
    assert render("{{ [1, 2] + [3] }}", CTX) == [1, 2, 3]
    assert render("{{ '%s-%03d'|format('a', 7) }}", CTX) == "a-007"
    assert render("{{ '%s/%s' % ('a', 'b') }}", CTX) == "a/b"
    assert render("{{ '{}:{:>4}'.format('k', 1) }}", CTX) == "k:   1"
    assert render("{{ 'x'.ljust(5, '.') }}", CTX) == "x...."
    assert render("{{ 'a-b'|replace('-', '+') }}", CTX) == "a+b"
    assert render("{{ case|tojson }}", CTX) == '{"id": 7, "tags": ["phishing", "vip"], "title": "Phish"}'
    assert render("{% set ns = namespace(n=0) %}{% for i in items %}{% set ns.n = ns.n + i %}{% endfor %}{{ ns.n }}",
                  CTX) == "1225"
    assert render("{% macro b(x) %}<{{ x }}>{% endmacro %}{{ b(case.title) }}", CTX) == "<Phish>"
    assert render("{{ 'x' ~ case.id ~ 'y' }}", CTX) == "x7y"
    assert eval_expr("'vip' in case.tags and case.id > 3", CTX) is True
    assert render_slack("*{{ case.title }}* <https://x.example|link>", {"case": {"title": "a<b"}}) \
        == "*a&lt;b* <https://x.example|link>"
    assert trigger_matches("case.id == 7", CTX) is True


def test_moderate_loops_still_work():
    # 100 x 100 nested iterations with output is fine
    out = render("{% for a in range(100) %}{% for b in range(100) %}.{% endfor %}{% endfor %}", CTX)
    assert out == "." * 10000


def test_output_limit_is_about_one_megabyte():
    assert len(render("{{ 'x' * 900000 }}", CTX)) == 900000
    with pytest.raises(TemplateError):
        render("{% for i in range(3) %}{{ 'x' * 900000 }}{% endfor %}", CTX)


@pytest.mark.parametrize("tpl", [
    "{{ ''.__class__.__mro__ }}",
    "{{ case.__class__ }}",
    "{{ range.__globals__ }}",
    "{{ cycler.__init__.__globals__ }}",
])
def test_sandbox_still_blocks_attribute_escapes(tpl):
    with pytest.raises(TemplateError):
        render(tpl, CTX)


# Vectors that used to allocate large amounts *before* any post-check could fire: format fields repeating a
# big argument, and filters materializing a generator of big items. They must be rejected up front.
MEMORY_VECTORS = [
    "{{ ('{0}' * 10000).format('x' * 10000) }}",
    "{{ ('{0}' * 100000).format('x' * 600000) }}",
    "{{ ('{0}' * 900).format('x' * 10000) }}",
    "{{ ('{a}' * 900).format(a='x' * 10000) }}",
    "{{ ('{a}' * 900).format_map({'a': 'x' * 10000}) }}",
    "{{ ('%(a)s' * 900) % {'a': 'x' * 10000} }}",
    "{{ ('%s' * 900) % (('x' * 10000,) * 900) }}",
    "{{ ('%(a)s' * 900)|format(a='x' * 10000) }}",
    "{{ ('%s' * 900)|format(*(('x' * 10000,) * 900)) }}",
    "{{ range(800)|map('center', 999999)|list|length }}",
    "{{ range(800)|map('center', 999999)|join|length }}",
    "{{ range(800)|map('center', 999999)|sort|length }}",
    "{{ range(800)|map('center', 999999)|unique|list|length }}",
    "{{ range(800)|map('center', 999999)|batch(1)|sum(start=[])|length }}",
    "{{ ''.join(range(800)|map('center', 999999))|length }}",
    "{% for x in range(800)|map('center', 999999)|list %}{% endfor %}",
]


def _peak(fn):
    tracemalloc.start()
    try:
        fn()
    finally:
        peak = tracemalloc.get_traced_memory()[1]
        tracemalloc.stop()
    return peak


@pytest.mark.parametrize("tpl", MEMORY_VECTORS)
def test_memory_vectors_rejected_with_small_peak(tpl):
    def go():
        with pytest.raises(TemplateLimitError):
            render(tpl, CTX)
    assert _peak(go) < 20 * 1024 * 1024, tpl


def test_format_field_count_limit():
    with pytest.raises(TemplateLimitError, match="format fields"):
        render("{{ ('{0}' * 2000).format('') }}", CTX)
    assert render("{{ ('{0}' * 10).format('ab') }}", CTX) == "ab" * 10


def test_trigger_filter_format_vector_is_no_match_with_small_peak():
    assert _peak(lambda: trigger_matches("(('{0}' * 900).format('x' * 10000))|length > 0", CTX)) < 20 * 1024 * 1024
    assert trigger_matches("(('{0}' * 900).format('x' * 10000))|length > 0", CTX) is False


def test_lazy_pipelines_of_reasonable_size_still_work():
    assert render("{{ range(5)|map('string')|join(',') }}", CTX) == "0,1,2,3,4"
    assert render("{{ case.tags|map('upper')|list }}", CTX) == ["PHISHING", "VIP"]
    assert render("{{ range(100)|select('odd')|sum }}", CTX) == 2500
    assert render("{{ range(3)|map('string')|batch(2)|list }}", CTX) == [["0", "1"], ["2"]]


@pytest.mark.parametrize("tpl", [
    "{{ -999999999|_wf_static }}",
    "{{ 1|wf_static }}",
    "{{ [1]|_wf_iter|list }}",
])
def test_internal_hooks_are_not_callable_from_templates(tpl):
    with pytest.raises(TemplateError):
        render(tpl, CTX)


def test_static_hook_ignores_negative_counts():
    from app.workflows.templating import _BUDGET, _budget, _wf_static
    with _budget():
        _wf_static(None, -10 ** 9)
        assert _BUDGET.get().emitted == 0


def test_wall_clock_budget(monkeypatch):
    clock = iter(range(0, 10 ** 9, 1))  # each read advances 1 s
    monkeypatch.setattr(templating.time, "monotonic", lambda: float(next(clock)))
    with pytest.raises(TemplateLimitError, match="longer than"):
        render("{% for i in range(5) %}{{ i }}{% endfor %}", CTX)


# Aliasing: one big value repeated N times is small in memory but huge as text. Every value is charged by
# the text it expands to, so the repetition (or the stringification) is refused before it happens.
ALIAS_VECTORS = [
    "{% set ns = namespace(a='x' * 900000) %}{{ ([ns] * 1000)|pprint|length }}",
    "{% set ns = namespace(a='x' * 900000) %}{{ ('{0}' * 900).format(ns)|length }}",
    "{% set ns = namespace(a='x' * 900000) %}{{ ('%s' * 900) % ((ns,) * 900) }}",
    "{% set ns = namespace(a='x' * 900000) %}{% for i in range(1000) %}{{ ns }}{% endfor %}",
    "{% set ns = namespace(a='x' * 900000) %}{{ ({}.fromkeys(range(1000), ns))|tojson|length }}",
    "{{ ([10 ** 1000] * 400000)|pprint|length }}",
    "{{ ([10 ** 1000] * 400000)|tojson|length }}",
    "{{ ('' ~ ([10 ** 1000] * 400000))|length }}",
    "{{ ([10 ** 1000] * 400000)|string|length }}",
    "x{{ [10 ** 1000] * 400000 }}",
    "{% set d = {'k': 'x' * 900000} %}x{{ [d.items()] * 1000 }}",
    "{% set d = {'k': 'x' * 900000} %}x{{ [d.values()] * 1000 }}",
    "{% set c = cycler('x' * 900000) %}x{{ [c.items] * 1000 }}",
    "{% set ns = namespace(a='x' * 900000) %}{{ ([ns, ns, ns, ns, ns, ns])|string|length }}",
]


@pytest.mark.parametrize("tpl", ALIAS_VECTORS)
def test_alias_vectors_rejected_with_small_peak(tpl):
    def go():
        with pytest.raises(TemplateLimitError):
            render(tpl, CTX)
    assert _peak(go) < 20 * 1024 * 1024, tpl


def test_alias_vectors_in_slack_render():
    def go():
        with pytest.raises(TemplateLimitError):
            render_slack("x{{ [10 ** 1000] * 400000 }}", CTX)
    assert _peak(go) < 20 * 1024 * 1024


@pytest.mark.parametrize("expr", [
    "(([namespace(a='x' * 900000)] * 1000)|join|length) > 0",
    "(([10 ** 1000] * 400000)|string|length) > 0",
])
def test_alias_vectors_in_trigger_filters_and_expressions(expr):
    def go():
        assert trigger_matches(expr, CTX) is False
        with pytest.raises(TemplateLimitError):
            eval_expr(expr, CTX)
    start = time.monotonic()
    assert _peak(go) < 20 * 1024 * 1024
    assert time.monotonic() - start < 5.0


def test_reasonable_values_still_stringify():
    assert render("{% set ns = namespace(a='hi') %}{{ ns.a }} {{ [1, 2]|string }}", CTX) == "hi [1, 2]"
    assert render("x{{ 10 ** 300 }}", CTX) == "x" + str(10 ** 300)
    assert render("{{ case|tojson|length > 10 }}", CTX) is True
    assert render("{% set d = {'k': 'v'} %}x{{ d.items()|list }}", CTX) == "x[('k', 'v')]"
    assert render("{% for i in range(3) %}{{ loop.cycle('a', 'b') }}{% endfor %}", CTX) == "aba"
    assert render("{% set c = cycler('x' * 900000) %}{{ ([c] * 1000)|length }}", CTX) == "1000"  # short repr


def test_wordwrap_pathological_width_rejected_quickly():
    start = time.monotonic()
    with pytest.raises(TemplateLimitError):
        render("{{ ('x' * 330000)|wordwrap(1)|length }}", CTX)
    assert time.monotonic() - start < 1.0
    assert render("{{ 'aaa bbb ccc'|wordwrap(4) }}", CTX) == "aaa\nbbb\nccc"


@pytest.mark.parametrize("tpl", [
    "{{ [-999999999]|map('wf:static')|list }}",
    "{{ [[1]]|map('wf:iter')|list }}",
    "{{ [1]|select('wf:cap')|list }}",
])
def test_hooks_not_reachable_through_map_or_select(tpl):
    with pytest.raises(TemplateError):
        render(tpl, CTX)
