"""MCP tools stay usable from Anthropic programmatic tool calling (docs/mcp/programmatic-tool-calling.md): Claude's
code calls each tool and json.loads() its result, so every tool must say what JSON it returns, take a schema without
$ref cycles, and answer with one text block of JSON (never images or documents)."""
import json

from app.mcp import tools
from tests.test_mcp_server import Mcp, _go, _setup


def test_tools_describe_json_results_and_have_flat_schemas():
    async def body(c, ids):
        _, _, _, token = await _setup(ids, role="admin")
        s = await Mcp(c, token).init()
        listed = (await s.rpc("tools/list"))["result"]["tools"]
        assert {x["name"] for x in listed} == set(tools.MIN_ROLE)
        undocumented = [x["name"] for x in listed if "Returns JSON:" not in (x.get("description") or "")]
        assert undocumented == [], f"say what these return (a 'Returns JSON: {{...}}' paragraph): {undocumented}"
        with_refs = [x["name"] for x in listed if "$ref" in json.dumps(x["inputSchema"])]
        assert with_refs == [], f"$ref in input schemas (cycles break programmatic calling): {with_refs}"

        # A result is exactly one text block whose text is a JSON object.
        res = (await s.rpc("tools/call", {"name": "whoami", "arguments": {}}))["result"]
        assert not res.get("isError") and [b["type"] for b in res["content"]] == ["text"]
        assert isinstance(json.loads(res["content"][0]["text"]), dict)
    _go(body)
