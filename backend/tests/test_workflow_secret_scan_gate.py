"""Old L4: enabling requires no plaintext credentials (bundled templates stay enableable); MCP workflow reads are
analyst+."""
from app.mcp import tools
from app.secrets.scan import plaintext_errors
from app.workflows.templates import load_templates


def test_bundled_templates_have_no_plaintext_credentials():
    flagged = {t["key"]: plaintext_errors(t["graph"]) for t in load_templates() if plaintext_errors(t["graph"])}
    assert flagged == {}


def test_plaintext_errors_never_echo_the_literal():
    g = {"nodes": [{"id": "h", "type": "http_request",
                    "config": {"url": "https://api.example.com/x?api_key=SECRET123", "headers": {"X-API-Key": "SECRET456"}}}]}
    errs = plaintext_errors(g)
    assert len(errs) == 2 and all(e["node_id"] == "h" for e in errs)
    assert "SECRET" not in str(errs)


def test_mcp_workflow_reads_need_analyst():
    for name in ("list_workflows", "get_workflow", "get_workflow_run", "validate_workflow"):
        assert tools.MIN_ROLE[name] == "analyst", name
