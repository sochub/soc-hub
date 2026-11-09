from app.workflows.templates import load_templates
from app.workflows.validation import validate_graph, workflow_errors


def _tpl():
    return next(t for t in load_templates() if t["key"] == "email-to-alert")


def test_template_loads_and_validates():
    t = _tpl()
    assert t["trigger_type"] == "email.received"
    assert t["kind"] == "intake"
    assert validate_graph(t["graph"], "email.received", secret_names=set()) == []
    assert any(n["type"] == "alert_create" for n in t["graph"]["nodes"])


def test_filter_accepted():
    t = _tpl()
    assert workflow_errors(t["graph"], "email.received", t["trigger_filter"], secret_names=set()) == []
    assert workflow_errors(t["graph"], "email.received", "'soc+phishing' in email.to_joined", secret_names=set()) == []
