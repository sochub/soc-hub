import copy

import pytest

from app.secrets.refs import PLACEHOLDER_RE, SecretRef, SecretsNamespace
from app.workflows.templating import TemplateError, eval_expr, render

CTX = {"secrets": SecretsNamespace(), "case": {"title": "t"}}


def test_inline_and_single_expression_render_placeholder():
    assert render("Bearer {{ secrets.JIRA_TOKEN }}", CTX) == "Bearer ⟦secret:JIRA_TOKEN⟧"
    assert render("{{ secrets.JIRA_TOKEN }}", CTX) == "⟦secret:JIRA_TOKEN⟧"
    assert render({"h": ["{{ secrets.A_B }}"]}, CTX) == {"h": ["⟦secret:A_B⟧"]}
    assert PLACEHOLDER_RE.findall("x ⟦secret:A_B⟧ y") == ["A_B"]


@pytest.mark.parametrize("tpl", [
    "{{ secrets.X | upper }}", "{{ secrets.X[0:3] }}", "{{ secrets.X == 'a' }}", "{{ secrets.X | length }}",
    "{{ (secrets.X ~ 'y') | b64encode }}", "{{ secrets.X + 'y' }}", "{{ 'a' in secrets.X }}",
    "{{ secrets.X.lower() }}", "{% if secrets.X %}y{% endif %}", "{{ secrets.lower_bad }}",
])
def test_transforms_rejected(tpl):
    with pytest.raises(TemplateError):
        render(tpl, CTX)


def test_expression_contexts_reject():
    with pytest.raises(TemplateError):
        eval_expr("secrets.X == 'a'", CTX)


# --- extra hardening checks ---------------------------------------------------

def test_item_access_and_concat():
    assert render("{{ secrets['JIRA_TOKEN'] }}", CTX) == "⟦secret:JIRA_TOKEN⟧"
    # R1: plain ~ concatenation yields placeholder text only (allowed).
    assert render("{{ 'Basic ' ~ secrets.JIRA_TOKEN }}", CTX) == "Basic ⟦secret:JIRA_TOKEN⟧"
    # str-formatting is the same as ~: it only ever sees the placeholder text.
    assert render("{{ '%s' % secrets.AB }}", CTX) == "⟦secret:AB⟧"
    assert render("{{ '{}'.format(secrets.AB) }}", CTX) == "⟦secret:AB⟧"
    assert render("{{ [secrets.AB, 'x'] }}", CTX) == ["⟦secret:AB⟧", "x"]


@pytest.mark.parametrize("tpl", [
    "Bearer {{ secrets.TOK | upper }}", "a {{ secrets.TOK | lower }}", "{{ secrets.TOK | replace('a','b') }}",
    "{{ secrets.TOK | string }}", "{{ secrets.TOK | list }}", "{{ secrets.TOK | urlencode }}",
    "{{ secrets.TOK | tojson }}", "{{ secrets.TOK | trim }}", "{{ secrets.TOK | first }}", "{{ secrets.TOK | reverse }}",
    "{{ [secrets.TOK] | map('upper') | list }}", "{{ secrets.TOK * 2 }}",
    "{{ secrets.TOK is string }}", "{{ secrets.TOK < 'a' }}", "{{ secrets.TOK._name }}", "{{ secrets.TOK.__class__ }}",
    "{{ secrets.__class__ }}", "{{ secrets.TOK.upper }}", "{{ secrets['bad'] }}", "{{ secrets.TOK1 if secrets.TOK else 'n' }}",
    "{% for c in secrets.TOK %}{{ c }}{% endfor %}", "{{ secrets.TOK | default('z') }}",
])
def test_more_transforms_rejected(tpl):
    with pytest.raises(TemplateError):
        render(tpl, CTX)


# The brief's cases use `secrets.X`, which already fails the name pattern (2+ chars). Re-run them
# with a VALID name so they prove the transformation itself is refused.
@pytest.mark.parametrize("tpl", [
    "{{ secrets.TOK | upper }}", "{{ secrets.TOK[0:3] }}", "{{ secrets.TOK == 'a' }}", "{{ secrets.TOK | length }}",
    "{{ secrets.TOK + 'y' }}", "{{ 'a' in secrets.TOK }}", "{{ secrets.TOK.lower() }}",
    "{% if secrets.TOK %}y{% endif %}", "Bearer {{ secrets.TOK | upper }}", "{{ secrets.TOK | default('z') }}",
    "{{ [secrets.TOK] | map('upper') | list }}", "{{ secrets.TOK * 2 }}", "{{ secrets.TOK is string }}",
])
def test_valid_name_transforms_rejected_with_message(tpl):
    with pytest.raises(TemplateError, match="secrets can only be inserted, not transformed"):
        render(tpl, CTX)


def test_b64encode_never_yields_valid_placeholder():
    try:
        out = render("{{ (secrets.TOK1 ~ 'y') | b64encode }}", CTX)
    except TemplateError:
        return
    assert not PLACEHOLDER_RE.search(str(out))


@pytest.mark.parametrize("expr", [
    "secrets.TOK", "secrets.TOK != 'a'", "secrets.TOK | length > 3", "'a' in secrets.TOK", "secrets.bad", "not secrets.TOK",
])
def test_eval_expr_rejects_or_is_opaque(expr):
    # A bare reference in an expression context is a SecretRef (opaque); anything observing it raises.
    if expr == "secrets.TOK":
        assert isinstance(eval_expr(expr, CTX), SecretRef)
        return
    with pytest.raises(TemplateError):
        eval_expr(expr, CTX)


def test_template_error_message_preserved():
    with pytest.raises(TemplateError, match="secrets can only be inserted, not transformed"):
        eval_expr("secrets.TOK == 'a'", CTX)
    with pytest.raises(TemplateError, match="secrets can only be inserted, not transformed"):
        eval_expr("secrets.TOK > 1", CTX)
    with pytest.raises(TemplateError, match="Invalid secret name lower_bad"):
        eval_expr("secrets.lower_bad", CTX)


def test_python_protocols_do_not_explode():
    ns = SecretsNamespace()
    assert not hasattr(ns, "__deepcopy__")
    copy.deepcopy({"secrets": ns})
    assert repr(ns.ABC) == "SecretRef(ABC)"
    with pytest.raises(TemplateError):
        hash(ns.ABC)
    with pytest.raises(TemplateError):
        bool(ns.ABC)
    with pytest.raises(TemplateError):
        f"{ns.ABC:>10}"
    assert f"{ns.ABC}" == "⟦secret:ABC⟧"
