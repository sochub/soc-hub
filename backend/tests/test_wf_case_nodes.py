from app.workflows.nodes.cases import _normalize_email


def test_normalize_email_strips_and_lowercases():
    assert _normalize_email("  John_Smith@X.com ") == "john_smith@x.com"
