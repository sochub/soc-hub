import pytest

from app.workflows.templating import render, render_slack, eval_expr, syntax_errors, TemplateError

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


EVIL = {"alert": {"payload": {"x": "<https://evil|click> <!channel> & co"}}}


def test_slack_render_escapes_interpolated_values():
    assert render_slack("Hi {{ alert.payload.x }}", EVIL) == "Hi &lt;https://evil|click&gt; &lt;!channel&gt; &amp; co"
    assert render_slack("{{ alert.payload.x }}", EVIL) == "&lt;https://evil|click&gt; &lt;!channel&gt; &amp; co"


def test_slack_render_keeps_author_markup():
    assert render_slack("<https://ok.example|ok> <!here> {{ case.title }}", CTX) == "<https://ok.example|ok> <!here> Phish"
    assert render_slack("<https://ok.example|ok>", CTX) == "<https://ok.example|ok>"


def test_slack_render_strict_and_sandboxed():
    with pytest.raises(TemplateError):
        render_slack("{{ nope }}", CTX)
    with pytest.raises(TemplateError):
        render_slack("{{ case.__class__ }}", CTX)


def test_render_config_escapes_only_slack_text():
    from app.workflows.runtime import render_config
    post = {"id": "p", "type": "slack_post_message", "config": {"text": "{{ alert.payload.x }}", "channel": "#soc"}}
    assert render_config(post, EVIL)["text"] == "&lt;https://evil|click&gt; &lt;!channel&gt; &amp; co"
    ask = {"id": "a", "type": "slack_ask_user", "config": {"email": "a@x.com", "message": "Is {{ alert.payload.x }} ok?",
                                                            "buttons": "{{ alert.payload.b }}"}}
    out = render_config(ask, {"alert": {"payload": {**EVIL["alert"]["payload"], "b": ["A&B", "C"]}}})
    assert out["message"] == "Is &lt;https://evil|click&gt; &lt;!channel&gt; &amp; co ok?"
    assert out["buttons"] == ["A&B", "C"], "plain_text button labels keep native values"
    note = {"id": "n", "type": "case_add_note", "config": {"content": "{{ alert.payload.x }}"}}
    assert render_config(note, EVIL)["content"] == "<https://evil|click> <!channel> & co"
