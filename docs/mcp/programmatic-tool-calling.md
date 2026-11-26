# Programmatic tool calling with SOC Hub's MCP tools

[Programmatic tool calling](https://platform.claude.com/docs/en/agents-and-tools/tool-use/programmatic-tool-calling)
lets Claude write Python, in the code execution container, that calls your tools in a loop or in parallel and only
sends the final answer back to the model. That suits SOC Hub work such as "pull the last 200 dismissed alerts from
source X and group them by `payload.agent.name`" or "check every IOC on these 30 cases for a malicious verdict": many
calls, small result.

SOC Hub's MCP tools are ready for it:

- Every tool description ends with a `Returns JSON: {...}` paragraph naming the keys it returns, so Claude's code
  can `json.loads()` the result and index into it without guessing.
- Every result is one text block holding a JSON object (no images or documents, which programmatic results can't
  carry). Errors come back as a plain-text message with `isError` set; pass them through as the tool result.
- Input schemas are flat JSON Schema without `$ref`, so no tool hits the "Circular $ref detected" limit.
- `backend/tests/test_mcp_programmatic_calling.py` keeps all three true for new tools.

## Use them from your own agent, not through the MCP connector

Tools that come through Anthropic's **MCP connector** (`mcp_servers` in the Messages API) can't be called
programmatically. Instead, your application talks to SOC Hub's MCP server itself and offers its tools to Claude as
ordinary (client) tools with `allowed_callers`:

1. Connect an MCP client to `<PUBLIC_BASE_URL>/mcp` with an access token from the normal sign-in and consent (see
   [README.md](README.md#2-connect-a-client); any MCP client library with OAuth support can obtain one). The token
   acts as that person, with their live role, and every write shows "via MCP · <client name>".
2. `tools/list` and turn each SOC Hub tool into a Messages API tool definition.
3. Send them with the code execution tool. When Claude's code calls one (a `tool_use` block whose `caller` is the
   code execution run), call the same tool on SOC Hub and return the text as the `tool_result`.

```python
import anthropic

client = anthropic.Anthropic()
PTC = ["code_execution_20260120"]


def to_claude_tool(t):
    """An MCP tool as a Messages API tool. Read-only tools may be called from code; tools that change data stay
    direct-only, so each write is a visible step the user can follow (and you can confirm before running it)."""
    read_only = bool(t.annotations and t.annotations.readOnlyHint)
    return {"name": t.name, "description": t.description, "input_schema": t.inputSchema,
            "allowed_callers": PTC if read_only else ["direct"]}


async def run(mcp, question: str):
    """`mcp` is an initialized MCP client session to SOC Hub (list_tools / call_tool)."""
    tools = [{"type": "code_execution_20260120", "name": "code_execution"},
             *[to_claude_tool(t) for t in (await mcp.list_tools()).tools]]
    messages = [{"role": "user", "content": question}]
    container = None
    while True:
        resp = client.messages.create(model="claude-opus-5-5", max_tokens=8192, tools=tools,
                                      messages=messages, **({"container": container} if container else {}))
        container = resp.container.id if resp.container else container  # required while code waits on a result
        messages.append({"role": "assistant", "content": resp.content})
        if resp.stop_reason != "tool_use":
            return resp
        results = []
        for block in resp.content:
            if block.type != "tool_use":
                continue
            r = await mcp.call_tool(block.name, block.input)
            text = "".join(c.text for c in r.content if c.type == "text")  # always one JSON text block
            results.append({"type": "tool_result", "tool_use_id": block.id, "content": text,
                            **({"is_error": True} if r.isError else {})})
        messages.append({"role": "user", "content": results})  # only tool_result blocks, no text
```

Claude's code then sees each allowed tool as an async function taking one dict of arguments and returning the
result text:

```python
import asyncio, json
alerts = []
for offset in (0, 100):  # list tools return at most 100 rows per call; page with offset
    alerts += json.loads(await list_alerts({"status": "dismissed", "source": "wazuh", "limit": 100,
                                            "offset": offset}))["alerts"]
cases = await asyncio.gather(*(get_case({"case_id": i}) for i in case_ids))
```

## Rules that come from the API

- Answer a pending programmatic call with a user message holding **only** `tool_result` blocks, and pass the
  `container` id back. A pending call times out after about 4 minutes.
- Don't set `strict: true` on these tools, and don't set `disable_parallel_tool_use`: neither works with
  programmatic calling. `tool_choice` can't force a code-only tool.
- `allowed_callers` guides Claude but is not a security boundary: handle a direct call to any tool you list. SOC Hub
  enforces the person's role on every call either way.
- Results carry untrusted data (alert payloads, titles, notes, enrichment output). Claude's code may parse them, but
  nothing in them should be run as code or followed as instructions (the MCP server's instructions say the same).
- Programmatic calls count toward rate limits one by one, and code execution containers are not ZDR-eligible:
  results pass through a container kept for up to 30 days. Decide whether that fits the data you let it read.
