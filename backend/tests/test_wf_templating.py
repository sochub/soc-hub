import pytest

from app.workflows.templating import render, eval_expr, syntax_errors, TemplateError

CTX = {
    "case": {"id": 7, "title": "Phish", "tags": ["phishing", "vip"], "severity": "high"},
    "alert": {"payload": {"severity": None, "host": "web-1"}},
    "steps": {"lookup": {"output": {"status": 200, "body": {"users": ["a@x.com", "b@x.com"]}}}},
}


def test_plain_string_untouched():
    assert render("hello", CTX) == "hello"


def test_interpolation():
    assert render("Case #{{ case.id }}: {{ case.title }}", CTX) == "Case #7: Phish"


def test_single_expression_returns_native_value():
    assert render("{{ steps.lookup.output.body.users }}", CTX) == ["a@x.com", "b@x.com"]
    assert render("{{ case.id }}", CTX) == 7


def test_recurses_into_dicts_and_lists():
    out = render({"a": ["{{ case.id }}", "x"], "b": {"c": "{{ case.title }}"}}, CTX)
    assert out == {"a": [7, "x"], "b": {"c": "Phish"}}


def test_eval_expr_boolean():
    assert eval_expr("'phishing' in case.tags", CTX) is True
    assert eval_expr("case.severity == 'low'", CTX) is False


def test_missing_path_error_names_attribute():
    with pytest.raises(TemplateError) as e:
        render("{{ steps.lookup.output.body.user.email }}", CTX)
    assert "user" in str(e.value)


def test_comparison_with_none_raises_template_error():
    with pytest.raises(TemplateError):
        eval_expr("alert.payload.severity >= 7", CTX)


@pytest.mark.parametrize("tpl", [
    "{{ ''.__class__.__mro__ }}",
    "{{ case.__class__ }}",
    "{{ ''.__class__.__mro__[1].__subclasses__() }}",
])
def test_sandbox_blocks_dunder_escapes(tpl):
    with pytest.raises(TemplateError):
        render(tpl, CTX)


def test_syntax_errors():
    assert syntax_errors("{{ case.id }}") == []
    assert syntax_errors("{{ case.id ") != []
    assert syntax_errors({"a": ["{% if %}"]}) != []
    assert syntax_errors("case.id == 1", raw=True) == []
    assert syntax_errors("case.id ==", raw=True) != []
