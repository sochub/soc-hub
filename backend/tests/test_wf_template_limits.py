"""Resource limits on workflow templates: a tenant's template or trigger filter must fail fast instead of
stalling the shared worker (unbounded range/repetition/exponentiation/loops/output)."""
import time

import pytest

from app.workflows.events import trigger_matches
from app.workflows.templating import TemplateError, eval_expr, render, render_slack

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
    assert time.monotonic() - start < 1.0, tpl


@pytest.mark.parametrize("expr", ["range(10**9)|list", "'x' * 10**9", "9 ** (9 ** 9)",
                                  "(range(10000)|list) * 10000"])
def test_expression_bombs_fail_fast(expr):
    start = time.monotonic()
    with pytest.raises(TemplateError):
        eval_expr(expr, CTX)
    assert time.monotonic() - start < 1.0


def test_slack_render_is_limited_too():
    start = time.monotonic()
    with pytest.raises(TemplateError):
        render_slack("{% for a in range(10000) %}{% for b in range(10000) %}{% endfor %}{% endfor %}", CTX)
    with pytest.raises(TemplateError):
        render_slack("{{ 'x' * 10**9 }}", CTX)
    assert time.monotonic() - start < 1.0


def test_trigger_filter_bomb_is_no_match_and_fast():
    start = time.monotonic()
    assert trigger_matches("('x' * 10**9)|length > 1", CTX) is False
    assert trigger_matches("9 ** (9 ** 9) > 1", CTX) is False
    assert time.monotonic() - start < 1.0


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
