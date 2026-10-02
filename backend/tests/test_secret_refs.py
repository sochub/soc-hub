import copy

import pytest

from app.secrets.refs import ANY_PLACEHOLDER_RE, SecretRef, SecretsNamespace, placeholder_re
from app.workflows.nodes import NodeContext
from app.workflows.runtime import bind_secrets
from app.workflows.templating import TemplateError, eval_expr, render

N = "0123456789abcdef"
CTX = {"secrets": SecretsNamespace(N), "case": {"title": "t"}}


def test_inline_and_single_expression_render_placeholder():
    assert render("Bearer {{ secrets.JIRA_TOKEN }}", CTX) == f"Bearer ⟦secret:JIRA_TOKEN#{N}⟧"
    assert render("{{ secrets.JIRA_TOKEN }}", CTX) == f"⟦secret:JIRA_TOKEN#{N}⟧"
    assert render({"h": ["{{ secrets.A_B }}"]}, CTX) == {"h": [f"⟦secret:A_B#{N}⟧"]}
    assert placeholder_re(N).findall(f"x ⟦secret:A_B#{N}⟧ y") == ["A_B"]
    assert ANY_PLACEHOLDER_RE.findall(f"x ⟦secret:A_B#{N}⟧ ⟦secret:C_D⟧ y") == ["A_B", "C_D"]


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
    assert render("{{ secrets['JIRA_TOKEN'] }}", CTX) == f"⟦secret:JIRA_TOKEN#{N}⟧"
    # R1: plain ~ concatenation yields placeholder text only (allowed).
    assert render("{{ 'Basic ' ~ secrets.JIRA_TOKEN }}", CTX) == f"Basic ⟦secret:JIRA_TOKEN#{N}⟧"
    # str-formatting is the same as ~: it only ever sees the placeholder text.
    assert render("{{ '%s' % secrets.AB }}", CTX) == f"⟦secret:AB#{N}⟧"
    assert render("{{ '{}'.format(secrets.AB) }}", CTX) == f"⟦secret:AB#{N}⟧"
    assert render("{{ [secrets.AB, 'x'] }}", CTX) == [f"⟦secret:AB#{N}⟧", "x"]


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
    assert not ANY_PLACEHOLDER_RE.search(str(out))


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
    ns = SecretsNamespace(N)
    assert not hasattr(ns, "__deepcopy__")
    copy.deepcopy({"secrets": ns})
    assert repr(ns.ABC) == "SecretRef(ABC)"
    with pytest.raises(TemplateError):
        hash(ns.ABC)
    with pytest.raises(TemplateError):
        bool(ns.ABC)
    with pytest.raises(TemplateError):
        f"{ns.ABC:>10}"
    assert f"{ns.ABC}" == f"⟦secret:ABC#{N}⟧"


# --- R2: per-step nonce so data cannot forge a resolvable placeholder ----------------------------

def _step_render(tpl, data_ctx):
    nctx = NodeContext(db=None, run=None, step=None, node={}, ctx=dict(data_ctx))
    bind_secrets(nctx)
    return nctx, render(tpl, nctx.ctx)


def test_rendered_placeholder_carries_step_nonce():
    nctx, out = _step_render("Bearer {{ secrets.TOK }}", {"case": {"title": "t"}})
    assert len(nctx.secret_nonce) == 16
    assert out == f"Bearer ⟦secret:TOK#{nctx.secret_nonce}⟧"
    assert placeholder_re(nctx.secret_nonce).findall(out) == ["TOK"]


def test_two_renders_get_different_nonces():
    a, out_a = _step_render("{{ secrets.TOK }}", {})
    b, out_b = _step_render("{{ secrets.TOK }}", {})
    assert a.secret_nonce != b.secret_nonce and out_a != out_b
    # A placeholder from another render (e.g. echoed into a step output) never resolves here.
    assert not placeholder_re(b.secret_nonce).search(out_a)


def test_forged_placeholders_from_data_stay_inert():
    # Data is fixed before the nonce exists, so it can only carry no nonce, a guessed one, or a stale one.
    _, stale = _step_render("{{ secrets.TOK }}", {})
    data = {"case": {"title": f"⟦secret:TOK⟧ ⟦secret:TOK#0000000000000000⟧ {stale}"}}
    nctx, out = _step_render("{{ case.title }} | {{ secrets.TOK }}", data)
    pat = placeholder_re(nctx.secret_nonce)
    assert pat.findall(out) == ["TOK"]  # only the author's real reference
    for forged in ("⟦secret:TOK⟧", "⟦secret:TOK#0000000000000000⟧", stale):
        assert forged in out and not pat.search(forged)


def test_placeholder_re_escapes_and_is_exact():
    pat = placeholder_re(N)
    assert not pat.search(f"⟦secret:TOK#{N}0⟧")
    assert not pat.search(f"⟦secret:TOK#{N[:-1]}⟧")
    assert not pat.search(f"⟦secret:tok#{N}⟧")


@pytest.mark.parametrize("bad", [None, "", "ABCDEF0123456789", "0123", "0123456789abcdeg"])
def test_namespace_requires_valid_nonce(bad):
    with pytest.raises(ValueError):
        SecretsNamespace(bad)


def test_contexts_without_secrets_fail_as_template_error():
    # trigger_filter (and any ctx not built by a step render) has no `secrets`: TemplateError, not KeyError.
    with pytest.raises(TemplateError):
        eval_expr("secrets.TOK", {"case": {"title": "t"}})
    with pytest.raises(TemplateError):
        render("x {{ secrets.TOK }}", {"case": {"title": "t"}})
