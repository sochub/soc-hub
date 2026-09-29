# Workflow Automation + Slack Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Tenants build DAG workflows (triggered by case/alert events or manually) that act on cases/alerts, call HTTP endpoints, loop over lists, message Slack and wait for an end user's button answer — with a side-effect-free dry-run mode.

**Architecture:** Workflow graphs are JSON (`{nodes, edges}`) stored per tenant. A run snapshots the graph; Celery tasks (`advance_run`, `execute_step`) walk it using a pure readiness function (`ready_nodes`) with run/step state in Postgres. Node types are small async executors. Slack is a bring-your-own app per tenant; its interaction callbacks are signature-verified and resume waiting steps.

**Tech Stack:** FastAPI + SQLAlchemy async + Alembic + Celery/Redis (existing); new `jinja2` (sandboxed templating); `httpx` (existing) for HTTP + Slack Web API; `cryptography` Fernet (existing, via python-jose extra); React 19 + TanStack Query + new `@xyflow/react`.

**Spec:** `docs/superpowers/specs/2026-09-29-workflows-slack-design.md` — read it before starting any task.

## Global Constraints

- All data rows carry `tenant_id`; cross-tenant access returns **404** (never 403).
- Graph limit: **≤ 50 nodes** (including loop bodies). Node ids match `[a-z0-9_]+`.
- Loop guard: events with **depth > 3** are dropped and logged.
- `http_request`: **15 s** timeout; `retries` default **2**, backoff **10 s, 60 s**, only on network errors and 5xx; redirects not followed; response body capped at **1 MB**.
- SSRF: block loopback / private (RFC1918, ULA) / link-local (incl. `169.254.169.254`) / unspecified / multicast / reserved unless hostname in the tenant allowlist; only `http`/`https`.
- `slack_ask_user`: `timeout_hours` default **24**, max **168**; expiry sweep every **60 s**.
- `for_each`: `concurrency` default **5**, max **20**; `max_items` default **100**, hard cap **500**; no nested `for_each`.
- `case_search`: max **50** results. `alert_promote` group `window_hours` default **24**.
- Slack signature: `v0=` + HMAC-SHA256(signing_secret, `v0:{ts}:{raw_body}`), constant-time compare, reject timestamps older than **5 min**.
- Permissions: admins create/edit/enable/delete/dry-run workflows and configure Slack + allowlist; analysts+ view, run manual workflows, cancel runs; viewers read-only. Every run start is audit-logged.
- Dry runs never write to cases/alerts, never call Slack/HTTP (unless `execute_in_dry_run` on `http_request`), never emit events.
- UI: light "Telemetry Console" theme — zinc-50 canvas, white bordered panels, sharp corners, `accent-*` blue, `.num` / `.label-mono` mono classes.
- ⚠️ Live dev DB: test cleanup deletes **only the row IDs the test created** — never table- or tenant-wide DELETEs.
- ⚠️ macOS Docker: after backend changes run `docker restart case_management-backend-1 case_management-worker-1`; after frontend changes run `docker compose up -d --build frontend`.
- Backend tests run inside the container: `docker compose exec backend python -m pytest tests/<file> -v` (from repo root).

## Review Focus

1. **Template references a path that doesn't exist** (e.g. `{{ steps.lookup.output.user.email }}` when `lookup` was skipped or returned no `user`) → the step fails with an error naming the missing attribute; the worker doesn't crash and the run is marked failed. *Test: Task 2 `test_missing_path_error_names_attribute`.*
2. **Trigger filter / condition hits `None`** (e.g. `alert.payload.severity >= 7` when the payload has no severity) → the filter evaluates as "don't run" (logged), and a condition node fails with a clear message rather than an unhandled `TypeError`. *Test: Task 2 `test_comparison_with_none_raises_template_error`, Task 7 `test_filter_error_means_no_match`.*
3. **A workflow re-triggers itself** (on `case.updated` it adds a tag, which fires `case.updated` again) → the chain stops at depth 3. *Test: Task 7 `test_depth_cap`.*
4. **`for_each` items aren't a list** (a string, a dict, `None`) → the step fails with "items must evaluate to a list"; it never iterates over a string's characters. *Test: Task 17 `test_items_must_be_list`.*
5. **Slack button double-clicked, clicked after timeout, or clicked by someone else** → only the first valid click from the target user resumes the run; later clicks get an ephemeral "already answered / expired / not for you". *Test: Task 25 `test_authorize_answer_*`.*

---

## File Structure

```
backend/
  requirements.txt                          # + jinja2
  alembic/versions/a1w2f3l4o5w6_workflows.py      # Task 6
  alembic/versions/b1s2l3a4c5k6_slack.py          # Task 21
  app/utils/crypto.py                       # Fernet encrypt/decrypt (Task 1)
  app/workflows/__init__.py
  app/workflows/templating.py               # sandboxed render/eval (Task 2)
  app/workflows/ssrf.py                     # URL guard (Task 3)
  app/workflows/node_types.py               # node registry: required keys, raw keys, flags (Task 4)
  app/workflows/validation.py               # validate_graph (Task 4, extended Task 17)
  app/workflows/graph.py                    # ready_nodes, run_outcome, body_graph (Task 5, 17)
  app/workflows/dryrun.py                   # dry_run_behaviour, simulated_output (Task 5, 26)
  app/workflows/loops.py                    # next_children_to_start, aggregate_children (Task 17)
  app/workflows/events.py                   # should_emit, trigger_matches, emit_event (Task 7)
  app/workflows/context.py                  # build_context, case/alert dicts (Task 8)
  app/workflows/runtime.py                  # create_run, advance_run, execute_step, cancel_run (Task 8)
  app/workflows/nodes/__init__.py           # EXECUTORS registry, NodeContext, NodeError, WAIT (Task 8)
  app/workflows/nodes/logic.py              # condition (Task 8), for_each (Task 18)
  app/workflows/nodes/http.py               # http_request (Task 8)
  app/workflows/nodes/cases.py              # case_* nodes + case_search (Task 9)
  app/workflows/nodes/alerts.py             # alert_promote, alert_dismiss (Task 10)
  app/workflows/nodes/slack.py              # slack_post_message (Task 23), slack_ask_user (Task 24)
  app/services/case_service.py              # extracted case create/update/note/artifact (Task 6)
  app/services/slack_service.py             # Slack Web API + signature + blocks (Task 21)
  app/models/workflow.py                    # Workflow, WorkflowRun, WorkflowRunStep (Task 6)
  app/models/slack_integration.py           # SlackIntegration (Task 21)
  app/schemas/workflow.py                   # API schemas (Task 11)
  app/api/v1/workflows.py                   # workflows + runs API (Task 11)
  app/api/v1/slack.py                       # slack config + interactions (Task 22, 23)
  app/services/slack_actions.py             # interaction handling: case buttons, answers (Task 23, 25)
  app/tasks/workflows.py                    # Celery tasks (Task 7, 8, 18, 23, 26)
  tests/test_wf_crypto.py, test_wf_templating.py, test_wf_ssrf.py, test_wf_validation.py,
  tests/test_wf_graph.py, test_wf_dryrun.py, test_wf_events.py, test_wf_loops.py,
  tests/test_wf_alert_group.py (DB), test_slack_signature.py, test_slack_answers.py
frontend/src/
  features/automations/types.ts             # TS types (Task 12)
  features/automations/nodeCatalog.ts       # node palette + config field schema (Task 12)
  features/automations/flow.ts              # graph <-> React Flow conversion (Task 13)
  features/automations/AutomationsList.tsx  # list + runs tabs (Task 12)
  features/automations/WorkflowEditor.tsx   # canvas editor (Task 13)
  features/automations/WorkflowNode.tsx     # custom node renderer (Task 13)
  features/automations/NodeInspector.tsx    # config form (Task 13)
  features/automations/DryRunDialog.tsx     # (Task 14)
  features/automations/RunsTable.tsx        # (Task 14)
  features/automations/RunDetail.tsx        # (Task 14, 20)
  features/automations/CaseAutomation.tsx   # CaseDetail run button + runs (Task 15)
  features/integrations/SlackCard.tsx       # (Task 22)
docs/slack/slack-app-manifest.yml, docs/slack/setup.md, docs/workflows/README.md
```

---

# Phase 1 — Engine, alert automation, dry-run, builder

### Task 1: Dependencies + secret encryption helper

**Files:**
- Modify: `backend/requirements.txt`
- Create: `backend/app/utils/crypto.py`
- Test: `backend/tests/test_wf_crypto.py`

**Interfaces:**
- Produces: `encrypt(plaintext: str) -> str`, `decrypt(token: str) -> str` (used by Task 21).

- [ ] **Step 1: Add jinja2 and rebuild images**

Append to `backend/requirements.txt`:
```
jinja2
```
Run (repo root): `docker compose build backend worker && docker compose up -d backend worker`
Expected: build succeeds; `docker compose exec backend python -c "import jinja2, cryptography; print('ok')"` prints `ok`.

- [ ] **Step 2: Write the failing test**

`backend/tests/test_wf_crypto.py`:
```python
from app.utils.crypto import encrypt, decrypt


def test_roundtrip():
    token = encrypt("xoxb-secret")
    assert token != "xoxb-secret"
    assert decrypt(token) == "xoxb-secret"


def test_ciphertexts_differ_per_call():
    assert encrypt("a") != encrypt("a")
```

- [ ] **Step 3: Run it — expect FAIL** (`ModuleNotFoundError: app.utils.crypto`)

Run: `docker compose exec backend python -m pytest tests/test_wf_crypto.py -v`

- [ ] **Step 4: Implement**

`backend/app/utils/crypto.py`:
```python
"""Symmetric encryption for secrets stored at rest (e.g. Slack tokens).

The Fernet key is derived from SECRET_KEY, so rotating SECRET_KEY makes
previously stored secrets unreadable — admins must re-enter them.
"""
import base64
import hashlib

from cryptography.fernet import Fernet

from app.core.config import settings


def _fernet() -> Fernet:
    key = base64.urlsafe_b64encode(hashlib.sha256(settings.SECRET_KEY.encode()).digest())
    return Fernet(key)


def encrypt(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt(token: str) -> str:
    return _fernet().decrypt(token.encode()).decode()
```

- [ ] **Step 5: Run — expect PASS.**

- [ ] **Step 6: Commit**
```bash
git add backend/requirements.txt backend/app/utils/crypto.py backend/tests/test_wf_crypto.py
git commit -m "feat(workflows): add jinja2 dep and Fernet secret helper"
```

---

### Task 2: Sandboxed templating

**Files:**
- Create: `backend/app/workflows/__init__.py` (empty), `backend/app/workflows/templating.py`
- Test: `backend/tests/test_wf_templating.py`

**Interfaces:**
- Produces:
  - `class TemplateError(Exception)`
  - `render(value: Any, ctx: dict) -> Any` — recursively renders str/dict/list. A string that is exactly one `{{ expr }}` returns the expression's **native** value (list/int/dict), otherwise a string.
  - `eval_expr(expr: str, ctx: dict) -> Any`
  - `syntax_errors(value: Any, raw: bool = False) -> list[str]` — `raw=True` treats strings as bare expressions.

- [ ] **Step 1: Write failing tests**

`backend/tests/test_wf_templating.py`:
```python
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
```

- [ ] **Step 2: Run — expect FAIL** (module missing).

Run: `docker compose exec backend python -m pytest tests/test_wf_templating.py -v`

- [ ] **Step 3: Implement**

`backend/app/workflows/templating.py`:
```python
"""Jinja2 sandboxed templating for workflow node configs and expressions."""
import re
from typing import Any, List

from jinja2 import StrictUndefined, Undefined
from jinja2.sandbox import SandboxedEnvironment

_env = SandboxedEnvironment(undefined=StrictUndefined, autoescape=False)
_SINGLE_EXPR = re.compile(r"^\s*\{\{(?P<expr>(?:(?!\{\{|\}\}).)+)\}\}\s*$", re.S)


class TemplateError(Exception):
    pass


def _force(value: Any) -> Any:
    # A sandbox-blocked or missing attribute comes back as an Undefined; make it raise.
    if isinstance(value, Undefined):
        value._fail_with_undefined_error()
    return value


def eval_expr(expr: str, ctx: dict) -> Any:
    try:
        return _force(_env.compile_expression(expr, undefined_to_none=False)(**ctx))
    except TemplateError:
        raise
    except Exception as e:  # jinja errors, SecurityError, TypeError on None comparisons, ...
        raise TemplateError(f"{type(e).__name__}: {e}") from e


def _render_str(s: str, ctx: dict) -> Any:
    if "{{" not in s and "{%" not in s:
        return s
    m = _SINGLE_EXPR.match(s)
    if m:
        return eval_expr(m.group("expr"), ctx)
    try:
        return _env.from_string(s).render(**ctx)
    except Exception as e:
        raise TemplateError(f"{type(e).__name__}: {e}") from e


def render(value: Any, ctx: dict) -> Any:
    if isinstance(value, str):
        return _render_str(value, ctx)
    if isinstance(value, dict):
        return {k: render(v, ctx) for k, v in value.items()}
    if isinstance(value, list):
        return [render(v, ctx) for v in value]
    return value


def syntax_errors(value: Any, raw: bool = False) -> List[str]:
    errors: List[str] = []
    if isinstance(value, str):
        try:
            if raw:
                _env.compile_expression(value)
            else:
                _env.parse(value)
        except Exception as e:
            errors.append(str(e))
    elif isinstance(value, dict):
        for v in value.values():
            errors += syntax_errors(v, raw)
    elif isinstance(value, list):
        for v in value:
            errors += syntax_errors(v, raw)
    return errors
```

- [ ] **Step 4: Run — expect PASS.** If `test_sandbox_blocks_dunder_escapes` fails for one case, print the returned value; the fix belongs in `_force` (every sandbox-blocked access must end in `TemplateError`), not in the test.

- [ ] **Step 5: Commit**
```bash
git add backend/app/workflows/__init__.py backend/app/workflows/templating.py backend/tests/test_wf_templating.py
git commit -m "feat(workflows): sandboxed jinja templating"
```

---

### Task 3: SSRF guard

**Files:**
- Create: `backend/app/workflows/ssrf.py`
- Test: `backend/tests/test_wf_ssrf.py`

**Interfaces:**
- Produces: `class SSRFError(ValueError)`; `assert_url_allowed(url: str, allowlist: list[str], resolve=socket.getaddrinfo) -> None`.

- [ ] **Step 1: Write failing tests**

`backend/tests/test_wf_ssrf.py`:
```python
import socket
import pytest

from app.workflows.ssrf import assert_url_allowed, SSRFError


def fake_resolver(mapping):
    def resolve(host, port, proto=0):
        if host not in mapping:
            raise socket.gaierror("nx")
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port)) for ip in mapping[host]]
    return resolve


R = fake_resolver({
    "api.example.com": ["93.184.216.34"],
    "sneaky.example.com": ["10.0.0.5"],
    "internal.corp": ["10.1.2.3"],
    "127.0.0.1": ["127.0.0.1"],
    "10.0.0.1": ["10.0.0.1"],
    "169.254.169.254": ["169.254.169.254"],
    "::1": ["::1"],
})


def test_public_host_allowed():
    assert_url_allowed("https://api.example.com/v1", [], resolve=R)


@pytest.mark.parametrize("url", [
    "http://127.0.0.1:5432/",
    "http://10.0.0.1/",
    "http://169.254.169.254/latest/meta-data/",
    "http://[::1]/",
    "http://sneaky.example.com/",   # public name, private IP
])
def test_private_targets_blocked(url):
    with pytest.raises(SSRFError):
        assert_url_allowed(url, [], resolve=R)


def test_allowlisted_host_passes():
    assert_url_allowed("http://internal.corp/hook", ["INTERNAL.corp"], resolve=R)


@pytest.mark.parametrize("url", ["file:///etc/passwd", "gopher://x", "ftp://api.example.com", "http:///nohost"])
def test_bad_scheme_or_host_blocked(url):
    with pytest.raises(SSRFError):
        assert_url_allowed(url, [], resolve=R)


def test_unresolvable_blocked():
    with pytest.raises(SSRFError):
        assert_url_allowed("https://nope.invalid/", [], resolve=R)
```

- [ ] **Step 2: Run — expect FAIL.** `docker compose exec backend python -m pytest tests/test_wf_ssrf.py -v`

- [ ] **Step 3: Implement**

`backend/app/workflows/ssrf.py`:
```python
"""Outbound URL guard for the http_request node."""
import ipaddress
import socket
from typing import List
from urllib.parse import urlparse


class SSRFError(ValueError):
    pass


def _blocked(ip: ipaddress._BaseAddress) -> bool:
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_unspecified
            or ip.is_multicast or ip.is_reserved)


def assert_url_allowed(url: str, allowlist: List[str], resolve=socket.getaddrinfo) -> None:
    # ponytail: checks resolved IPs, then httpx resolves again (DNS-rebinding TOCTOU window).
    # Upgrade path: pin the vetted IP via a custom httpx transport if this ever matters.
    p = urlparse(url)
    if p.scheme not in ("http", "https"):
        raise SSRFError(f"scheme '{p.scheme}' not allowed")
    host = p.hostname
    if not host:
        raise SSRFError("URL has no host")
    if host.lower() in {h.lower() for h in allowlist}:
        return
    port = p.port or (443 if p.scheme == "https" else 80)
    try:
        infos = resolve(host, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror as e:
        raise SSRFError(f"cannot resolve {host}") from e
    for info in infos:
        ip = ipaddress.ip_address(info[4][0].split("%")[0])
        if _blocked(ip):
            raise SSRFError(f"{host} resolves to a non-public address ({ip}); add it to the HTTP allowlist to permit it")
```

- [ ] **Step 4: Run — expect PASS.**

- [ ] **Step 5: Commit**
```bash
git add backend/app/workflows/ssrf.py backend/tests/test_wf_ssrf.py
git commit -m "feat(workflows): SSRF guard for outbound HTTP"
```

---

### Task 4: Node registry + graph validation

**Files:**
- Create: `backend/app/workflows/node_types.py`, `backend/app/workflows/validation.py`
- Test: `backend/tests/test_wf_validation.py`

**Interfaces:**
- Produces:
  - `NODE_TYPES: dict[str, dict]` — per type: `required: list[str]`, `raw: list[str]` (keys holding bare expressions, not templates), `alert_only: bool`.
  - `TRIGGER_TYPES = ("case.created", "case.updated", "alert.ingested", "manual")`
  - `validate_graph(graph: dict, trigger_type: str) -> list[dict]` — each error `{"node_id": str | None, "message": str}`; empty list = valid.

Graph JSON contract (used everywhere):
```json
{"nodes": [{"id": "start", "type": "trigger", "position": {"x": 0, "y": 0}, "config": {},
            "parent_id": null, "continue_on_error": false, "retries": 2,
            "execute_in_dry_run": false, "mock_output": null}],
 "edges": [{"id": "e1", "source": "start", "target": "check", "source_handle": null}]}
```

- [ ] **Step 1: Write failing tests**

`backend/tests/test_wf_validation.py`:
```python
from app.workflows.validation import validate_graph


def n(id, type, **kw):
    return {"id": id, "type": type, "position": {"x": 0, "y": 0}, "config": kw.pop("config", {}), **kw}


def e(src, tgt, handle=None):
    return {"id": f"{src}-{tgt}-{handle}", "source": src, "target": tgt, "source_handle": handle}


def msgs(errors):
    return " | ".join(x["message"] for x in errors)


VALID = {
    "nodes": [
        n("start", "trigger"),
        n("check", "condition", config={"expression": "'x' in case.tags"}),
        n("note", "case_add_note", config={"content": "Tag X on {{ case.title }}"}),
    ],
    "edges": [e("start", "check"), e("check", "note", "true")],
}


def test_valid_graph():
    assert validate_graph(VALID, "case.created") == []


def test_unknown_trigger_type():
    assert "trigger type" in msgs(validate_graph(VALID, "case.deleted"))


def test_requires_exactly_one_trigger():
    g = {"nodes": [n("a", "case_add_note", config={"content": "x"})], "edges": []}
    assert "exactly one trigger" in msgs(validate_graph(g, "manual"))
    g2 = {"nodes": [n("a", "trigger"), n("b", "trigger")], "edges": []}
    assert "exactly one trigger" in msgs(validate_graph(g2, "manual"))


def test_cycle_detected():
    g = {"nodes": [n("start", "trigger"), n("a", "case_add_note", config={"content": "x"}),
                   n("b", "case_add_note", config={"content": "y"})],
         "edges": [e("start", "a"), e("a", "b"), e("b", "a")]}
    assert "cycle" in msgs(validate_graph(g, "manual"))


def test_bad_ids_duplicates_unknown_type_missing_config():
    g = {"nodes": [n("start", "trigger"), n("Bad-Id", "case_add_note", config={"content": "x"}),
                   n("dup", "case_add_note", config={"content": "x"}), n("dup", "case_add_note", config={"content": "x"}),
                   n("zz", "teleport"), n("nocontent", "case_add_note")],
         "edges": []}
    m = msgs(validate_graph(g, "manual"))
    assert "invalid id" in m and "duplicate" in m and "unknown node type" in m and "content" in m


def test_condition_handles():
    g = {"nodes": [n("start", "trigger"), n("c", "condition", config={"expression": "true"}),
                   n("a", "case_add_note", config={"content": "x"})],
         "edges": [e("start", "c"), e("c", "a")]}  # missing handle on condition edge
    assert "true/false" in msgs(validate_graph(g, "manual"))
    g["edges"] = [e("start", "c", "true"), e("c", "a", "true")]  # handle on non-condition source
    assert "only condition" in msgs(validate_graph(g, "manual"))


def test_dangling_edge():
    g = {"nodes": [n("start", "trigger")], "edges": [e("start", "ghost")]}
    assert "unknown node" in msgs(validate_graph(g, "manual"))


def test_template_syntax_error():
    g = {"nodes": [n("start", "trigger"), n("a", "case_add_note", config={"content": "{{ case.title "})],
         "edges": [e("start", "a")]}
    assert "template" in msgs(validate_graph(g, "manual"))


def test_expression_syntax_error():
    g = {"nodes": [n("start", "trigger"), n("c", "condition", config={"expression": "case.id =="})],
         "edges": [e("start", "c")]}
    assert "template" in msgs(validate_graph(g, "manual"))


def test_node_limit():
    nodes = [n("start", "trigger")] + [n(f"n{i}", "case_add_note", config={"content": "x"}) for i in range(50)]
    assert "50" in msgs(validate_graph({"nodes": nodes, "edges": []}, "manual"))


def test_alert_nodes_only_on_alert_trigger():
    g = {"nodes": [n("start", "trigger"), n("p", "alert_promote", config={"mode": "new", "title": "x"})],
         "edges": [e("start", "p")]}
    assert "alert.ingested" in msgs(validate_graph(g, "case.created"))
    assert validate_graph(g, "alert.ingested") == []


def test_alert_promote_mode_requirements():
    g = {"nodes": [n("start", "trigger"), n("p", "alert_promote", config={"mode": "group", "title": "x"})],
         "edges": [e("start", "p")]}
    assert "group_key" in msgs(validate_graph(g, "alert.ingested"))
```

- [ ] **Step 2: Run — expect FAIL.** `docker compose exec backend python -m pytest tests/test_wf_validation.py -v`

- [ ] **Step 3: Implement the registry**

`backend/app/workflows/node_types.py`:
```python
"""Registry of workflow node types. Later phases add for_each and slack_* entries."""

TRIGGER_TYPES = ("case.created", "case.updated", "alert.ingested", "manual")

NODE_TYPES = {
    "trigger":             {"required": [], "raw": [], "alert_only": False},
    "condition":           {"required": ["expression"], "raw": ["expression"], "alert_only": False},
    "http_request":        {"required": ["method", "url"], "raw": [], "alert_only": False},
    "case_update":         {"required": [], "raw": [], "alert_only": False},
    "case_add_note":       {"required": ["content"], "raw": [], "alert_only": False},
    "case_add_artifact":   {"required": ["artifact_type", "value"], "raw": [], "alert_only": False},
    "case_apply_playbook": {"required": ["template_id"], "raw": [], "alert_only": False},
    "case_search":         {"required": [], "raw": [], "alert_only": False},
    "alert_promote":       {"required": ["mode"], "raw": [], "alert_only": True},
    "alert_dismiss":       {"required": [], "raw": [], "alert_only": True},
}

MAX_NODES = 50
```

- [ ] **Step 4: Implement validation**

`backend/app/workflows/validation.py`:
```python
"""Pure save-time validation of a workflow graph."""
import re
from typing import Dict, List, Optional

from app.workflows.node_types import NODE_TYPES, TRIGGER_TYPES, MAX_NODES
from app.workflows.templating import syntax_errors

_ID_RE = re.compile(r"^[a-z0-9_]+$")


def _err(errors: List[dict], node_id: Optional[str], message: str) -> None:
    errors.append({"node_id": node_id, "message": message})


def _has_cycle(node_ids: List[str], edges: List[dict]) -> bool:
    adj: Dict[str, List[str]] = {nid: [] for nid in node_ids}
    for e in edges:
        if e["source"] in adj and e["target"] in adj:
            adj[e["source"]].append(e["target"])
    WHITE, GREY, BLACK = 0, 1, 2
    color = {nid: WHITE for nid in node_ids}

    def visit(u: str) -> bool:
        color[u] = GREY
        for v in adj[u]:
            if color[v] == GREY or (color[v] == WHITE and visit(v)):
                return True
        color[u] = BLACK
        return False

    return any(color[nid] == WHITE and visit(nid) for nid in node_ids)


def _check_node_config(errors: List[dict], node: dict, trigger_type: str) -> None:
    nid, spec, cfg = node["id"], NODE_TYPES[node["type"]], node.get("config") or {}
    for key in spec["required"]:
        if cfg.get(key) in (None, "", []):
            _err(errors, nid, f"missing required field '{key}'")
    if spec["alert_only"] and trigger_type != "alert.ingested":
        _err(errors, nid, f"{node['type']} requires trigger type alert.ingested")
    if node["type"] == "alert_promote":
        mode = cfg.get("mode")
        if mode not in ("new", "link", "group"):
            _err(errors, nid, "mode must be new, link or group")
        if mode in ("new", "group") and not cfg.get("title"):
            _err(errors, nid, "missing required field 'title'")
        if mode == "link" and not cfg.get("case_id"):
            _err(errors, nid, "missing required field 'case_id'")
        if mode == "group" and not cfg.get("group_key"):
            _err(errors, nid, "missing required field 'group_key'")
    for key, value in cfg.items():
        for msg in syntax_errors(value, raw=key in spec["raw"]):
            _err(errors, nid, f"template error in '{key}': {msg}")


def validate_graph(graph: dict, trigger_type: str) -> List[dict]:
    errors: List[dict] = []
    nodes = graph.get("nodes") or []
    edges = graph.get("edges") or []

    if trigger_type not in TRIGGER_TYPES:
        _err(errors, None, f"unknown trigger type '{trigger_type}'")
    if len(nodes) > MAX_NODES:
        _err(errors, None, f"too many nodes ({len(nodes)} > {MAX_NODES})")

    seen = set()
    for node in nodes:
        nid = node.get("id", "")
        if not _ID_RE.match(nid):
            _err(errors, nid, f"invalid id '{nid}' (use a-z, 0-9, _)")
        if nid in seen:
            _err(errors, nid, f"duplicate node id '{nid}'")
        seen.add(nid)
        if node.get("type") not in NODE_TYPES:
            _err(errors, nid, f"unknown node type '{node.get('type')}'")
            continue
        _check_node_config(errors, node, trigger_type)

    triggers = [n for n in nodes if n.get("type") == "trigger"]
    if len(triggers) != 1:
        _err(errors, None, "workflow needs exactly one trigger node")

    by_id = {n.get("id"): n for n in nodes}
    for e in edges:
        src, tgt = by_id.get(e.get("source")), by_id.get(e.get("target"))
        if not src or not tgt:
            _err(errors, None, f"edge {e.get('id')} references an unknown node")
            continue
        handle = e.get("source_handle")
        if src.get("type") == "condition":
            if handle not in ("true", "false"):
                _err(errors, src["id"], "condition edges must leave from the true/false handles")
        elif handle is not None:
            _err(errors, src["id"], "only condition nodes have true/false handles")

    if _has_cycle(list(by_id.keys()), edges):
        _err(errors, None, "graph contains a cycle")
    return errors
```

- [ ] **Step 5: Run — expect PASS.**

- [ ] **Step 6: Commit**
```bash
git add backend/app/workflows/node_types.py backend/app/workflows/validation.py backend/tests/test_wf_validation.py
git commit -m "feat(workflows): node registry and graph validation"
```

---

### Task 5: Pure graph readiness + dry-run behaviour

**Files:**
- Create: `backend/app/workflows/graph.py`, `backend/app/workflows/dryrun.py`
- Test: `backend/tests/test_wf_graph.py`, `backend/tests/test_wf_dryrun.py`

**Interfaces:**
- Produces:
  - `ready_nodes(graph: dict, states: dict[str, dict]) -> tuple[set[str], set[str]]` — `states` maps node_id → `{"status": str, "output": dict | None}`. Returns `(ready, skipped)` for nodes **without** a state yet; skipped includes transitive skips. Ignores nodes with `parent_id`.
  - `run_outcome(graph: dict, states: dict) -> str | None` — `"failed"` if any step failed; `"succeeded"` if every top-level node is `succeeded`/`skipped`; otherwise `None` (still going).
  - `dry_run_behaviour(node: dict) -> "execute" | "simulate"`
  - `simulated_output(node: dict, rendered_input: dict, dry_run_mocks: dict) -> dict` — `dry_run_mocks = {"mocks": {node_id: dict}, "ask_user_answers": {node_id: str}}`.

Step statuses: `pending | running | succeeded | failed | skipped | waiting | cancelled`. `continue_on_error` failures are stored as `succeeded` with `output.error`, so `failed` always means "fail the run".

- [ ] **Step 1: Write failing graph tests**

`backend/tests/test_wf_graph.py`:
```python
from app.workflows.graph import ready_nodes, run_outcome


def n(id, type="case_add_note", parent_id=None):
    return {"id": id, "type": type, "config": {}, "parent_id": parent_id}


def e(s, t, h=None):
    return {"id": f"{s}{t}", "source": s, "target": t, "source_handle": h}


OK = {"status": "succeeded", "output": {}}


def test_linear_chain():
    g = {"nodes": [n("t", "trigger"), n("a"), n("b")], "edges": [e("t", "a"), e("a", "b")]}
    assert ready_nodes(g, {"t": OK}) == ({"a"}, set())
    assert ready_nodes(g, {"t": OK, "a": {"status": "running"}}) == (set(), set())
    assert ready_nodes(g, {"t": OK, "a": OK}) == ({"b"}, set())


def test_diamond_join_waits_for_both():
    g = {"nodes": [n("t", "trigger"), n("a"), n("b"), n("j")],
         "edges": [e("t", "a"), e("t", "b"), e("a", "j"), e("b", "j")]}
    assert ready_nodes(g, {"t": OK}) == ({"a", "b"}, set())
    assert ready_nodes(g, {"t": OK, "a": OK, "b": {"status": "running"}}) == (set(), set())
    assert ready_nodes(g, {"t": OK, "a": OK, "b": OK}) == ({"j"}, set())


def cond_graph():
    return {"nodes": [n("t", "trigger"), n("c", "condition"), n("yes"), n("no"), n("yes2"), n("merge")],
            "edges": [e("t", "c"), e("c", "yes", "true"), e("c", "no", "false"),
                      e("yes", "yes2"), e("yes2", "merge"), e("no", "merge")]}


def test_condition_true_skips_false_branch():
    ready, skipped = ready_nodes(cond_graph(), {"t": OK, "c": {"status": "succeeded", "output": {"result": True}}})
    assert ready == {"yes"} and skipped == {"no"}


def test_merge_after_condition_runs_once_taken_branch_done():
    states = {"t": OK, "c": {"status": "succeeded", "output": {"result": True}},
              "no": {"status": "skipped"}, "yes": OK, "yes2": OK}
    assert ready_nodes(cond_graph(), states) == ({"merge"}, set())


def test_skip_cascades_through_chain():
    ready, skipped = ready_nodes(cond_graph(), {"t": OK, "c": {"status": "succeeded", "output": {"result": False}}})
    assert ready == {"no"} and skipped == {"yes", "yes2"}


def test_all_parents_skipped_skips_join():
    g = {"nodes": [n("t", "trigger"), n("c", "condition"), n("a"), n("b"), n("j")],
         "edges": [e("t", "c"), e("c", "a", "true"), e("c", "b", "true"), e("a", "j"), e("b", "j")]}
    ready, skipped = ready_nodes(g, {"t": OK, "c": {"status": "succeeded", "output": {"result": False}}})
    assert ready == set() and skipped == {"a", "b", "j"}


def test_continue_on_error_counts_as_success():
    g = {"nodes": [n("t", "trigger"), n("a"), n("b")], "edges": [e("t", "a"), e("a", "b")]}
    states = {"t": OK, "a": {"status": "succeeded", "output": {"error": "boom"}}}
    assert ready_nodes(g, states) == ({"b"}, set())


def test_body_nodes_ignored_at_top_level():
    g = {"nodes": [n("t", "trigger"), n("loop", "for_each"), n("inner", parent_id="loop")],
         "edges": [e("t", "loop")]}
    assert ready_nodes(g, {"t": OK}) == ({"loop"}, set())


def test_run_outcome():
    g = {"nodes": [n("t", "trigger"), n("a"), n("b")], "edges": [e("t", "a"), e("a", "b")]}
    assert run_outcome(g, {"t": OK, "a": OK}) is None
    assert run_outcome(g, {"t": OK, "a": OK, "b": {"status": "skipped"}}) == "succeeded"
    assert run_outcome(g, {"t": OK, "a": {"status": "failed"}}) == "failed"
```

- [ ] **Step 2: Write failing dry-run tests**

`backend/tests/test_wf_dryrun.py`:
```python
from app.workflows.dryrun import dry_run_behaviour, simulated_output

NO_MOCKS = {"mocks": {}, "ask_user_answers": {}}


def test_read_only_nodes_execute():
    for t in ("trigger", "condition", "for_each", "case_search"):
        assert dry_run_behaviour({"id": "x", "type": t}) == "execute"


def test_side_effect_nodes_simulate():
    for t in ("http_request", "case_update", "case_add_note", "case_add_artifact",
              "case_apply_playbook", "alert_promote", "alert_dismiss",
              "slack_post_message", "slack_ask_user"):
        assert dry_run_behaviour({"id": "x", "type": t}) == "simulate"


def test_execute_in_dry_run_only_honoured_on_http():
    assert dry_run_behaviour({"id": "x", "type": "http_request", "execute_in_dry_run": True}) == "execute"
    assert dry_run_behaviour({"id": "x", "type": "case_update", "execute_in_dry_run": True}) == "simulate"


def test_mock_precedence():
    node = {"id": "h", "type": "http_request", "mock_output": {"status": 404}}
    out = simulated_output(node, {"url": "u"}, {"mocks": {"h": {"status": 201}}, "ask_user_answers": {}})
    assert out["status"] == 201 and out["simulated"] is True and out["would_do"] == {"url": "u"}
    assert simulated_output(node, {}, NO_MOCKS)["status"] == 404
    bare = {"id": "h", "type": "http_request"}
    assert simulated_output(bare, {}, NO_MOCKS)["status"] == 200


def test_alert_promote_stub():
    out = simulated_output({"id": "p", "type": "alert_promote"}, {"mode": "new"}, NO_MOCKS)
    assert out["case_id"] is None and out["created"] is True
```

- [ ] **Step 3: Run both — expect FAIL.**

Run: `docker compose exec backend python -m pytest tests/test_wf_graph.py tests/test_wf_dryrun.py -v`

- [ ] **Step 4: Implement `graph.py`**

`backend/app/workflows/graph.py`:
```python
"""Pure DAG readiness logic — the heart of the engine; keep it DB-free."""
from typing import Dict, Optional, Set, Tuple

_RESOLVED = {"succeeded", "skipped"}


def _top_level(graph: dict) -> Dict[str, dict]:
    return {n["id"]: n for n in graph["nodes"] if not n.get("parent_id")}


def _edge_active(edge: dict, source: dict, state: dict) -> bool:
    if state["status"] != "succeeded":
        return False
    if source["type"] == "condition":
        result = bool((state.get("output") or {}).get("result"))
        return edge.get("source_handle") == ("true" if result else "false")
    return True


def ready_nodes(graph: dict, states: Dict[str, dict]) -> Tuple[Set[str], Set[str]]:
    nodes = _top_level(graph)
    incoming: Dict[str, list] = {nid: [] for nid in nodes}
    for e in graph["edges"]:
        if e["source"] in nodes and e["target"] in nodes:
            incoming[e["target"]].append(e)

    states = dict(states)
    ready: Set[str] = set()
    skipped: Set[str] = set()
    changed = True
    while changed:  # repeat so skips cascade down chains
        changed = False
        for nid in nodes:
            if nid in states or nid in ready or not incoming[nid]:
                continue
            parent_states = [states.get(e["source"]) for e in incoming[nid]]
            if any(s is None or s["status"] not in _RESOLVED for s in parent_states):
                continue
            if any(_edge_active(e, nodes[e["source"]], s) for e, s in zip(incoming[nid], parent_states)):
                ready.add(nid)
            else:
                skipped.add(nid)
                states[nid] = {"status": "skipped", "output": None}
                changed = True
    return ready, skipped


def run_outcome(graph: dict, states: Dict[str, dict]) -> Optional[str]:
    if any(s["status"] == "failed" for s in states.values()):
        return "failed"
    nodes = _top_level(graph)
    if all(states.get(nid, {}).get("status") in _RESOLVED for nid in nodes):
        return "succeeded"
    return None
```

- [ ] **Step 5: Implement `dryrun.py`**

`backend/app/workflows/dryrun.py`:
```python
"""Pure dry-run decisions: which nodes really execute and what simulated ones return."""

_READ_ONLY = {"trigger", "condition", "for_each", "case_search"}

_STUBS = {
    "http_request": {"status": 200, "headers": {}, "body": {}},
    "alert_promote": {"case_id": None, "created": True},
}


def dry_run_behaviour(node: dict) -> str:
    if node["type"] in _READ_ONLY:
        return "execute"
    if node["type"] == "http_request" and node.get("execute_in_dry_run"):
        return "execute"
    return "simulate"


def simulated_output(node: dict, rendered_input: dict, dry_run_mocks: dict) -> dict:
    mocks = (dry_run_mocks or {}).get("mocks") or {}
    if node["id"] in mocks:
        base = mocks[node["id"]]
    elif node.get("mock_output") is not None:
        base = node["mock_output"]
    else:
        base = _STUBS.get(node["type"], {})
    return {**base, "simulated": True, "would_do": rendered_input}
```

- [ ] **Step 6: Run both — expect PASS.**

- [ ] **Step 7: Commit**
```bash
git add backend/app/workflows/graph.py backend/app/workflows/dryrun.py backend/tests/test_wf_graph.py backend/tests/test_wf_dryrun.py
git commit -m "feat(workflows): pure DAG readiness and dry-run behaviour"
```

---

### Task 6: Models, migration, and case-service extraction

**Files:**
- Create: `backend/app/models/workflow.py`, `backend/alembic/versions/a1w2f3l4o5w6_workflows.py`, `backend/app/services/case_service.py`
- Modify: `backend/app/models/tenant.py`, `backend/app/models/case.py` (Case.group_key, Alert.dismiss_reason), `backend/app/db/base.py`, `backend/app/api/v1/cases.py` (create_case, update_case, create_timeline_event use the service), `backend/app/api/v1/artifacts.py` (create_artifact uses the service), `backend/app/schemas/case.py` (Alert schema gets `dismiss_reason`, `case_id`)

**Interfaces:**
- Produces (models): `Workflow`, `WorkflowRun`, `WorkflowRunStep` with the columns in the spec's Data model.
- Produces (`app/services/case_service.py`), all flush-only (caller commits):
  - `async create_case_record(db, *, tenant_id: int, data: dict, user_id: int | None) -> tuple[Case, int]` — returns `(case, triage_id)`; caller must `run_case_triage_task.delay(triage_id)` **after** commit.
  - `async apply_case_update(db, *, case: Case, update_data: dict, user_id: int | None) -> dict` — returns `changes` (`{field: {"from","to"}}`); writes timeline events, resolved/acknowledged timestamps, audit log.
  - `async add_timeline_note(db, *, case: Case, content: str, user_id: int | None, event_type: str = "comment") -> TimelineEvent`
  - `async add_artifact_to_case(db, *, case: Case, artifact_type: ArtifactType, value: str, description: str | None, user_id: int | None, isolated: bool = False) -> Artifact`

- [ ] **Step 1: Add model columns**

In `backend/app/models/tenant.py` add import `JSON` and column:
```python
    workflow_http_allowlist = Column(JSON, nullable=False, default=list, server_default="[]")
```
In `backend/app/models/case.py`, in `Case` (after `source`):
```python
    # Set by the alert_promote workflow node in "group" mode; dedupes alert storms.
    group_key = Column(String, nullable=True, index=True)
```
In `Alert` (after `status`):
```python
    dismiss_reason = Column(Text, nullable=True)
```

- [ ] **Step 2: Create workflow models**

`backend/app/models/workflow.py`:
```python
from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.sql import func

from app.db.base_class import Base


class Workflow(Base):
    __tablename__ = "workflows"

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    enabled = Column(Boolean, nullable=False, default=False)
    trigger_type = Column(String, nullable=False)
    trigger_filter = Column(Text, nullable=True)
    graph = Column(JSON, nullable=False)
    version = Column(Integer, nullable=False, default=1)
    created_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())


class WorkflowRun(Base):
    __tablename__ = "workflow_runs"

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    workflow_id = Column(Integer, ForeignKey("workflows.id", ondelete="CASCADE"), nullable=False, index=True)
    workflow_version = Column(Integer, nullable=False)
    case_id = Column(Integer, ForeignKey("cases.id", ondelete="SET NULL"), nullable=True, index=True)
    alert_id = Column(Integer, ForeignKey("alerts.id", ondelete="SET NULL"), nullable=True, index=True)
    # queued (loop children only) | running | waiting | succeeded | failed | cancelled
    status = Column(String, nullable=False, default="running", index=True)
    trigger_payload = Column(JSON, nullable=True)
    graph_snapshot = Column(JSON, nullable=False)
    depth = Column(Integer, nullable=False, default=0)
    is_dry_run = Column(Boolean, nullable=False, default=False)
    dry_run_mocks = Column(JSON, nullable=True)
    parent_run_id = Column(Integer, ForeignKey("workflow_runs.id", ondelete="CASCADE"), nullable=True, index=True)
    parent_step_id = Column(Integer, ForeignKey("workflow_run_steps.id", ondelete="CASCADE", use_alter=True), nullable=True, index=True)
    loop_index = Column(Integer, nullable=True)
    loop_item = Column(JSON, nullable=True)
    error = Column(Text, nullable=True)
    started_at = Column(DateTime(timezone=True), server_default=func.now())
    finished_at = Column(DateTime(timezone=True), nullable=True)


class WorkflowRunStep(Base):
    __tablename__ = "workflow_run_steps"
    __table_args__ = (UniqueConstraint("run_id", "node_id", name="uq_run_step_node"),)

    id = Column(Integer, primary_key=True, index=True)
    run_id = Column(Integer, ForeignKey("workflow_runs.id", ondelete="CASCADE"), nullable=False, index=True)
    node_id = Column(String, nullable=False)
    status = Column(String, nullable=False, default="pending")
    input = Column(JSON, nullable=True)
    output = Column(JSON, nullable=True)
    error = Column(Text, nullable=True)
    attempt = Column(Integer, nullable=False, default=0)
    wait_token = Column(String, unique=True, nullable=True)
    wait_expires_at = Column(DateTime(timezone=True), nullable=True, index=True)
    started_at = Column(DateTime(timezone=True), nullable=True)
    finished_at = Column(DateTime(timezone=True), nullable=True)
```
Register in `backend/app/db/base.py`:
```python
from app.models.workflow import Workflow, WorkflowRun, WorkflowRunStep  # noqa: F401
```

- [ ] **Step 3: Write the migration**

First confirm the head: `docker compose exec backend alembic heads` — expected `e5f6a7b8c9d0 (head)`. If different, use the printed head as `down_revision`.

`backend/alembic/versions/a1w2f3l4o5w6_workflows.py`:
```python
"""workflows, runs, steps; case.group_key; alert.dismiss_reason; tenant http allowlist

Revision ID: a1w2f3l4o5w6
Revises: e5f6a7b8c9d0
"""
import sqlalchemy as sa
from alembic import op

revision = "a1w2f3l4o5w6"
down_revision = "e5f6a7b8c9d0"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("tenants", sa.Column("workflow_http_allowlist", sa.JSON(), nullable=False, server_default="[]"))
    op.add_column("cases", sa.Column("group_key", sa.String(), nullable=True))
    op.create_index("ix_cases_tenant_group_key", "cases", ["tenant_id", "group_key"])
    op.add_column("alerts", sa.Column("dismiss_reason", sa.Text(), nullable=True))

    op.create_table(
        "workflows",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("trigger_type", sa.String(), nullable=False),
        sa.Column("trigger_filter", sa.Text()),
        sa.Column("graph", sa.JSON(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_workflows_tenant_id", "workflows", ["tenant_id"])

    op.create_table(
        "workflow_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("workflow_id", sa.Integer(), sa.ForeignKey("workflows.id", ondelete="CASCADE"), nullable=False),
        sa.Column("workflow_version", sa.Integer(), nullable=False),
        sa.Column("case_id", sa.Integer(), sa.ForeignKey("cases.id", ondelete="SET NULL")),
        sa.Column("alert_id", sa.Integer(), sa.ForeignKey("alerts.id", ondelete="SET NULL")),
        sa.Column("status", sa.String(), nullable=False, server_default="running"),
        sa.Column("trigger_payload", sa.JSON()),
        sa.Column("graph_snapshot", sa.JSON(), nullable=False),
        sa.Column("depth", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_dry_run", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("dry_run_mocks", sa.JSON()),
        sa.Column("parent_run_id", sa.Integer(), sa.ForeignKey("workflow_runs.id", ondelete="CASCADE")),
        sa.Column("parent_step_id", sa.Integer()),
        sa.Column("loop_index", sa.Integer()),
        sa.Column("loop_item", sa.JSON()),
        sa.Column("error", sa.Text()),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
    )
    for col in ("tenant_id", "workflow_id", "case_id", "alert_id", "status", "parent_run_id", "parent_step_id"):
        op.create_index(f"ix_workflow_runs_{col}", "workflow_runs", [col])

    op.create_table(
        "workflow_run_steps",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("run_id", sa.Integer(), sa.ForeignKey("workflow_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("node_id", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="pending"),
        sa.Column("input", sa.JSON()),
        sa.Column("output", sa.JSON()),
        sa.Column("error", sa.Text()),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("wait_token", sa.String(), unique=True),
        sa.Column("wait_expires_at", sa.DateTime(timezone=True)),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("run_id", "node_id", name="uq_run_step_node"),
    )
    op.create_index("ix_workflow_run_steps_run_id", "workflow_run_steps", ["run_id"])
    op.create_index("ix_workflow_run_steps_wait_expires_at", "workflow_run_steps", ["wait_expires_at"])
    op.create_foreign_key("fk_workflow_runs_parent_step", "workflow_runs", "workflow_run_steps",
                          ["parent_step_id"], ["id"], ondelete="CASCADE")


def downgrade():
    op.drop_constraint("fk_workflow_runs_parent_step", "workflow_runs", type_="foreignkey")
    op.drop_table("workflow_run_steps")
    op.drop_table("workflow_runs")
    op.drop_table("workflows")
    op.drop_column("alerts", "dismiss_reason")
    op.drop_index("ix_cases_tenant_group_key", table_name="cases")
    op.drop_column("cases", "group_key")
    op.drop_column("tenants", "workflow_http_allowlist")
```

Run: `docker restart case_management-backend-1 && docker compose exec backend alembic upgrade head`
Expected: `Running upgrade e5f6a7b8c9d0 -> a1w2f3l4o5w6`. Then `docker compose exec backend alembic downgrade -1 && docker compose exec backend alembic upgrade head` — both succeed.

- [ ] **Step 4: Create the case service** (moves logic verbatim out of `cases.py`/`artifacts.py`)

`backend/app/services/case_service.py`:
```python
"""Case mutations shared by the HTTP API, workflow nodes and Slack buttons.

All functions only flush; the caller commits (and emits workflow events after commit).
"""
from datetime import datetime, timezone
from typing import Optional, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.artifact import Artifact, ArtifactType
from app.models.case import Case, CaseStatus, TimelineEvent
from app.models.case_artifact import CaseArtifact
from app.models.case_triage import CaseTriageResult
from app.utils.audit import create_audit_log


async def create_case_record(db: AsyncSession, *, tenant_id: int, data: dict, user_id: Optional[int]) -> Tuple[Case, int]:
    case = Case(**data, tenant_id=tenant_id)
    if not case.owner_id and user_id:
        case.owner_id = user_id
    db.add(case)
    await db.flush()
    await create_audit_log(db=db, entity_type="case", entity_id=case.id, action="create",
                           tenant_id=tenant_id, user_id=user_id)
    triage = CaseTriageResult(case_id=case.id, tenant_id=tenant_id, triggered_by="auto_on_create", status="pending")
    db.add(triage)
    await db.flush()
    return case, triage.id


async def apply_case_update(db: AsyncSession, *, case: Case, update_data: dict, user_id: Optional[int]) -> dict:
    old_status = case.status
    changes = {}
    for field, value in update_data.items():
        old_value = getattr(case, field)
        if old_value != value:
            changes[field] = {"from": str(old_value) if old_value is not None else None,
                              "to": str(value) if value is not None else None}
        setattr(case, field, value)

    if "status" in changes:
        db.add(TimelineEvent(case_id=case.id, user_id=user_id, event_type="status_change",
                             content=f"Status changed from {changes['status']['from']} to {changes['status']['to']}"))
        new_status = update_data["status"]
        if new_status in (CaseStatus.RESOLVED, CaseStatus.CLOSED):
            if case.resolved_at is None:
                case.resolved_at = datetime.now(timezone.utc)
        else:
            case.resolved_at = None
        if old_status == CaseStatus.NEW and case.acknowledged_at is None:
            case.acknowledged_at = datetime.now(timezone.utc)

    if "severity" in changes:
        db.add(TimelineEvent(case_id=case.id, user_id=user_id, event_type="severity_change",
                             content=f"Severity changed from {changes['severity']['from']} to {changes['severity']['to']}"))

    if changes:
        await create_audit_log(db=db, entity_type="case", entity_id=case.id, action="update",
                               tenant_id=case.tenant_id, user_id=user_id, changes=changes)
    await db.flush()
    return changes


async def add_timeline_note(db: AsyncSession, *, case: Case, content: str, user_id: Optional[int],
                            event_type: str = "comment") -> TimelineEvent:
    ev = TimelineEvent(case_id=case.id, user_id=user_id, event_type=event_type, content=content)
    db.add(ev)
    await db.flush()
    return ev


async def add_artifact_to_case(db: AsyncSession, *, case: Case, artifact_type: ArtifactType, value: str,
                               description: Optional[str], user_id: Optional[int], isolated: bool = False) -> Artifact:
    artifact = None
    if not isolated:
        artifact = (await db.execute(select(Artifact).where(
            Artifact.value == value, Artifact.artifact_type == artifact_type,
            Artifact.tenant_id == case.tenant_id, Artifact.isolated == False,  # noqa: E712
        ))).scalars().first()
    if artifact is None:
        artifact = Artifact(artifact_type=artifact_type, value=value, description=description,
                            isolated=isolated, tenant_id=case.tenant_id, created_by=user_id)
        db.add(artifact)
        await db.flush()

    linked = (await db.execute(select(CaseArtifact).where(
        CaseArtifact.case_id == case.id, CaseArtifact.artifact_id == artifact.id))).scalars().first()
    if not linked:
        db.add(CaseArtifact(case_id=case.id, artifact_id=artifact.id, added_by=user_id))
        db.add(TimelineEvent(case_id=case.id, user_id=user_id, event_type="artifact_added",
                             content=f"Added artifact: {value} ({artifact_type})"))
    await db.flush()
    return artifact
```

- [ ] **Step 5: Point the endpoints at the service**

In `backend/app/api/v1/cases.py`, `create_case` body becomes (keep signature and the re-load at the end):
```python
    case, triage_id = await create_case_record(db, tenant_id=tenant_id, data=case_in.model_dump(), user_id=current_user.id)
    await db.commit()
    run_case_triage_task.delay(triage_id)
```
`update_case`: replace everything from `old_status = case.status` through the `create_audit_log(...)` block with:
```python
    changes = await apply_case_update(db, case=case, update_data=case_in.model_dump(exclude_unset=True), user_id=current_user.id)
```
`create_timeline_event`: use `add_timeline_note(db, case=case, content=..., user_id=current_user.id, event_type=<existing value>)` if its body is a plain insert; otherwise leave it.
In `backend/app/api/v1/artifacts.py` `create_artifact`: after the case lookup, replace the find/create/junction/timeline block with:
```python
    artifact = await add_artifact_to_case(db, case=case, artifact_type=artifact_in.artifact_type, value=artifact_in.value,
                                          description=artifact_in.description, user_id=current_user.id,
                                          isolated=artifact_in.isolated)
```
Add imports (`from app.services.case_service import ...`), and remove imports that become unused.

In `backend/app/schemas/case.py` `AlertBase` (or the `Alert` response schema), add `case_id: Optional[int] = None` and `dismiss_reason: Optional[str] = None` if missing.

- [ ] **Step 6: Verify nothing regressed**

Run: `docker restart case_management-backend-1 case_management-worker-1 && docker compose exec backend python -m pytest tests -v`
Expected: all existing + new tests PASS.
Manual: in the UI create a case, change its status to In Progress and severity to High, add a comment and an artifact → timeline shows the status/severity/artifact events exactly as before; the triage panel still populates.

- [ ] **Step 7: Commit**
```bash
git add backend/app/models backend/app/db/base.py backend/alembic/versions/a1w2f3l4o5w6_workflows.py \
        backend/app/services/case_service.py backend/app/api/v1/cases.py backend/app/api/v1/artifacts.py backend/app/schemas/case.py
git commit -m "feat(workflows): models + migration; extract case mutations into case_service"
```

---

### Task 7: Events — trigger matching, depth cap, dispatch

**Files:**
- Create: `backend/app/workflows/events.py`, `backend/app/tasks/workflows.py`
- Modify: `backend/app/worker.py` (import tasks), `backend/app/api/v1/cases.py`, `backend/app/api/v1/alerts.py`
- Test: `backend/tests/test_wf_events.py`

**Interfaces:**
- Consumes: `eval_expr`, `TemplateError` (Task 2).
- Produces:
  - `MAX_DEPTH = 3`; `should_emit(depth: int) -> bool`
  - `trigger_matches(trigger_filter: str | None, ctx: dict) -> bool` — empty filter → True; any `TemplateError` → False (logged).
  - `emit_event(tenant_id: int, event_type: str, *, case_id: int | None = None, alert_id: int | None = None, changes: dict | None = None, depth: int = 0) -> None` — enqueues `dispatch_event_task`.
  - Celery task `dispatch_event_task(tenant_id, event_type, case_id, alert_id, changes, depth)` (body completed in Task 8 once `create_run` exists).

- [ ] **Step 1: Write failing tests**

`backend/tests/test_wf_events.py`:
```python
from app.workflows.events import should_emit, trigger_matches, MAX_DEPTH

CTX = {"case": {"tags": ["x"], "severity": "high"}, "alert": None,
       "trigger": {"changes": {"tags": {"from": "[]", "to": "['x']"}}}}


def test_depth_cap():
    assert MAX_DEPTH == 3
    assert should_emit(0) and should_emit(3)
    assert not should_emit(4)


def test_empty_filter_matches():
    assert trigger_matches(None, CTX) and trigger_matches("  ", CTX)


def test_filter_evaluates():
    assert trigger_matches("'x' in case.tags", CTX)
    assert not trigger_matches("case.severity == 'low'", CTX)


def test_filter_error_means_no_match():
    assert not trigger_matches("alert.payload.severity >= 7", CTX)  # alert is None
```

- [ ] **Step 2: Run — expect FAIL.** `docker compose exec backend python -m pytest tests/test_wf_events.py -v`

- [ ] **Step 3: Implement `events.py`**

`backend/app/workflows/events.py`:
```python
"""Workflow event emission + trigger matching."""
import logging
from typing import Optional

from app.workflows.templating import eval_expr, TemplateError

logger = logging.getLogger(__name__)

MAX_DEPTH = 3


def should_emit(depth: int) -> bool:
    return depth <= MAX_DEPTH


def trigger_matches(trigger_filter: Optional[str], ctx: dict) -> bool:
    if not trigger_filter or not trigger_filter.strip():
        return True
    try:
        return bool(eval_expr(trigger_filter, ctx))
    except TemplateError as e:
        logger.warning("workflow trigger_filter error (treated as no match): %s", e)
        return False


def emit_event(tenant_id: int, event_type: str, *, case_id: Optional[int] = None, alert_id: Optional[int] = None,
               changes: Optional[dict] = None, depth: int = 0) -> None:
    """Call only AFTER the originating change is committed."""
    if not should_emit(depth):
        logger.warning("dropping %s event for tenant %s at depth %s (loop guard)", event_type, tenant_id, depth)
        return
    from app.tasks.workflows import dispatch_event_task  # late import: avoids worker import cycle
    dispatch_event_task.delay(tenant_id, event_type, case_id, alert_id, changes or {}, depth)
```

- [ ] **Step 4: Create the task module skeleton and register it**

`backend/app/tasks/workflows.py`:
```python
import asyncio
import logging

from app.worker import celery_app

logger = logging.getLogger(__name__)


def _run_async(coro):
    """Run a coroutine in a fresh loop, then dispose pooled connections bound to it
    (same reason as app/tasks/triage.py)."""
    from app.db.session import engine

    async def wrapper():
        try:
            return await coro
        finally:
            await engine.dispose()
    return asyncio.run(wrapper())


@celery_app.task(acks_late=True)
def dispatch_event_task(tenant_id, event_type, case_id, alert_id, changes, depth):
    from app.workflows.runtime import dispatch_event
    _run_async(dispatch_event(tenant_id, event_type, case_id, alert_id, changes, depth))
```
In `backend/app/worker.py` add next to the other task imports:
```python
import app.tasks.workflows  # noqa: E402,F401
```

- [ ] **Step 5: Emit from the API**

`backend/app/api/v1/cases.py` — `create_case`, after `run_case_triage_task.delay(triage_id)`:
```python
    emit_event(tenant_id, "case.created", case_id=case.id)
```
`update_case`, after `await db.commit()`:
```python
    if changes:
        emit_event(tenant_id, "case.updated", case_id=case.id, changes=changes)
```
`backend/app/api/v1/alerts.py` — `ingest_alert`, after `await db.refresh(alert)`:
```python
    emit_event(webhook.tenant_id, "alert.ingested", alert_id=alert.id)
```
Import: `from app.workflows.events import emit_event`.

- [ ] **Step 6: Run tests — expect PASS** (the runtime import is lazy, so nothing breaks before Task 8).

- [ ] **Step 7: Commit**
```bash
git add backend/app/workflows/events.py backend/app/tasks/workflows.py backend/app/worker.py \
        backend/app/api/v1/cases.py backend/app/api/v1/alerts.py backend/tests/test_wf_events.py
git commit -m "feat(workflows): event emission with trigger filter and depth cap"
```

---

### Task 8: Runtime — context, create/advance/execute/cancel, condition + http nodes

**Files:**
- Create: `backend/app/workflows/context.py`, `backend/app/workflows/runtime.py`, `backend/app/workflows/nodes/__init__.py`, `backend/app/workflows/nodes/logic.py`, `backend/app/workflows/nodes/http.py`
- Modify: `backend/app/tasks/workflows.py`

**Interfaces:**
- Consumes: `ready_nodes`, `run_outcome` (Task 5), `render`, `eval_expr` (Task 2), `dry_run_behaviour`, `simulated_output` (Task 5), `assert_url_allowed` (Task 3), `trigger_matches` (Task 7), models (Task 6).
- Produces:
  - `nodes/__init__.py`: `class NodeError(Exception)`, `class RetryableNodeError(NodeError)`, `WAIT` sentinel object, `@dataclass NodeContext(db, run, step, node, ctx, after_commit: list)`, `EXECUTORS: dict[str, Callable[[NodeContext, dict], Awaitable[dict | WAIT]]]`, decorator `executor(type_name)`, helper `async load_target_case(nctx, config) -> Case`.
  - `context.py`: `case_to_dict(db, case) -> dict`, `alert_to_dict(alert) -> dict`, `async build_context(db, run) -> dict`.
  - `runtime.py`: `async create_run(db, workflow, *, trigger_payload, case_id=None, alert_id=None, depth=0, is_dry_run=False, dry_run_mocks=None) -> WorkflowRun` (flush only), `async dispatch_event(...)`, `async advance_run(run_id)`, `async execute_step(step_id)`, `async cancel_run(db, run) -> None` (flush only), `async finalize_run(db, run, status, error=None)`.
  - Celery: `advance_run_task(run_id)`, `execute_step_task(step_id)`.

Design notes for the implementer:
- `advance_run` locks the run row with `SELECT ... FOR UPDATE` so two finishing branches can't double-schedule; step rows are also unique on `(run_id, node_id)`.
- `execute_step` runs the node in **its own transaction after** marking the step `running`, and after the final commit runs every callable in `nctx.after_commit` (event emission, triage kick-off, Slack follow-ups).
- Every mutation node appends `lambda: emit_event(..., depth=run.depth + 1)` to `after_commit`, never when `run.is_dry_run`.
- `RAW` config keys (`NODE_TYPES[type]["raw"]`) are not rendered; the node evaluates them itself.

- [ ] **Step 1: Node plumbing**

`backend/app/workflows/nodes/__init__.py`:
```python
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List

from fastapi import HTTPException
from sqlalchemy import select


class NodeError(Exception):
    pass


class RetryableNodeError(NodeError):
    pass


WAIT = object()  # returned by nodes that pause the step (slack_ask_user, for_each)


@dataclass
class NodeContext:
    db: Any
    run: Any
    step: Any
    node: dict
    ctx: dict
    after_commit: List[Callable[[], None]] = field(default_factory=list)


EXECUTORS: Dict[str, Callable[[NodeContext, dict], Awaitable[Any]]] = {}


def executor(type_name: str):
    def deco(fn):
        EXECUTORS[type_name] = fn
        return fn
    return deco


async def load_target_case(nctx: NodeContext, config: dict):
    from app.models.case import Case
    raw = config.get("case_id") or nctx.run.case_id
    if raw in (None, ""):
        raise NodeError("no case: this run has no case and no case_id was given")
    try:
        case_id = int(raw)
    except (TypeError, ValueError):
        raise NodeError(f"case_id must be an integer, got {raw!r}")
    case = (await nctx.db.execute(select(Case).where(Case.id == case_id, Case.tenant_id == nctx.run.tenant_id))).scalars().first()
    if not case:
        raise NodeError(f"case {case_id} not found")
    return case


def http_exc_to_node_error(e: HTTPException) -> NodeError:
    return NodeError(str(e.detail))


# register executors
from app.workflows.nodes import logic, http, cases, alerts  # noqa: E402,F401
```
(`cases`/`alerts` modules are created in Tasks 9–10. Until then create both as empty files so imports work.)

- [ ] **Step 2: Condition node**

`backend/app/workflows/nodes/logic.py`:
```python
from app.workflows.nodes import NodeContext, NodeError, executor
from app.workflows.templating import eval_expr, TemplateError


@executor("trigger")
async def run_trigger(nctx: NodeContext, config: dict) -> dict:
    return nctx.run.trigger_payload or {}


@executor("condition")
async def run_condition(nctx: NodeContext, config: dict) -> dict:
    try:
        return {"result": bool(eval_expr(config["expression"], nctx.ctx))}
    except TemplateError as e:
        raise NodeError(f"condition failed: {e}")
```

- [ ] **Step 3: HTTP node**

`backend/app/workflows/nodes/http.py`:
```python
import json

import httpx
from sqlalchemy import select

from app.models.tenant import Tenant
from app.workflows.nodes import NodeContext, NodeError, RetryableNodeError, executor
from app.workflows.ssrf import SSRFError, assert_url_allowed

MAX_BODY = 1_000_000


@executor("http_request")
async def run_http(nctx: NodeContext, config: dict) -> dict:
    tenant = (await nctx.db.execute(select(Tenant).where(Tenant.id == nctx.run.tenant_id))).scalars().first()
    url = str(config["url"])
    try:
        assert_url_allowed(url, tenant.workflow_http_allowlist or [])
    except SSRFError as e:
        raise NodeError(str(e))

    method = str(config.get("method", "GET")).upper()
    headers = {str(k): str(v) for k, v in (config.get("headers") or {}).items()}
    body = config.get("body")
    kwargs = {"json": body} if isinstance(body, (dict, list)) else ({"content": str(body)} if body not in (None, "") else {})
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=False) as client:
            resp = await client.request(method, url, headers=headers, **kwargs)
    except httpx.TransportError as e:
        raise RetryableNodeError(f"request failed: {e}")
    if resp.status_code >= 500:
        raise RetryableNodeError(f"HTTP {resp.status_code} from {url}")

    raw = resp.content[:MAX_BODY]
    try:
        parsed = json.loads(raw)
    except ValueError:
        parsed = raw.decode(errors="replace")
    # 4xx is returned (not raised) so workflows can branch on output.status.
    return {"status": resp.status_code, "headers": dict(resp.headers), "body": parsed}
```

- [ ] **Step 4: Context builder**

`backend/app/workflows/context.py`:
```python
from typing import Optional

from sqlalchemy import select

from app.models.artifact import Artifact
from app.models.case import Alert, Case
from app.models.case_artifact import CaseArtifact
from app.models.workflow import WorkflowRun, WorkflowRunStep


def _enum(v):
    return getattr(v, "value", v)


async def case_to_dict(db, case: Optional[Case]) -> Optional[dict]:
    if case is None:
        return None
    arts = (await db.execute(
        select(Artifact.artifact_type, Artifact.value)
        .join(CaseArtifact, CaseArtifact.artifact_id == Artifact.id)
        .where(CaseArtifact.case_id == case.id)
    )).all()
    return {
        "id": case.id, "title": case.title, "description": case.description,
        "status": _enum(case.status), "severity": _enum(case.severity),
        "tags": list(case.tags or []), "source": case.source, "owner_id": case.owner_id,
        "group_key": case.group_key,
        "created_at": case.created_at.isoformat() if case.created_at else None,
        "artifacts": [{"type": _enum(t), "value": v} for t, v in arts],
    }


def alert_to_dict(alert: Optional[Alert]) -> Optional[dict]:
    if alert is None:
        return None
    return {"id": alert.id, "source": alert.source, "external_id": alert.external_id,
            "title": alert.title, "payload": alert.payload, "status": alert.status, "case_id": alert.case_id}


async def _step_outputs(db, run_id: int) -> dict:
    rows = (await db.execute(select(WorkflowRunStep).where(WorkflowRunStep.run_id == run_id))).scalars().all()
    return {s.node_id: {"output": s.output, "status": s.status} for s in rows}


async def build_context(db, run: WorkflowRun) -> dict:
    case = None
    if run.case_id:
        case = (await db.execute(select(Case).where(Case.id == run.case_id, Case.tenant_id == run.tenant_id))).scalars().first()
    alert = None
    if run.alert_id:
        alert = (await db.execute(select(Alert).where(Alert.id == run.alert_id, Alert.tenant_id == run.tenant_id))).scalars().first()

    steps = {}
    if run.parent_run_id:  # loop child: parent's outputs are visible too
        steps.update(await _step_outputs(db, run.parent_run_id))
    steps.update(await _step_outputs(db, run.id))

    ctx = {"case": await case_to_dict(db, case), "alert": alert_to_dict(alert),
           "trigger": run.trigger_payload or {}, "steps": steps, "dry_run": run.is_dry_run}
    if run.parent_run_id:
        ctx["loop"] = {"item": run.loop_item, "index": run.loop_index}
    return ctx
```

- [ ] **Step 5: Runtime**

`backend/app/workflows/runtime.py`:
```python
"""Run lifecycle: create -> advance (schedule ready nodes) -> execute steps -> finalize."""
import copy
import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select

from app.db.session import AsyncSessionLocal
from app.models.workflow import Workflow, WorkflowRun, WorkflowRunStep
from app.utils.audit import create_audit_log
from app.workflows.context import build_context
from app.workflows.dryrun import dry_run_behaviour, simulated_output
from app.workflows.events import trigger_matches
from app.workflows.graph import ready_nodes, run_outcome
from app.workflows.node_types import NODE_TYPES
from app.workflows.nodes import EXECUTORS, NodeContext, NodeError, RetryableNodeError, WAIT
from app.workflows.templating import render, TemplateError

logger = logging.getLogger(__name__)

_ACTIVE_STEP = {"pending", "running", "waiting"}
_TERMINAL_RUN = {"succeeded", "failed", "cancelled"}
_BACKOFF = [10, 60]


def _now():
    return datetime.now(timezone.utc)


def _node(graph: dict, node_id: str) -> dict:
    return next(n for n in graph["nodes"] if n["id"] == node_id)


async def create_run(db, workflow: Workflow, *, trigger_payload: dict, case_id: Optional[int] = None,
                     alert_id: Optional[int] = None, depth: int = 0, is_dry_run: bool = False,
                     dry_run_mocks: Optional[dict] = None, user_id: Optional[int] = None) -> WorkflowRun:
    graph = copy.deepcopy(workflow.graph)
    run = WorkflowRun(tenant_id=workflow.tenant_id, workflow_id=workflow.id, workflow_version=workflow.version,
                      case_id=case_id, alert_id=alert_id, status="running", trigger_payload=trigger_payload,
                      graph_snapshot=graph, depth=depth, is_dry_run=is_dry_run, dry_run_mocks=dry_run_mocks)
    db.add(run)
    await db.flush()
    trigger = next(n for n in graph["nodes"] if n["type"] == "trigger" and not n.get("parent_id"))
    db.add(WorkflowRunStep(run_id=run.id, node_id=trigger["id"], status="succeeded", output=trigger_payload,
                           started_at=_now(), finished_at=_now()))
    await create_audit_log(db=db, entity_type="workflow_run", entity_id=run.id,
                           action="dry_run" if is_dry_run else "start", tenant_id=workflow.tenant_id,
                           user_id=user_id, changes={"workflow": workflow.name, "trigger": trigger_payload})
    await db.flush()
    return run


async def dispatch_event(tenant_id, event_type, case_id, alert_id, changes, depth) -> None:
    from app.tasks.workflows import advance_run_task
    from app.models.case import Alert, Case
    from app.workflows.context import alert_to_dict, case_to_dict

    async with AsyncSessionLocal() as db:
        workflows = (await db.execute(select(Workflow).where(
            Workflow.tenant_id == tenant_id, Workflow.enabled == True, Workflow.trigger_type == event_type,  # noqa: E712
        ))).scalars().all()
        if not workflows:
            return
        case = (await db.execute(select(Case).where(Case.id == case_id, Case.tenant_id == tenant_id))).scalars().first() if case_id else None
        alert = (await db.execute(select(Alert).where(Alert.id == alert_id, Alert.tenant_id == tenant_id))).scalars().first() if alert_id else None
        payload = {"event": event_type, "case_id": case_id, "alert_id": alert_id, "changes": changes}
        ctx = {"case": await case_to_dict(db, case), "alert": alert_to_dict(alert), "trigger": payload}

        run_ids = []
        for wf in workflows:
            if trigger_matches(wf.trigger_filter, ctx):
                run = await create_run(db, wf, trigger_payload=payload, case_id=case_id, alert_id=alert_id, depth=depth)
                run_ids.append(run.id)
        await db.commit()
    for rid in run_ids:
        advance_run_task.delay(rid)


async def finalize_run(db, run: WorkflowRun, status: str, error: Optional[str] = None) -> None:
    run.status = status
    run.error = error
    run.finished_at = _now()
    for s in (await db.execute(select(WorkflowRunStep).where(
            WorkflowRunStep.run_id == run.id, WorkflowRunStep.status.in_(_ACTIVE_STEP)))).scalars().all():
        s.status = "cancelled"
        s.finished_at = _now()
    await db.flush()


async def cancel_run(db, run: WorkflowRun) -> None:
    if run.status in _TERMINAL_RUN:
        return
    await finalize_run(db, run, "cancelled")
    children = (await db.execute(select(WorkflowRun).where(WorkflowRun.parent_run_id == run.id))).scalars().all()
    for child in children:
        await cancel_run(db, child)


async def advance_run(run_id: int) -> None:
    from app.tasks.workflows import execute_step_task

    to_execute = []
    finished_child = None
    async with AsyncSessionLocal() as db:
        run = (await db.execute(select(WorkflowRun).where(WorkflowRun.id == run_id).with_for_update())).scalars().first()
        if not run or run.status in _TERMINAL_RUN or run.status == "queued":
            return
        steps = (await db.execute(select(WorkflowRunStep).where(WorkflowRunStep.run_id == run.id))).scalars().all()
        states = {s.node_id: {"status": s.status, "output": s.output} for s in steps}

        failed = next((s for s in steps if s.status == "failed"), None)
        if failed:
            await finalize_run(db, run, "failed", f"step '{failed.node_id}' failed: {failed.error}")
        else:
            ready, skipped = ready_nodes(run.graph_snapshot, states)
            for nid in skipped:
                db.add(WorkflowRunStep(run_id=run.id, node_id=nid, status="skipped", finished_at=_now()))
                states[nid] = {"status": "skipped", "output": None}
            for nid in ready:
                step = WorkflowRunStep(run_id=run.id, node_id=nid, status="pending")
                db.add(step)
                to_execute.append(step)
                states[nid] = {"status": "pending", "output": None}
            await db.flush()

            outcome = run_outcome(run.graph_snapshot, states)
            if outcome:
                await finalize_run(db, run, outcome)
            elif any(s["status"] == "waiting" for s in states.values()) and \
                    not any(s["status"] in ("pending", "running") for s in states.values()):
                run.status = "waiting"
            else:
                run.status = "running"
        if run.status in _TERMINAL_RUN and run.parent_step_id:
            finished_child = run.parent_step_id
        await db.commit()
        step_ids = [s.id for s in to_execute]

    for sid in step_ids:
        execute_step_task.delay(sid)
    if finished_child:
        from app.tasks.workflows import loop_tick_task  # defined in Task 18
        loop_tick_task.delay(finished_child)


async def execute_step(step_id: int) -> None:
    from app.tasks.workflows import advance_run_task, execute_step_task

    async with AsyncSessionLocal() as db:
        step = (await db.execute(select(WorkflowRunStep).where(WorkflowRunStep.id == step_id).with_for_update())).scalars().first()
        if not step or step.status != "pending":
            return
        run = (await db.execute(select(WorkflowRun).where(WorkflowRun.id == step.run_id))).scalars().first()
        if run.status in _TERMINAL_RUN:
            return
        step.status = "running"
        step.started_at = step.started_at or _now()
        await db.commit()

        node = _node(run.graph_snapshot, step.node_id)
        nctx = NodeContext(db=db, run=run, step=step, node=node, ctx={})
        raw_keys = set(NODE_TYPES[node["type"]]["raw"])
        retry_in = None
        try:
            nctx.ctx = await build_context(db, run)
            cfg = node.get("config") or {}
            rendered = {k: (v if k in raw_keys else render(v, nctx.ctx)) for k, v in cfg.items()}
            step.input = rendered
            if run.is_dry_run and dry_run_behaviour(node) == "simulate":
                output = simulated_output(node, rendered, run.dry_run_mocks or {})
            else:
                output = await EXECUTORS[node["type"]](nctx, rendered)
            if output is WAIT:
                step.status = "waiting"
            else:
                step.status, step.output, step.finished_at = "succeeded", output, _now()
        except (NodeError, TemplateError) as e:
            await db.rollback()  # discard partial writes from the failed node
            step = (await db.execute(select(WorkflowRunStep).where(WorkflowRunStep.id == step_id))).scalars().first()
            nctx.after_commit.clear()
            retries = int(node.get("retries", 2 if node["type"] == "http_request" else 0))
            if isinstance(e, RetryableNodeError) and step.attempt < min(retries, len(_BACKOFF)):
                retry_in = _BACKOFF[step.attempt]
                step.attempt += 1
                step.status = "pending"
                step.error = f"attempt {step.attempt} failed: {e}"
            elif node.get("continue_on_error"):
                step.status, step.output, step.finished_at = "succeeded", {"error": str(e)}, _now()
            else:
                step.status, step.error, step.finished_at = "failed", str(e), _now()
        except Exception as e:  # bug in an executor — fail the step, keep the worker alive
            logger.exception("workflow step %s crashed", step_id)
            await db.rollback()
            step = (await db.execute(select(WorkflowRunStep).where(WorkflowRunStep.id == step_id))).scalars().first()
            nctx.after_commit.clear()
            step.status, step.error, step.finished_at = "failed", f"internal error: {e}", _now()
        await db.commit()

    for fn in nctx.after_commit:
        try:
            fn()
        except Exception:
            logger.exception("after_commit hook failed for step %s", step_id)
    if retry_in is not None:
        execute_step_task.apply_async((step_id,), countdown=retry_in)
    else:  # also for "waiting": advance_run flips the run to status=waiting
        advance_run_task.delay(step.run_id)
```

- [ ] **Step 6: Celery tasks**

Append to `backend/app/tasks/workflows.py`:
```python
@celery_app.task(acks_late=True)
def advance_run_task(run_id):
    from app.workflows.runtime import advance_run
    _run_async(advance_run(run_id))


@celery_app.task(acks_late=True)
def execute_step_task(step_id):
    from app.workflows.runtime import execute_step
    _run_async(execute_step(step_id))
```
Also add a temporary no-op so `advance_run`'s import resolves before Task 18 (Task 18 replaces the body):
```python
@celery_app.task(acks_late=True)
def loop_tick_task(parent_step_id):
    pass  # replaced in Task 18
```

- [ ] **Step 7: Smoke test end-to-end in the container**

Restart: `docker restart case_management-backend-1 case_management-worker-1`.
Create a scratch script at `/tmp/wf_smoke.py` **inside the container** (don't commit it):
```bash
docker compose exec backend sh -c 'cat > /tmp/wf_smoke.py' <<'EOF'
import asyncio
from sqlalchemy import select
from app.db.session import AsyncSessionLocal
from app.models.case import Case
from app.models.workflow import Workflow
from app.workflows.runtime import create_run
from app.tasks.workflows import advance_run_task

async def main():
    async with AsyncSessionLocal() as db:
        case = (await db.execute(select(Case).order_by(Case.id.desc()))).scalars().first()
        wf = Workflow(tenant_id=case.tenant_id, name="smoke", trigger_type="manual", enabled=False, version=1, graph={
            "nodes": [{"id": "start", "type": "trigger", "config": {}},
                      {"id": "c", "type": "condition", "config": {"expression": "case.id > 0"}},
                      {"id": "h", "type": "http_request", "config": {"method": "GET", "url": "https://httpbin.org/get?case={{ case.id }}"}}],
            "edges": [{"id": "1", "source": "start", "target": "c", "source_handle": None},
                      {"id": "2", "source": "c", "target": "h", "source_handle": "true"}]})
        db.add(wf); await db.flush()
        run = await create_run(db, wf, trigger_payload={"event": "manual", "case_id": case.id}, case_id=case.id)
        await db.commit()
        print("workflow", wf.id, "run", run.id)
    advance_run_task.delay(run.id)

asyncio.run(main())
EOF
docker compose exec backend python /tmp/wf_smoke.py
```
Then after ~5 s:
`docker compose exec db psql -U user -d sicms -c "select node_id,status,error,output->>'status' from workflow_run_steps where run_id=<RUN_ID> order by id"`
Expected: `start succeeded`, `c succeeded`, `h succeeded` with status `200`; `select status from workflow_runs where id=<RUN_ID>` → `succeeded`.
Cleanup (scoped): `delete from workflows where id=<WORKFLOW_ID>;` (runs/steps cascade).

- [ ] **Step 8: Run the unit suite — expect PASS.** `docker compose exec backend python -m pytest tests -v`

- [ ] **Step 9: Commit**
```bash
git add backend/app/workflows backend/app/tasks/workflows.py
git commit -m "feat(workflows): runtime engine with condition and http_request nodes"
```

---

### Task 9: Case nodes + case_search

**Files:**
- Modify: `backend/app/workflows/nodes/cases.py` (was empty)

**Interfaces:**
- Consumes: `apply_case_update`, `add_timeline_note`, `add_artifact_to_case` (Task 6), `apply_playbook_to_case` (existing `app/utils/playbooks.py`), `load_target_case`, `NodeContext`, `NodeError` (Task 8), `emit_event` (Task 7).
- Produces executors: `case_update`, `case_add_note`, `case_add_artifact`, `case_apply_playbook`, `case_search`.

Config reference:
- `case_update`: `severity?`, `status?`, `owner_email?`, `add_tags?` (list or comma string), `remove_tags?`, `case_id?`
- `case_add_note`: `content`, `case_id?`
- `case_add_artifact`: `artifact_type` (`ip|domain|url|file_hash|email|other`), `value`, `description?`, `case_id?`
- `case_apply_playbook`: `template_id`, `case_id?`
- `case_search`: `status?` (list/comma), `tags_any?`, `title_contains?`, `artifact_value?`, `created_within_hours?`

- [ ] **Step 1: Implement**

`backend/app/workflows/nodes/cases.py`:
```python
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import select

from app.models.artifact import Artifact, ArtifactType
from app.models.case import Case, CaseSeverity, CaseStatus
from app.models.case_artifact import CaseArtifact
from app.models.membership import TenantMembership
from app.models.user import User
from app.services.case_service import add_artifact_to_case, add_timeline_note, apply_case_update
from app.utils.audit import create_audit_log
from app.utils.playbooks import apply_playbook_to_case
from app.workflows.events import emit_event
from app.workflows.nodes import NodeContext, NodeError, executor, load_target_case


def _as_list(v) -> list:
    if v in (None, ""):
        return []
    if isinstance(v, str):
        return [x.strip() for x in v.split(",") if x.strip()]
    return [str(x) for x in v]


def _emit_updated(nctx: NodeContext, case_id: int, changes: dict) -> None:
    run = nctx.run
    if changes:
        nctx.after_commit.append(lambda: emit_event(run.tenant_id, "case.updated", case_id=case_id,
                                                    changes=changes, depth=run.depth + 1))


@executor("case_update")
async def run_case_update(nctx: NodeContext, config: dict) -> dict:
    case = await load_target_case(nctx, config)
    update = {}
    try:
        if config.get("severity"):
            update["severity"] = CaseSeverity(config["severity"])
        if config.get("status"):
            update["status"] = CaseStatus(config["status"])
    except ValueError as e:
        raise NodeError(str(e))
    if config.get("owner_email"):
        user = (await nctx.db.execute(
            select(User).join(TenantMembership, TenantMembership.user_id == User.id)
            .where(User.email.ilike(str(config["owner_email"])), TenantMembership.tenant_id == case.tenant_id)
        )).scalars().first()
        if not user:
            raise NodeError(f"no tenant member with email {config['owner_email']}")
        update["owner_id"] = user.id
    add, remove = _as_list(config.get("add_tags")), set(_as_list(config.get("remove_tags")))
    if add or remove:
        tags = [t for t in (case.tags or []) if t not in remove]
        tags += [t for t in add if t not in tags]
        update["tags"] = tags
    changes = await apply_case_update(nctx.db, case=case, update_data=update, user_id=None)
    _emit_updated(nctx, case.id, changes)
    return {"case_id": case.id, "changes": changes}


@executor("case_add_note")
async def run_case_add_note(nctx: NodeContext, config: dict) -> dict:
    case = await load_target_case(nctx, config)
    ev = await add_timeline_note(nctx.db, case=case, content=f"[automation] {config['content']}", user_id=None)
    return {"case_id": case.id, "event_id": ev.id}


@executor("case_add_artifact")
async def run_case_add_artifact(nctx: NodeContext, config: dict) -> dict:
    case = await load_target_case(nctx, config)
    try:
        atype = ArtifactType(config["artifact_type"])
    except ValueError:
        raise NodeError(f"unknown artifact_type {config['artifact_type']!r}")
    artifact = await add_artifact_to_case(nctx.db, case=case, artifact_type=atype, value=str(config["value"]),
                                          description=config.get("description"), user_id=None)
    return {"case_id": case.id, "artifact_id": artifact.id}


@executor("case_apply_playbook")
async def run_case_apply_playbook(nctx: NodeContext, config: dict) -> dict:
    case = await load_target_case(nctx, config)
    try:
        n = await apply_playbook_to_case(nctx.db, case=case, template_id=int(config["template_id"]), tenant_id=case.tenant_id)
    except HTTPException as e:
        raise NodeError(str(e.detail))
    await create_audit_log(db=nctx.db, entity_type="case", entity_id=case.id, action="playbook_applied",
                           tenant_id=case.tenant_id, user_id=None,
                           changes={"template_id": int(config["template_id"]), "tasks_added": n, "by": "automation"})
    return {"case_id": case.id, "tasks_added": n}


@executor("case_search")
async def run_case_search(nctx: NodeContext, config: dict) -> dict:
    q = select(Case).where(Case.tenant_id == nctx.run.tenant_id)
    statuses = _as_list(config.get("status"))
    if statuses:
        try:
            q = q.where(Case.status.in_([CaseStatus(s) for s in statuses]))
        except ValueError as e:
            raise NodeError(str(e))
    if config.get("title_contains"):
        q = q.where(Case.title.ilike(f"%{config['title_contains']}%"))
    if config.get("created_within_hours"):
        since = datetime.now(timezone.utc) - timedelta(hours=float(config["created_within_hours"]))
        q = q.where(Case.created_at >= since)
    if config.get("artifact_value"):
        q = q.join(CaseArtifact, CaseArtifact.case_id == Case.id).join(Artifact, Artifact.id == CaseArtifact.artifact_id) \
             .where(Artifact.value == str(config["artifact_value"]))
    rows = (await nctx.db.execute(q.order_by(Case.created_at.desc()).limit(200))).scalars().unique().all()
    tags_any = set(_as_list(config.get("tags_any")))
    if tags_any:  # tags is JSON; filter in Python (bounded by limit above)
        rows = [c for c in rows if tags_any & set(c.tags or [])]
    rows = rows[:50]
    cases = [{"id": c.id, "title": c.title, "status": c.status.value, "severity": c.severity.value,
              "tags": c.tags or [], "created_at": c.created_at.isoformat() if c.created_at else None} for c in rows]
    return {"cases": cases, "first": cases[0] if cases else None, "count": len(cases)}
```

- [ ] **Step 2: Smoke test** — reuse `/tmp/wf_smoke.py`, replacing the graph with:
```python
{"nodes": [{"id": "start", "type": "trigger", "config": {}},
           {"id": "upd", "type": "case_update", "config": {"severity": "critical", "add_tags": "wf-smoke"}},
           {"id": "note", "type": "case_add_note", "config": {"content": "severity was {{ steps.upd.output.changes }}"}},
           {"id": "find", "type": "case_search", "config": {"tags_any": "wf-smoke"}}],
 "edges": [{"id": "1", "source": "start", "target": "upd", "source_handle": None},
           {"id": "2", "source": "upd", "target": "note", "source_handle": None},
           {"id": "3", "source": "note", "target": "find", "source_handle": None}]}
```
Expected: all steps `succeeded`; the case is critical with tag `wf-smoke` and an `[automation]` note in its timeline; `find` output `count >= 1`. Restore the case's severity/tags by hand afterwards, then delete the scratch workflow by id.

- [ ] **Step 3: Commit**
```bash
git add backend/app/workflows/nodes/cases.py
git commit -m "feat(workflows): case action nodes and case_search"
```

---

### Task 10: Alert nodes (promote new/link/group, dismiss) + concurrency test

**Files:**
- Modify: `backend/app/workflows/nodes/alerts.py` (was empty)
- Test: `backend/tests/test_wf_alert_group.py` (DB-backed)

**Interfaces:**
- Consumes: `create_case_record` (Task 6), `emit_event` (Task 7), `NodeContext`, `NodeError`, `executor` (Task 8), `run_case_triage_task` (existing `app/tasks/triage.py`).
- Produces:
  - `async find_or_create_grouped_case(db, *, tenant_id: int, group_key: str, window_hours: float, case_data: dict) -> tuple[Case, bool, int | None]` — `(case, created, triage_id)`; takes `pg_advisory_xact_lock`, so it must run inside the caller's transaction and the caller must commit.
  - executors `alert_promote` (sets `run.case_id`), `alert_dismiss`.

- [ ] **Step 1: Write the failing DB test**

`backend/tests/test_wf_alert_group.py`:
```python
"""Two concurrent group-mode promotes with the same key must produce ONE case.
Runs against the dev Postgres; cleans up only the rows it created."""
import asyncio
import uuid

import pytest
from sqlalchemy import delete

from app.db.session import AsyncSessionLocal, engine
from app.models.case import Case, TimelineEvent
from app.models.case_triage import CaseTriageResult
from app.models.audit_log import AuditLog
from app.models.tenant import Tenant
from app.workflows.nodes.alerts import find_or_create_grouped_case


@pytest.mark.asyncio
async def test_concurrent_group_promotes_create_one_case():
    slug = f"wf-test-{uuid.uuid4().hex[:8]}"
    async with AsyncSessionLocal() as db:
        tenant = Tenant(name=slug, slug=slug)
        db.add(tenant)
        await db.commit()
        tenant_id = tenant.id

    async def promote():
        async with AsyncSessionLocal() as db:
            case, created, _ = await find_or_create_grouped_case(
                db, tenant_id=tenant_id, group_key="host:web-1", window_hours=6,
                case_data={"title": "EDR on web-1", "severity": "high", "tags": ["edr"]})
            await asyncio.sleep(0.2)  # widen the race window while holding the lock
            await db.commit()
            return case.id, created

    try:
        results = await asyncio.gather(promote(), promote(), promote())
        case_ids = {cid for cid, _ in results}
        assert len(case_ids) == 1
        assert sum(1 for _, created in results if created) == 1
    finally:
        async with AsyncSessionLocal() as db:
            ids = list({cid for cid, _ in results}) if "results" in locals() else []
            if ids:
                await db.execute(delete(CaseTriageResult).where(CaseTriageResult.case_id.in_(ids)))
                await db.execute(delete(TimelineEvent).where(TimelineEvent.case_id.in_(ids)))
                await db.execute(delete(AuditLog).where(AuditLog.entity_type == "case", AuditLog.entity_id.in_(ids)))
                await db.execute(delete(Case).where(Case.id.in_(ids)))
            await db.execute(delete(Tenant).where(Tenant.id == tenant_id))
            await db.commit()
        await engine.dispose()
```

- [ ] **Step 2: Run — expect FAIL** (`ImportError: find_or_create_grouped_case`).

Run: `docker compose exec backend python -m pytest tests/test_wf_alert_group.py -v`
(pytest-asyncio 1.x is installed in the container; `@pytest.mark.asyncio` is all that's needed.)

- [ ] **Step 3: Implement**

`backend/app/workflows/nodes/alerts.py`:
```python
import zlib
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, text

from app.models.case import Alert, Case, CaseSeverity, CaseStatus
from app.services.case_service import create_case_record
from app.utils.audit import create_audit_log
from app.workflows.events import emit_event
from app.workflows.nodes import NodeContext, NodeError, executor


def _case_data(config: dict) -> dict:
    tags = config.get("tags") or []
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",") if t.strip()]
    try:
        severity = CaseSeverity(config.get("severity") or "medium")
    except ValueError:
        raise NodeError(f"invalid severity {config.get('severity')!r}")
    return {"title": str(config["title"]), "description": config.get("description") or "",
            "severity": severity, "tags": tags, "source": "automation"}


async def find_or_create_grouped_case(db, *, tenant_id: int, group_key: str, window_hours: float, case_data: dict):
    lock_key = zlib.crc32(f"{tenant_id}:{group_key}".encode())
    await db.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": lock_key})
    since = datetime.now(timezone.utc) - timedelta(hours=window_hours)
    existing = (await db.execute(
        select(Case).where(Case.tenant_id == tenant_id, Case.group_key == group_key, Case.created_at >= since,
                           Case.status.notin_([CaseStatus.RESOLVED, CaseStatus.CLOSED]))
        .order_by(Case.created_at.desc()).limit(1)
    )).scalars().first()
    if existing:
        return existing, False, None
    data = dict(case_data)
    if isinstance(data.get("severity"), str):
        data["severity"] = CaseSeverity(data["severity"])
    case, triage_id = await create_case_record(db, tenant_id=tenant_id, data={**data, "group_key": group_key}, user_id=None)
    return case, True, triage_id


async def _load_pending_alert(nctx: NodeContext) -> Alert:
    alert = (await nctx.db.execute(select(Alert).where(
        Alert.id == nctx.run.alert_id, Alert.tenant_id == nctx.run.tenant_id).with_for_update())).scalars().first()
    if not alert:
        raise NodeError("this run has no alert")
    if alert.status != "pending":
        raise NodeError(f"alert {alert.id} is already {alert.status}")
    return alert


@executor("alert_promote")
async def run_alert_promote(nctx: NodeContext, config: dict) -> dict:
    from app.tasks.triage import run_case_triage_task
    alert = await _load_pending_alert(nctx)
    mode = config["mode"]
    triage_id = None
    if mode == "link":
        try:
            cid = int(config["case_id"])
        except (TypeError, ValueError):
            raise NodeError(f"case_id must be an integer, got {config.get('case_id')!r}")
        case = (await nctx.db.execute(select(Case).where(Case.id == cid, Case.tenant_id == nctx.run.tenant_id))).scalars().first()
        if not case:
            raise NodeError(f"case {cid} not found")
        created = False
    elif mode == "group":
        case, created, triage_id = await find_or_create_grouped_case(
            nctx.db, tenant_id=nctx.run.tenant_id, group_key=str(config["group_key"]),
            window_hours=float(config.get("window_hours") or 24), case_data=_case_data(config))
    else:
        case, triage_id = await create_case_record(nctx.db, tenant_id=nctx.run.tenant_id, data=_case_data(config), user_id=None)
        created = True

    alert.status = "promoted"
    alert.case_id = case.id
    nctx.run.case_id = case.id  # later nodes now act on this case
    await create_audit_log(db=nctx.db, entity_type="alert", entity_id=alert.id, action="promote",
                           tenant_id=nctx.run.tenant_id, user_id=None,
                           changes={"case_id": case.id, "mode": mode, "by": "automation"})
    await nctx.db.flush()

    run = nctx.run
    if created:
        nctx.after_commit.append(lambda: run_case_triage_task.delay(triage_id))
        nctx.after_commit.append(lambda: emit_event(run.tenant_id, "case.created", case_id=case.id, depth=run.depth + 1))
    return {"case_id": case.id, "created": created}


@executor("alert_dismiss")
async def run_alert_dismiss(nctx: NodeContext, config: dict) -> dict:
    alert = await _load_pending_alert(nctx)
    alert.status = "dismissed"
    alert.dismiss_reason = config.get("reason") or None
    await create_audit_log(db=nctx.db, entity_type="alert", entity_id=alert.id, action="dismiss",
                           tenant_id=nctx.run.tenant_id, user_id=None,
                           changes={"reason": alert.dismiss_reason, "by": "automation"})
    return {"alert_id": alert.id, "dismissed": True}
```

- [ ] **Step 4: Run — expect PASS.** Confirm cleanup: `docker compose exec db psql -U user -d sicms -c "select count(*) from tenants where slug like 'wf-test-%'"` → `0`.

- [ ] **Step 5: Commit**
```bash
git add backend/app/workflows/nodes/alerts.py backend/tests/test_wf_alert_group.py
git commit -m "feat(workflows): alert promote (new/link/group) and dismiss nodes"
```

---

### Task 11: Workflows + runs API

**Files:**
- Create: `backend/app/schemas/workflow.py`, `backend/app/api/v1/workflows.py`
- Modify: `backend/app/api/api.py`

**Interfaces:**
- Consumes: `validate_graph` (Task 4), `create_run`, `cancel_run` (Task 8), `advance_run_task` (Task 8), models (Task 6).
- Produces HTTP API (prefix `/api/v1`):

| Method + path | Role | Body / query | Returns |
|---|---|---|---|
| `GET /workflows/settings/http-allowlist` | admin | – | `{"hosts": [str]}` |
| `PUT /workflows/settings/http-allowlist` | admin | `{"hosts": [str]}` | same |
| `GET /workflows` | viewer+ | – | `[WorkflowSummary]` |
| `POST /workflows` | admin | `WorkflowIn` | `WorkflowOut` (201) |
| `GET /workflows/{id}` | viewer+ | – | `WorkflowOut` |
| `PUT /workflows/{id}` | admin | `WorkflowIn` | `WorkflowOut` (version+1; 422 if enabled and invalid) |
| `DELETE /workflows/{id}` | admin | – | 204 |
| `POST /workflows/{id}/validate` | viewer+ | – | `{"errors": [...]}` |
| `POST /workflows/{id}/enable` / `/disable` | admin | – | `WorkflowOut` (enable → 422 `{"errors"}` if invalid) |
| `POST /workflows/{id}/run` | analyst+ | `{"case_id": int}` | `{"run_id": int}` (400 unless trigger_type manual & enabled) |
| `POST /workflows/{id}/dry-run` | admin | `DryRunIn` | `{"run_id": int}` |
| `GET /workflow-runs` | viewer+ | `workflow_id, case_id, alert_id, status, dry_run, limit=50` | `[RunSummary]` (top-level only) |
| `GET /workflow-runs/{id}` | viewer+ | – | `RunDetail` |
| `POST /workflow-runs/{id}/cancel` | analyst+ | – | `RunSummary` |

- [ ] **Step 1: Schemas**

`backend/app/schemas/workflow.py`:
```python
from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

TriggerType = Literal["case.created", "case.updated", "alert.ingested", "manual"]


class WorkflowIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: Optional[str] = None
    trigger_type: TriggerType
    trigger_filter: Optional[str] = None
    graph: Dict[str, Any]


class WorkflowSummary(BaseModel):
    id: int
    name: str
    description: Optional[str]
    enabled: bool
    trigger_type: str
    version: int
    updated_at: Optional[datetime]
    created_at: Optional[datetime]
    last_run_status: Optional[str] = None
    last_run_at: Optional[datetime] = None
    run_count: int = 0


class WorkflowOut(WorkflowSummary):
    trigger_filter: Optional[str]
    graph: Dict[str, Any]
    validation_errors: List[Dict[str, Any]] = []


class ManualRunIn(BaseModel):
    case_id: int


class DryRunIn(BaseModel):
    case_id: Optional[int] = None
    alert_id: Optional[int] = None
    payload: Optional[Dict[str, Any]] = None
    mocks: Dict[str, Dict[str, Any]] = {}
    ask_user_answers: Dict[str, str] = {}


class AllowlistIn(BaseModel):
    hosts: List[str]


class RunSummary(BaseModel):
    id: int
    workflow_id: int
    workflow_name: Optional[str] = None
    workflow_version: int
    status: str
    case_id: Optional[int]
    alert_id: Optional[int]
    is_dry_run: bool
    depth: int
    error: Optional[str]
    started_at: Optional[datetime]
    finished_at: Optional[datetime]

    class Config:
        from_attributes = True


class StepOut(BaseModel):
    id: int
    node_id: str
    status: str
    input: Optional[Any]
    output: Optional[Any]
    error: Optional[str]
    attempt: int
    started_at: Optional[datetime]
    finished_at: Optional[datetime]

    class Config:
        from_attributes = True


class ChildRunOut(BaseModel):
    id: int
    parent_step_id: Optional[int]
    loop_index: Optional[int]
    status: str
    error: Optional[str]

    class Config:
        from_attributes = True


class RunDetail(RunSummary):
    trigger_payload: Optional[Any]
    graph_snapshot: Dict[str, Any]
    parent_run_id: Optional[int]
    loop_index: Optional[int]
    loop_item: Optional[Any]
    steps: List[StepOut]
    children: List[ChildRunOut]
```

- [ ] **Step 2: Router**

`backend/app/api/v1/workflows.py`:
```python
from typing import Any, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from app.models.case import Alert, Case
from app.models.tenant import Tenant
from app.models.user import User
from app.models.workflow import Workflow, WorkflowRun, WorkflowRunStep
from app.schemas.workflow import (AllowlistIn, DryRunIn, ManualRunIn, RunDetail, RunSummary,
                                  WorkflowIn, WorkflowOut, WorkflowSummary)
from app.utils.audit import create_audit_log
from app.workflows.runtime import cancel_run, create_run
from app.workflows.validation import validate_graph

router = APIRouter()
runs_router = APIRouter()


async def _get_workflow(db, workflow_id: int, tenant_id: int) -> Workflow:
    wf = (await db.execute(select(Workflow).where(Workflow.id == workflow_id, Workflow.tenant_id == tenant_id))).scalars().first()
    if not wf:
        raise HTTPException(status_code=404, detail="Workflow not found")
    return wf


async def _run_stats(db, workflow_ids: List[int]) -> dict:
    if not workflow_ids:
        return {}
    counts = dict((await db.execute(
        select(WorkflowRun.workflow_id, func.count()).where(
            WorkflowRun.workflow_id.in_(workflow_ids), WorkflowRun.parent_run_id.is_(None))
        .group_by(WorkflowRun.workflow_id))).all())
    last = {}
    for r in (await db.execute(
            select(WorkflowRun).where(WorkflowRun.workflow_id.in_(workflow_ids), WorkflowRun.parent_run_id.is_(None))
            .order_by(WorkflowRun.started_at.desc()).limit(500))).scalars():
        last.setdefault(r.workflow_id, r)
    return {wid: {"run_count": counts.get(wid, 0),
                  "last_run_status": last[wid].status if wid in last else None,
                  "last_run_at": last[wid].started_at if wid in last else None} for wid in workflow_ids}


def _summary(wf: Workflow, stats: dict) -> dict:
    return {"id": wf.id, "name": wf.name, "description": wf.description, "enabled": wf.enabled,
            "trigger_type": wf.trigger_type, "version": wf.version, "updated_at": wf.updated_at,
            "created_at": wf.created_at, **stats.get(wf.id, {})}


def _out(wf: Workflow, stats: dict) -> dict:
    return {**_summary(wf, stats), "trigger_filter": wf.trigger_filter, "graph": wf.graph,
            "validation_errors": validate_graph(wf.graph, wf.trigger_type)}


# settings routes are declared BEFORE /{workflow_id} so "settings" isn't parsed as an id
@router.get("/settings/http-allowlist")
async def get_allowlist(db: AsyncSession = Depends(deps.get_db), current_user: User = Depends(deps.require_admin),
                        tenant_id: int = Depends(deps.get_effective_tenant_id)) -> Any:
    tenant = (await db.execute(select(Tenant).where(Tenant.id == tenant_id))).scalars().first()
    return {"hosts": tenant.workflow_http_allowlist or []}


@router.put("/settings/http-allowlist")
async def put_allowlist(body: AllowlistIn, db: AsyncSession = Depends(deps.get_db),
                        current_user: User = Depends(deps.require_admin),
                        tenant_id: int = Depends(deps.get_effective_tenant_id)) -> Any:
    tenant = (await db.execute(select(Tenant).where(Tenant.id == tenant_id))).scalars().first()
    hosts = sorted({h.strip().lower() for h in body.hosts if h.strip()})
    tenant.workflow_http_allowlist = hosts
    await create_audit_log(db=db, entity_type="tenant", entity_id=tenant_id, action="http_allowlist_update",
                           tenant_id=tenant_id, user_id=current_user.id, changes={"hosts": hosts})
    await db.commit()
    return {"hosts": hosts}


@router.get("/", response_model=List[WorkflowSummary])
async def list_workflows(db: AsyncSession = Depends(deps.get_db), current_user: User = Depends(deps.get_current_active_user),
                         tenant_id: int = Depends(deps.get_effective_tenant_id)) -> Any:
    wfs = (await db.execute(select(Workflow).where(Workflow.tenant_id == tenant_id).order_by(Workflow.name))).scalars().all()
    stats = await _run_stats(db, [w.id for w in wfs])
    return [_summary(w, stats) for w in wfs]


@router.post("/", response_model=WorkflowOut, status_code=201)
async def create_workflow(body: WorkflowIn, db: AsyncSession = Depends(deps.get_db),
                          current_user: User = Depends(deps.require_admin),
                          tenant_id: int = Depends(deps.get_effective_tenant_id)) -> Any:
    wf = Workflow(tenant_id=tenant_id, name=body.name, description=body.description, trigger_type=body.trigger_type,
                  trigger_filter=body.trigger_filter, graph=body.graph, enabled=False, version=1, created_by=current_user.id)
    db.add(wf)
    await db.flush()
    await create_audit_log(db=db, entity_type="workflow", entity_id=wf.id, action="create", tenant_id=tenant_id, user_id=current_user.id)
    await db.commit()
    return _out(wf, {})


@router.get("/{workflow_id}", response_model=WorkflowOut)
async def get_workflow(workflow_id: int, db: AsyncSession = Depends(deps.get_db),
                       current_user: User = Depends(deps.get_current_active_user),
                       tenant_id: int = Depends(deps.get_effective_tenant_id)) -> Any:
    wf = await _get_workflow(db, workflow_id, tenant_id)
    return _out(wf, await _run_stats(db, [wf.id]))


@router.put("/{workflow_id}", response_model=WorkflowOut)
async def update_workflow(workflow_id: int, body: WorkflowIn, db: AsyncSession = Depends(deps.get_db),
                          current_user: User = Depends(deps.require_admin),
                          tenant_id: int = Depends(deps.get_effective_tenant_id)) -> Any:
    wf = await _get_workflow(db, workflow_id, tenant_id)
    if wf.enabled:
        errors = validate_graph(body.graph, body.trigger_type)
        if errors:
            raise HTTPException(status_code=422, detail={"errors": errors, "message": "Disable the workflow to save an invalid graph"})
    wf.name, wf.description, wf.trigger_type = body.name, body.description, body.trigger_type
    wf.trigger_filter, wf.graph, wf.version = body.trigger_filter, body.graph, wf.version + 1
    await create_audit_log(db=db, entity_type="workflow", entity_id=wf.id, action="update", tenant_id=tenant_id,
                           user_id=current_user.id, changes={"version": wf.version})
    await db.commit()
    return _out(wf, await _run_stats(db, [wf.id]))


@router.delete("/{workflow_id}", status_code=204)
async def delete_workflow(workflow_id: int, db: AsyncSession = Depends(deps.get_db),
                          current_user: User = Depends(deps.require_admin),
                          tenant_id: int = Depends(deps.get_effective_tenant_id)) -> Response:
    wf = await _get_workflow(db, workflow_id, tenant_id)
    for run in (await db.execute(select(WorkflowRun).where(WorkflowRun.workflow_id == wf.id))).scalars():
        await cancel_run(db, run)
    await create_audit_log(db=db, entity_type="workflow", entity_id=wf.id, action="delete", tenant_id=tenant_id,
                           user_id=current_user.id, changes={"name": wf.name})
    await db.delete(wf)
    await db.commit()
    return Response(status_code=204)


@router.post("/{workflow_id}/validate")
async def validate_workflow(workflow_id: int, db: AsyncSession = Depends(deps.get_db),
                            current_user: User = Depends(deps.get_current_active_user),
                            tenant_id: int = Depends(deps.get_effective_tenant_id)) -> Any:
    wf = await _get_workflow(db, workflow_id, tenant_id)
    return {"errors": validate_graph(wf.graph, wf.trigger_type)}


async def _set_enabled(db, wf: Workflow, enabled: bool, user_id: int, tenant_id: int) -> None:
    if enabled:
        errors = validate_graph(wf.graph, wf.trigger_type)
        if errors:
            raise HTTPException(status_code=422, detail={"errors": errors})
    wf.enabled = enabled
    await create_audit_log(db=db, entity_type="workflow", entity_id=wf.id, action="enable" if enabled else "disable",
                           tenant_id=tenant_id, user_id=user_id)
    await db.commit()


@router.post("/{workflow_id}/enable", response_model=WorkflowOut)
async def enable_workflow(workflow_id: int, db: AsyncSession = Depends(deps.get_db),
                          current_user: User = Depends(deps.require_admin),
                          tenant_id: int = Depends(deps.get_effective_tenant_id)) -> Any:
    wf = await _get_workflow(db, workflow_id, tenant_id)
    await _set_enabled(db, wf, True, current_user.id, tenant_id)
    return _out(wf, await _run_stats(db, [wf.id]))


@router.post("/{workflow_id}/disable", response_model=WorkflowOut)
async def disable_workflow(workflow_id: int, db: AsyncSession = Depends(deps.get_db),
                           current_user: User = Depends(deps.require_admin),
                           tenant_id: int = Depends(deps.get_effective_tenant_id)) -> Any:
    wf = await _get_workflow(db, workflow_id, tenant_id)
    await _set_enabled(db, wf, False, current_user.id, tenant_id)
    return _out(wf, await _run_stats(db, [wf.id]))


@router.post("/{workflow_id}/run")
async def run_manual(workflow_id: int, body: ManualRunIn, db: AsyncSession = Depends(deps.get_db),
                     current_user: User = Depends(deps.require_analyst_or_above),
                     tenant_id: int = Depends(deps.get_effective_tenant_id)) -> Any:
    from app.tasks.workflows import advance_run_task
    wf = await _get_workflow(db, workflow_id, tenant_id)
    if wf.trigger_type != "manual" or not wf.enabled:
        raise HTTPException(status_code=400, detail="Only enabled manual workflows can be run by hand")
    case = (await db.execute(select(Case).where(Case.id == body.case_id, Case.tenant_id == tenant_id))).scalars().first()
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    run = await create_run(db, wf, trigger_payload={"event": "manual", "case_id": case.id, "user_id": current_user.id},
                           case_id=case.id, user_id=current_user.id)
    await db.commit()
    advance_run_task.delay(run.id)
    return {"run_id": run.id}


@router.post("/{workflow_id}/dry-run")
async def dry_run(workflow_id: int, body: DryRunIn, db: AsyncSession = Depends(deps.get_db),
                  current_user: User = Depends(deps.require_admin),
                  tenant_id: int = Depends(deps.get_effective_tenant_id)) -> Any:
    from app.tasks.workflows import advance_run_task
    wf = await _get_workflow(db, workflow_id, tenant_id)
    errors = validate_graph(wf.graph, wf.trigger_type)
    if errors:
        raise HTTPException(status_code=422, detail={"errors": errors})
    case_id = alert_id = None
    if body.case_id is not None:
        if not (await db.execute(select(Case.id).where(Case.id == body.case_id, Case.tenant_id == tenant_id))).first():
            raise HTTPException(status_code=404, detail="Case not found")
        case_id = body.case_id
    if body.alert_id is not None:
        alert = (await db.execute(select(Alert).where(Alert.id == body.alert_id, Alert.tenant_id == tenant_id))).scalars().first()
        if not alert:
            raise HTTPException(status_code=404, detail="Alert not found")
        alert_id, case_id = alert.id, case_id or alert.case_id
    payload = body.payload or {"event": wf.trigger_type, "case_id": case_id, "alert_id": alert_id, "changes": {}}
    run = await create_run(db, wf, trigger_payload=payload, case_id=case_id, alert_id=alert_id, is_dry_run=True,
                           dry_run_mocks={"mocks": body.mocks, "ask_user_answers": body.ask_user_answers},
                           user_id=current_user.id)
    await db.commit()
    advance_run_task.delay(run.id)
    return {"run_id": run.id}


# ── runs ─────────────────────────────────────────────────────────

async def _get_run(db, run_id: int, tenant_id: int) -> WorkflowRun:
    run = (await db.execute(select(WorkflowRun).where(WorkflowRun.id == run_id, WorkflowRun.tenant_id == tenant_id))).scalars().first()
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    return run


async def _names(db, ids) -> dict:
    return dict((await db.execute(select(Workflow.id, Workflow.name).where(Workflow.id.in_(list(ids))))).all()) if ids else {}


@runs_router.get("/", response_model=List[RunSummary])
async def list_runs(workflow_id: Optional[int] = None, case_id: Optional[int] = None, alert_id: Optional[int] = None,
                    status: Optional[str] = None, dry_run: Optional[bool] = None, limit: int = 50,
                    db: AsyncSession = Depends(deps.get_db), current_user: User = Depends(deps.get_current_active_user),
                    tenant_id: int = Depends(deps.get_effective_tenant_id)) -> Any:
    q = select(WorkflowRun).where(WorkflowRun.tenant_id == tenant_id, WorkflowRun.parent_run_id.is_(None))
    if workflow_id is not None:
        q = q.where(WorkflowRun.workflow_id == workflow_id)
    if case_id is not None:
        q = q.where(WorkflowRun.case_id == case_id)
    if alert_id is not None:
        q = q.where(WorkflowRun.alert_id == alert_id)
    if status:
        q = q.where(WorkflowRun.status == status)
    if dry_run is not None:
        q = q.where(WorkflowRun.is_dry_run == dry_run)
    runs = (await db.execute(q.order_by(WorkflowRun.started_at.desc()).limit(min(limit, 200)))).scalars().all()
    names = await _names(db, {r.workflow_id for r in runs})
    return [{**RunSummary.model_validate(r).model_dump(), "workflow_name": names.get(r.workflow_id)} for r in runs]


@runs_router.get("/{run_id}", response_model=RunDetail)
async def get_run(run_id: int, db: AsyncSession = Depends(deps.get_db),
                  current_user: User = Depends(deps.get_current_active_user),
                  tenant_id: int = Depends(deps.get_effective_tenant_id)) -> Any:
    run = await _get_run(db, run_id, tenant_id)
    steps = (await db.execute(select(WorkflowRunStep).where(WorkflowRunStep.run_id == run.id).order_by(WorkflowRunStep.id))).scalars().all()
    children = (await db.execute(select(WorkflowRun).where(WorkflowRun.parent_run_id == run.id).order_by(WorkflowRun.loop_index))).scalars().all()
    names = await _names(db, {run.workflow_id})
    return {**RunSummary.model_validate(run).model_dump(), "workflow_name": names.get(run.workflow_id),
            "trigger_payload": run.trigger_payload, "graph_snapshot": run.graph_snapshot,
            "parent_run_id": run.parent_run_id, "loop_index": run.loop_index, "loop_item": run.loop_item,
            "steps": steps, "children": children}


@runs_router.post("/{run_id}/cancel", response_model=RunSummary)
async def cancel(run_id: int, db: AsyncSession = Depends(deps.get_db),
                 current_user: User = Depends(deps.require_analyst_or_above),
                 tenant_id: int = Depends(deps.get_effective_tenant_id)) -> Any:
    run = await _get_run(db, run_id, tenant_id)
    await cancel_run(db, run)
    await create_audit_log(db=db, entity_type="workflow_run", entity_id=run.id, action="cancel", tenant_id=tenant_id, user_id=current_user.id)
    await db.commit()
    return run
```

- [ ] **Step 3: Register routers**

In `backend/app/api/api.py` add `workflows` to the `from app.api.v1 import ...` line and:
```python
api_router.include_router(workflows.router, prefix="/workflows", tags=["workflows"])
api_router.include_router(workflows.runs_router, prefix="/workflow-runs", tags=["workflows"])
```

- [ ] **Step 4: Verify with curl**

Restart backend + worker. Log in, grab a token (as an admin):
```bash
TOKEN=$(curl -s -X POST localhost:8000/api/v1/auth/login/access-token -H 'Content-Type: application/x-www-form-urlencoded' \
  -d 'username=<admin email>&password=<password>' | python3 -c 'import sys,json;print(json.load(sys.stdin)["access_token"])')
H="Authorization: Bearer $TOKEN"
curl -s -X POST localhost:8000/api/v1/workflows/ -H "$H" -H 'Content-Type: application/json' -d '{
 "name":"tag x","trigger_type":"case.created","trigger_filter":"\"x\" in case.tags",
 "graph":{"nodes":[{"id":"start","type":"trigger","config":{}},
                   {"id":"note","type":"case_add_note","config":{"content":"tag X seen"}}],
          "edges":[{"id":"e1","source":"start","target":"note","source_handle":null}]}}'
```
Expected: 201 with `validation_errors: []`, `enabled: false`. Then:
- `POST /workflows/<id>/enable` → `enabled: true`.
- Create a case with tag `x` via the UI → within a few seconds its timeline shows `[automation] tag X seen`; `GET /workflow-runs?workflow_id=<id>` shows a `succeeded` run.
- Create a case **without** tag `x` → no new run.
- `POST /workflows/<id>/dry-run -d '{"case_id": <case id>}'` → `GET /workflow-runs/<run_id>` shows `is_dry_run: true`, the `note` step output has `simulated: true` and `would_do.content`, and **no new note** on the case.
- Viewer token: `POST /workflows/` → 403. Other-tenant workflow id → 404.
- `GET /workflows/settings/http-allowlist` → `{"hosts": []}` (not a 422).
Delete the test workflow by id afterwards.

- [ ] **Step 5: Commit**
```bash
git add backend/app/schemas/workflow.py backend/app/api/v1/workflows.py backend/app/api/api.py
git commit -m "feat(workflows): workflows, runs, dry-run and allowlist API"
```

---

### Task 12: Frontend foundation — dependency, types, node catalog, list page, route, sidebar

**Files:**
- Modify: `frontend/package.json` (via npm), `frontend/src/App.tsx`, `frontend/src/components/layout/Layout.tsx`
- Create: `frontend/src/features/automations/types.ts`, `frontend/src/features/automations/nodeCatalog.ts`, `frontend/src/features/automations/AutomationsList.tsx`, `frontend/src/features/automations/RunsTable.tsx`

**Interfaces:**
- Consumes: API from Task 11.
- Produces: TS types `WfNode`, `WfEdge`, `WfGraph`, `ValidationError`, `WorkflowSummary`, `Workflow`, `RunSummary`, `RunStep`, `ChildRun`, `RunDetail`, `TriggerType`; `NODE_DEFS`, `NODE_DEF`, `TRIGGER_LABEL`, `FieldDef`, `NodeDef`; `<RunsTable workflowId? caseId? />`; `STATUS_STYLE: Record<string, string>` (exported from `RunsTable.tsx`).

No frontend test runner exists in this repo; each frontend task is verified with `npm run build` (type-check + bundle) plus a manual browser check.

- [ ] **Step 1: Install React Flow**

Run (in `frontend/`): `npm install @xyflow/react`
Expected: `package.json` gains `"@xyflow/react": "^12.x"`.

- [ ] **Step 2: Types**

`frontend/src/features/automations/types.ts`:
```ts
export type TriggerType = 'case.created' | 'case.updated' | 'alert.ingested' | 'manual';

export interface WfNode {
    id: string;
    type: string;
    position: { x: number; y: number };
    config: Record<string, unknown>;
    parent_id?: string | null;
    size?: { width: number; height: number };
    continue_on_error?: boolean;
    retries?: number;
    execute_in_dry_run?: boolean;
    mock_output?: Record<string, unknown> | null;
}

export interface WfEdge { id: string; source: string; target: string; source_handle: 'true' | 'false' | null }
export interface WfGraph { nodes: WfNode[]; edges: WfEdge[] }
export interface ValidationError { node_id: string | null; message: string }

export interface WorkflowSummary {
    id: number;
    name: string;
    description: string | null;
    enabled: boolean;
    trigger_type: TriggerType;
    version: number;
    updated_at: string | null;
    created_at: string | null;
    last_run_status: string | null;
    last_run_at: string | null;
    run_count: number;
}

export interface Workflow extends WorkflowSummary {
    trigger_filter: string | null;
    graph: WfGraph;
    validation_errors: ValidationError[];
}

export interface RunSummary {
    id: number;
    workflow_id: number;
    workflow_name: string | null;
    workflow_version: number;
    status: string;
    case_id: number | null;
    alert_id: number | null;
    is_dry_run: boolean;
    depth: number;
    error: string | null;
    started_at: string | null;
    finished_at: string | null;
}

export interface RunStep {
    id: number;
    node_id: string;
    status: string;
    input: unknown;
    output: Record<string, unknown> | null;
    error: string | null;
    attempt: number;
    started_at: string | null;
    finished_at: string | null;
}

export interface ChildRun { id: number; parent_step_id: number | null; loop_index: number | null; status: string; error: string | null }

export interface RunDetail extends RunSummary {
    trigger_payload: unknown;
    graph_snapshot: WfGraph;
    parent_run_id: number | null;
    loop_index: number | null;
    loop_item: unknown;
    steps: RunStep[];
    children: ChildRun[];
}
```

- [ ] **Step 3: Node catalog**

`frontend/src/features/automations/nodeCatalog.ts`:
```ts
import type { LucideIcon } from 'lucide-react';
import { Zap, GitBranch, Globe, PenLine, StickyNote, Database, BookText, Search, ArrowUpCircle, XCircle } from 'lucide-react';
import type { TriggerType } from './types';

export type FieldKind = 'text' | 'textarea' | 'select' | 'json' | 'number' | 'expression';

export interface FieldDef {
    key: string;
    label: string;
    kind: FieldKind;
    options?: string[];
    placeholder?: string;
    help?: string;
    required?: boolean;
}

export interface NodeDef {
    type: string;
    label: string;
    category: 'Trigger' | 'Logic' | 'Cases' | 'Alerts' | 'HTTP' | 'Slack';
    icon: LucideIcon;
    fields: FieldDef[];
    /** node has true/false output handles */
    branching?: boolean;
    alertOnly?: boolean;
    /** renders as a resizable container (for_each) */
    container?: boolean;
    /** has side effects -> simulated in dry-run, mock_output editable */
    sideEffect?: boolean;
}

const CASE_ID: FieldDef = { key: 'case_id', label: 'Case ID (optional)', kind: 'text', placeholder: 'defaults to the run\'s case', help: 'e.g. {{ loop.item.id }}' };
const SEVERITIES = ['', 'critical', 'high', 'medium', 'low', 'info'];
const STATUSES = ['', 'new', 'open', 'in_progress', 'pending', 'resolved', 'closed'];

export const NODE_DEFS: NodeDef[] = [
    { type: 'trigger', label: 'Trigger', category: 'Trigger', icon: Zap, fields: [] },
    {
        type: 'condition', label: 'Condition', category: 'Logic', icon: GitBranch, branching: true,
        fields: [{ key: 'expression', label: 'Expression', kind: 'expression', required: true, placeholder: "'phishing' in case.tags", help: 'Jinja expression, no {{ }}' }],
    },
    {
        type: 'http_request', label: 'HTTP request', category: 'HTTP', icon: Globe, sideEffect: true,
        fields: [
            { key: 'method', label: 'Method', kind: 'select', options: ['GET', 'POST', 'PUT', 'PATCH', 'DELETE'], required: true },
            { key: 'url', label: 'URL', kind: 'text', required: true, placeholder: 'https://hooks.example.com/{{ case.id }}' },
            { key: 'headers', label: 'Headers (JSON)', kind: 'json', placeholder: '{"Authorization": "Bearer ..."}' },
            { key: 'body', label: 'Body (JSON or text)', kind: 'json' },
        ],
    },
    {
        type: 'case_update', label: 'Update case', category: 'Cases', icon: PenLine, sideEffect: true,
        fields: [
            { key: 'severity', label: 'Severity', kind: 'select', options: SEVERITIES },
            { key: 'status', label: 'Status', kind: 'select', options: STATUSES },
            { key: 'owner_email', label: 'Assign to (email)', kind: 'text' },
            { key: 'add_tags', label: 'Add tags', kind: 'text', placeholder: 'escalated, vip' },
            { key: 'remove_tags', label: 'Remove tags', kind: 'text' },
            CASE_ID,
        ],
    },
    { type: 'case_add_note', label: 'Add note', category: 'Cases', icon: StickyNote, sideEffect: true,
      fields: [{ key: 'content', label: 'Note', kind: 'textarea', required: true }, CASE_ID] },
    {
        type: 'case_add_artifact', label: 'Add artifact', category: 'Cases', icon: Database, sideEffect: true,
        fields: [
            { key: 'artifact_type', label: 'Type', kind: 'select', options: ['ip', 'domain', 'url', 'file_hash', 'email', 'other'], required: true },
            { key: 'value', label: 'Value', kind: 'text', required: true },
            { key: 'description', label: 'Description', kind: 'text' },
            CASE_ID,
        ],
    },
    { type: 'case_apply_playbook', label: 'Apply playbook', category: 'Cases', icon: BookText, sideEffect: true,
      fields: [{ key: 'template_id', label: 'Playbook ID', kind: 'number', required: true }, CASE_ID] },
    {
        type: 'case_search', label: 'Search cases', category: 'Cases', icon: Search,
        fields: [
            { key: 'status', label: 'Status in', kind: 'text', placeholder: 'new, open, in_progress' },
            { key: 'tags_any', label: 'Has any tag', kind: 'text' },
            { key: 'title_contains', label: 'Title contains', kind: 'text' },
            { key: 'artifact_value', label: 'Has artifact value', kind: 'text', placeholder: '{{ alert.payload.src_ip }}' },
            { key: 'created_within_hours', label: 'Created within (hours)', kind: 'number' },
        ],
    },
    {
        type: 'alert_promote', label: 'Promote alert', category: 'Alerts', icon: ArrowUpCircle, alertOnly: true, sideEffect: true,
        fields: [
            { key: 'mode', label: 'Mode', kind: 'select', options: ['new', 'link', 'group'], required: true,
              help: 'new: create a case · link: attach to case_id · group: reuse an open case with the same group key' },
            { key: 'title', label: 'Case title (new/group)', kind: 'text', placeholder: '{{ alert.title }}' },
            { key: 'description', label: 'Description', kind: 'textarea' },
            { key: 'severity', label: 'Severity', kind: 'select', options: SEVERITIES },
            { key: 'tags', label: 'Tags', kind: 'text' },
            { key: 'group_key', label: 'Group key (group)', kind: 'text', placeholder: 'host:{{ alert.payload.host }}' },
            { key: 'window_hours', label: 'Group window (hours)', kind: 'number', placeholder: '24' },
            { key: 'case_id', label: 'Case ID (link)', kind: 'text' },
        ],
    },
    { type: 'alert_dismiss', label: 'Dismiss alert', category: 'Alerts', icon: XCircle, alertOnly: true, sideEffect: true,
      fields: [{ key: 'reason', label: 'Reason', kind: 'text' }] },
];

export const NODE_DEF: Record<string, NodeDef> = Object.fromEntries(NODE_DEFS.map((d) => [d.type, d]));

export const TRIGGER_LABEL: Record<TriggerType, string> = {
    'case.created': 'Case created',
    'case.updated': 'Case updated',
    'alert.ingested': 'Alert ingested',
    manual: 'Manual',
};
```

- [ ] **Step 4: Runs table (shared by list page, editor, CaseDetail)**

`frontend/src/features/automations/RunsTable.tsx`:
```tsx
import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { api } from '../../api/client';
import { cn } from '../../lib/utils';
import type { RunSummary } from './types';

export const STATUS_STYLE: Record<string, string> = {
    succeeded: 'bg-emerald-50 text-emerald-700 border-emerald-200',
    failed: 'bg-red-50 text-red-700 border-red-200',
    running: 'bg-accent-50 text-accent-700 border-accent-200',
    queued: 'bg-zinc-50 text-zinc-600 border-zinc-200',
    waiting: 'bg-amber-50 text-amber-700 border-amber-200',
    cancelled: 'bg-zinc-100 text-zinc-500 border-zinc-200',
    skipped: 'bg-zinc-50 text-zinc-400 border-zinc-200',
    pending: 'bg-zinc-50 text-zinc-600 border-zinc-200',
};

export function StatusBadge({ status }: { status: string }) {
    return <span className={cn('inline-flex px-2 py-0.5 text-[10px] font-bold uppercase tracking-wider border', STATUS_STYLE[status] ?? STATUS_STYLE.pending)}>{status}</span>;
}

function duration(r: RunSummary) {
    if (!r.started_at) return '—';
    const end = r.finished_at ? new Date(r.finished_at).getTime() : Date.now();
    const s = Math.round((end - new Date(r.started_at).getTime()) / 1000);
    return s < 60 ? `${s}s` : `${Math.floor(s / 60)}m ${s % 60}s`;
}

export default function RunsTable({ workflowId, caseId }: { workflowId?: number; caseId?: number }) {
    const [showDry, setShowDry] = useState(true);
    const params: Record<string, string | number | boolean> = { limit: 100 };
    if (workflowId) params.workflow_id = workflowId;
    if (caseId) params.case_id = caseId;
    if (!showDry) params.dry_run = false;

    const { data: runs = [], isLoading } = useQuery({
        queryKey: ['workflow-runs', params],
        queryFn: async () => (await api.get('/workflow-runs/', { params })).data as RunSummary[],
        refetchInterval: (q) => ((q.state.data as RunSummary[] | undefined)?.some((r) => ['running', 'waiting'].includes(r.status)) ? 3000 : false),
    });

    return (
        <div className="bg-white border border-zinc-200">
            <div className="flex items-center justify-between px-4 py-2 border-b border-zinc-200">
                <span className="label-mono">runs</span>
                <label className="flex items-center gap-2 text-xs text-zinc-600">
                    <input type="checkbox" checked={showDry} onChange={(e) => setShowDry(e.target.checked)} /> include dry runs
                </label>
            </div>
            <table className="w-full text-sm">
                <thead className="bg-zinc-50 text-left label-mono">
                    <tr><th className="px-4 py-2">#</th><th className="px-4 py-2">Workflow</th><th className="px-4 py-2">Status</th>
                        <th className="px-4 py-2">Case / alert</th><th className="px-4 py-2">Started</th><th className="px-4 py-2">Duration</th></tr>
                </thead>
                <tbody className="divide-y divide-zinc-100">
                    {isLoading && <tr><td colSpan={6} className="px-4 py-8 text-center text-zinc-400">Loading…</td></tr>}
                    {!isLoading && runs.length === 0 && <tr><td colSpan={6} className="px-4 py-8 text-center text-zinc-400">No runs yet</td></tr>}
                    {runs.map((r) => (
                        <tr key={r.id} className="hover:bg-zinc-50">
                            <td className="px-4 py-2 num"><Link to={`/automations/runs/${r.id}`} className="text-accent-600 hover:underline">{r.id}</Link></td>
                            <td className="px-4 py-2">{r.workflow_name ?? r.workflow_id} <span className="num text-xs text-zinc-400">v{r.workflow_version}</span></td>
                            <td className="px-4 py-2 flex items-center gap-1.5">
                                <StatusBadge status={r.status} />
                                {r.is_dry_run && <span className="px-1.5 py-0.5 text-[10px] font-bold border border-violet-200 bg-violet-50 text-violet-700">DRY RUN</span>}
                            </td>
                            <td className="px-4 py-2 num text-xs">
                                {r.case_id && <Link to={`/cases/${r.case_id}`} className="text-accent-600 hover:underline mr-2">case {r.case_id}</Link>}
                                {r.alert_id && <span className="text-zinc-500">alert {r.alert_id}</span>}
                            </td>
                            <td className="px-4 py-2 text-xs text-zinc-500">{r.started_at ? new Date(r.started_at).toLocaleString() : '—'}</td>
                            <td className="px-4 py-2 num text-xs">{duration(r)}</td>
                        </tr>
                    ))}
                </tbody>
            </table>
        </div>
    );
}
```

- [ ] **Step 5: List page**

`frontend/src/features/automations/AutomationsList.tsx`:
```tsx
import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Link, useNavigate } from 'react-router-dom';
import { Plus, Workflow as WorkflowIcon } from 'lucide-react';
import { api } from '../../api/client';
import { cn } from '../../lib/utils';
import type { User } from '../../types';
import type { WorkflowSummary } from './types';
import { TRIGGER_LABEL } from './nodeCatalog';
import RunsTable, { StatusBadge } from './RunsTable';

export default function AutomationsList() {
    const qc = useQueryClient();
    const navigate = useNavigate();
    const [tab, setTab] = useState<'workflows' | 'runs'>('workflows');
    const [toggleError, setToggleError] = useState<string | null>(null);

    const { data: me } = useQuery({ queryKey: ['currentUser'], queryFn: async () => (await api.get('/users/me')).data as User, staleTime: 300_000 });
    const isAdmin = me?.role === 'admin' || !!me?.is_super_admin;

    const { data: workflows = [], isLoading } = useQuery({
        queryKey: ['workflows'],
        queryFn: async () => (await api.get('/workflows/')).data as WorkflowSummary[],
    });

    const toggle = useMutation({
        mutationFn: async (w: WorkflowSummary) => api.post(`/workflows/${w.id}/${w.enabled ? 'disable' : 'enable'}`),
        onSuccess: () => { setToggleError(null); qc.invalidateQueries({ queryKey: ['workflows'] }); },
        onError: () => setToggleError('This workflow has validation errors — open it to fix them before enabling.'),
    });

    return (
        <div className="p-4 sm:p-6 max-w-[1400px] mx-auto">
            <div className="flex items-end justify-between mb-5 flex-wrap gap-3">
                <div>
                    <h1 className="text-xl font-semibold tracking-tight text-zinc-900">Automations</h1>
                    <p className="label-mono mt-1">workflows for cases and alerts</p>
                </div>
                {isAdmin && (
                    <button onClick={() => navigate('/automations/new')}
                        className="inline-flex items-center gap-1.5 h-9 px-3.5 bg-accent-600 text-white text-sm font-medium hover:bg-accent-700">
                        <Plus size={15} /> New workflow
                    </button>
                )}
            </div>

            <div className="flex gap-4 border-b border-zinc-200 mb-4">
                {(['workflows', 'runs'] as const).map((t) => (
                    <button key={t} onClick={() => setTab(t)}
                        className={cn('pb-2 text-sm capitalize', tab === t ? 'text-accent-700 border-b-2 border-accent-600' : 'text-zinc-500 hover:text-zinc-800')}>{t}</button>
                ))}
            </div>

            {toggleError && <div className="mb-3 px-3 py-2 text-sm border border-red-200 bg-red-50 text-red-700">{toggleError}</div>}

            {tab === 'runs' ? <RunsTable /> : (
                <div className="bg-white border border-zinc-200">
                    <table className="w-full text-sm">
                        <thead className="bg-zinc-50 text-left label-mono">
                            <tr><th className="px-4 py-2">Name</th><th className="px-4 py-2">Trigger</th><th className="px-4 py-2">Enabled</th>
                                <th className="px-4 py-2">Last run</th><th className="px-4 py-2">Runs</th></tr>
                        </thead>
                        <tbody className="divide-y divide-zinc-100">
                            {isLoading && <tr><td colSpan={5} className="px-4 py-8 text-center text-zinc-400">Loading…</td></tr>}
                            {!isLoading && workflows.length === 0 && (
                                <tr><td colSpan={5} className="px-4 py-12 text-center text-zinc-400">
                                    <WorkflowIcon className="mx-auto mb-2 opacity-50" />No workflows yet
                                </td></tr>
                            )}
                            {workflows.map((w) => (
                                <tr key={w.id} className="hover:bg-zinc-50">
                                    <td className="px-4 py-2.5">
                                        <Link to={`/automations/${w.id}`} className="font-medium text-zinc-900 hover:text-accent-700">{w.name}</Link>
                                        {w.description && <div className="text-xs text-zinc-500">{w.description}</div>}
                                    </td>
                                    <td className="px-4 py-2.5 font-mono text-xs text-zinc-600">{TRIGGER_LABEL[w.trigger_type]}</td>
                                    <td className="px-4 py-2.5">
                                        <button disabled={!isAdmin || toggle.isPending} onClick={() => toggle.mutate(w)}
                                            aria-label={w.enabled ? 'Disable workflow' : 'Enable workflow'}
                                            className={cn('w-9 h-5 relative transition-colors disabled:opacity-50', w.enabled ? 'bg-accent-600' : 'bg-zinc-300')}>
                                            <span className={cn('absolute top-0.5 w-4 h-4 bg-white transition-all', w.enabled ? 'left-[18px]' : 'left-0.5')} />
                                        </button>
                                    </td>
                                    <td className="px-4 py-2.5 text-xs text-zinc-500">
                                        {w.last_run_status ? <span className="flex items-center gap-2"><StatusBadge status={w.last_run_status} />
                                            {w.last_run_at && new Date(w.last_run_at).toLocaleString()}</span> : '—'}
                                    </td>
                                    <td className="px-4 py-2.5 num">{w.run_count}</td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            )}
        </div>
    );
}
```

- [ ] **Step 6: Route + sidebar**

`frontend/src/App.tsx`: import `AutomationsList` and add inside the `<Route path="/" element={<Layout />}>` block (next to `playbooks`):
```tsx
<Route path="automations" element={<AutomationsList />} />
```
`frontend/src/components/layout/Layout.tsx`: add `Workflow` to the lucide import and, after the Playbooks item in `baseSidebarItems`:
```ts
    { icon: Workflow, label: 'Automations', path: '/automations' },
```

- [ ] **Step 7: Verify**

Run (in `frontend/`): `npm run build` — expected: no TS errors.
Run: `docker compose up -d --build frontend`; open http://localhost/automations — sidebar shows Automations; the workflow created in Task 11 (if still present) is listed; toggling works; Runs tab lists runs with DRY RUN badges.

- [ ] **Step 8: Commit**
```bash
git add frontend/package.json frontend/package-lock.json frontend/src/features/automations frontend/src/App.tsx frontend/src/components/layout/Layout.tsx
git commit -m "feat(automations): list page, runs table, node catalog"
```

---

### Task 13: Workflow editor (canvas, palette, inspector, save/validate/enable)

**Files:**
- Create: `frontend/src/features/automations/flow.ts`, `frontend/src/features/automations/WorkflowNode.tsx`, `frontend/src/features/automations/NodeInspector.tsx`, `frontend/src/features/automations/WorkflowEditor.tsx`
- Modify: `frontend/src/App.tsx`

**Interfaces:**
- Consumes: types + `NODE_DEFS`/`NODE_DEF`/`TRIGGER_LABEL` (Task 12), `RunsTable` (Task 12).
- Produces:
  - `flow.ts`: `type WfFlowNode = Node<WfNodeData, 'wf'>`, `interface WfNodeData { wf: WfNode; status?: string; invalid?: boolean; [k: string]: unknown }`, `toFlow(graph: WfGraph, opts?: { statusByNode?: Record<string,string>; invalidIds?: Set<string> }) -> { nodes: WfFlowNode[]; edges: Edge[] }`, `fromFlow(nodes: WfFlowNode[], edges: Edge[]) -> WfGraph`, `nextNodeId(type: string, taken: Set<string>) -> string`.
  - `<WorkflowNode>` registered as node type `wf`.
  - `<NodeInspector node onChange(node) onRename(oldId,newId) onDelete readOnly />`.
  - `WorkflowEditor` default export; route `/automations/:id` (`id === 'new'` creates).

- [ ] **Step 1: Graph conversion**

`frontend/src/features/automations/flow.ts`:
```ts
import type { Edge, Node } from '@xyflow/react';
import { NODE_DEF } from './nodeCatalog';
import type { WfGraph, WfNode } from './types';

export interface WfNodeData { wf: WfNode; status?: string; invalid?: boolean; [k: string]: unknown }
export type WfFlowNode = Node<WfNodeData, 'wf'>;

const DEFAULT_CONTAINER = { width: 420, height: 260 };

export function toFlow(graph: WfGraph, opts: { statusByNode?: Record<string, string>; invalidIds?: Set<string> } = {}) {
    // React Flow requires parents before their children in the array.
    const sorted = [...graph.nodes].sort((a, b) => Number(!!a.parent_id) - Number(!!b.parent_id));
    const nodes: WfFlowNode[] = sorted.map((n) => {
        const container = NODE_DEF[n.type]?.container;
        return {
            id: n.id,
            type: 'wf',
            position: n.position ?? { x: 0, y: 0 },
            data: { wf: n, status: opts.statusByNode?.[n.id], invalid: opts.invalidIds?.has(n.id) },
            ...(n.parent_id ? { parentId: n.parent_id, extent: 'parent' as const } : {}),
            ...(container ? { style: { ...(n.size ?? DEFAULT_CONTAINER) }, zIndex: -1 } : {}),
        };
    });
    const edges: Edge[] = graph.edges.map((e) => ({
        id: e.id, source: e.source, target: e.target,
        sourceHandle: e.source_handle ?? undefined,
        label: e.source_handle ?? undefined,
    }));
    return { nodes, edges };
}

export function fromFlow(nodes: WfFlowNode[], edges: Edge[]): WfGraph {
    return {
        nodes: nodes.map((n) => {
            const container = NODE_DEF[n.data.wf.type]?.container;
            const size = container
                ? { width: Math.round(n.measured?.width ?? Number(n.style?.width ?? DEFAULT_CONTAINER.width)),
                    height: Math.round(n.measured?.height ?? Number(n.style?.height ?? DEFAULT_CONTAINER.height)) }
                : undefined;
            return { ...n.data.wf, id: n.id, position: n.position, parent_id: n.parentId ?? null, ...(size ? { size } : {}) };
        }),
        edges: edges.map((e) => ({
            id: e.id, source: e.source, target: e.target,
            source_handle: (e.sourceHandle === 'true' || e.sourceHandle === 'false') ? e.sourceHandle : null,
        })),
    };
}

export function nextNodeId(type: string, taken: Set<string>): string {
    const base = type.replace(/[^a-z0-9_]/g, '_');
    let i = 1;
    while (taken.has(`${base}_${i}`)) i++;
    return `${base}_${i}`;
}
```

- [ ] **Step 2: Custom node**

`frontend/src/features/automations/WorkflowNode.tsx`:
```tsx
import { Handle, Position, type NodeProps } from '@xyflow/react';
import { cn } from '../../lib/utils';
import { NODE_DEF } from './nodeCatalog';
import type { WfFlowNode } from './flow';

const RING: Record<string, string> = {
    succeeded: 'border-emerald-500', failed: 'border-red-500', skipped: 'border-zinc-300 opacity-60',
    waiting: 'border-amber-500', running: 'border-accent-500', pending: 'border-accent-300', cancelled: 'border-zinc-400',
};

export default function WorkflowNode({ id, data, selected }: NodeProps<WfFlowNode>) {
    const def = NODE_DEF[data.wf.type];
    const Icon = def?.icon;
    const isTrigger = data.wf.type === 'trigger';
    return (
        <div className={cn('bg-white border-2 min-w-[180px] shadow-sm',
            data.status ? RING[data.status] : 'border-zinc-300',
            data.invalid && 'border-red-500', selected && 'ring-2 ring-accent-400')}>
            {!isTrigger && <Handle type="target" position={Position.Top} className="!bg-zinc-500 !w-2.5 !h-2.5 !rounded-none" />}
            <div className="flex items-center gap-2 px-3 py-2 border-b border-zinc-100">
                {Icon && <Icon size={14} className="text-accent-600 shrink-0" />}
                <span className="text-sm font-medium text-zinc-900">{def?.label ?? data.wf.type}</span>
            </div>
            <div className="px-3 py-1 flex items-center justify-between gap-2">
                <span className="font-mono text-[10px] text-zinc-500">{id}</span>
                {data.status && <span className="font-mono text-[10px] uppercase text-zinc-500">{data.status}</span>}
            </div>
            {def?.branching ? (
                <>
                    <Handle type="source" id="true" position={Position.Bottom} style={{ left: '30%' }} className="!bg-emerald-600 !w-2.5 !h-2.5 !rounded-none" />
                    <Handle type="source" id="false" position={Position.Bottom} style={{ left: '70%' }} className="!bg-red-600 !w-2.5 !h-2.5 !rounded-none" />
                    <div className="flex justify-between px-6 pb-1 font-mono text-[9px] text-zinc-400"><span>true</span><span>false</span></div>
                </>
            ) : (
                <Handle type="source" position={Position.Bottom} className="!bg-zinc-500 !w-2.5 !h-2.5 !rounded-none" />
            )}
        </div>
    );
}
```

- [ ] **Step 3: Inspector**

`frontend/src/features/automations/NodeInspector.tsx`:
```tsx
import { useEffect, useState } from 'react';
import { Trash2 } from 'lucide-react';
import { NODE_DEF, type FieldDef } from './nodeCatalog';
import type { WfNode } from './types';

interface Props {
    node: WfNode;
    readOnly: boolean;
    errors: string[];
    onChange: (node: WfNode) => void;
    onRename: (oldId: string, newId: string) => void;
    onDelete: () => void;
}

const inputCls = 'w-full border border-zinc-300 px-2 py-1.5 text-sm bg-white focus:outline-none focus:border-accent-500 disabled:bg-zinc-50';
const monoCls = inputCls + ' font-mono text-xs';

function JsonField({ value, disabled, onChange }: { value: unknown; disabled: boolean; onChange: (v: unknown) => void }) {
    const [text, setText] = useState(value === undefined || value === null ? '' : typeof value === 'string' ? value : JSON.stringify(value, null, 2));
    const [err, setErr] = useState<string | null>(null);
    return (
        <>
            <textarea rows={4} className={monoCls} disabled={disabled} value={text} onChange={(e) => setText(e.target.value)}
                onBlur={() => {
                    if (!text.trim()) { setErr(null); onChange(undefined); return; }
                    try { onChange(JSON.parse(text)); setErr(null); }
                    catch { onChange(text); setErr('Not valid JSON — saved as a text template'); }
                }} />
            {err && <p className="text-[11px] text-amber-700 mt-0.5">{err}</p>}
        </>
    );
}

function Field({ f, value, disabled, onChange }: { f: FieldDef; value: unknown; disabled: boolean; onChange: (v: unknown) => void }) {
    const str = value === undefined || value === null ? '' : String(value);
    switch (f.kind) {
        case 'select':
            return <select className={inputCls} disabled={disabled} value={str} onChange={(e) => onChange(e.target.value || undefined)}>
                {(f.options ?? []).map((o) => <option key={o} value={o}>{o || '—'}</option>)}
            </select>;
        case 'textarea':
            return <textarea rows={4} className={monoCls} disabled={disabled} value={str} placeholder={f.placeholder} onChange={(e) => onChange(e.target.value)} />;
        case 'json':
            return <JsonField value={value} disabled={disabled} onChange={onChange} />;
        default:
            return <input className={f.kind === 'number' ? inputCls : monoCls} disabled={disabled} value={str} placeholder={f.placeholder}
                onChange={(e) => onChange(e.target.value === '' ? undefined : e.target.value)} />;
    }
}

export default function NodeInspector({ node, readOnly, errors, onChange, onRename, onDelete }: Props) {
    const def = NODE_DEF[node.type];
    const [idDraft, setIdDraft] = useState(node.id);
    useEffect(() => setIdDraft(node.id), [node.id]);
    const setCfg = (key: string, v: unknown) => {
        const config = { ...node.config };
        if (v === undefined) delete config[key]; else config[key] = v;
        onChange({ ...node, config });
    };

    return (
        <div className="p-4 space-y-4 text-sm" key={node.id}>
            <div className="flex items-center justify-between">
                <span className="font-semibold text-zinc-900">{def?.label ?? node.type}</span>
                {!readOnly && node.type !== 'trigger' && (
                    <button onClick={onDelete} className="p-1 text-zinc-400 hover:text-red-600" aria-label="Delete node"><Trash2 size={15} /></button>
                )}
            </div>
            {errors.length > 0 && <ul className="text-xs text-red-700 bg-red-50 border border-red-200 p-2 space-y-0.5">{errors.map((e, i) => <li key={i}>{e}</li>)}</ul>}

            <label className="block">
                <span className="label-mono">node id</span>
                <input className={monoCls} disabled={readOnly} value={idDraft} onChange={(e) => setIdDraft(e.target.value)}
                    onBlur={() => { if (idDraft !== node.id && /^[a-z0-9_]+$/.test(idDraft)) onRename(node.id, idDraft); else setIdDraft(node.id); }} />
                <span className="text-[11px] text-zinc-500">Reference outputs as <code className="font-mono">{`{{ steps.${node.id}.output }}`}</code></span>
            </label>

            {def?.fields.map((f) => (
                <label key={f.key} className="block">
                    <span className="label-mono">{f.label}{f.required && ' *'}</span>
                    <Field f={f} value={node.config[f.key]} disabled={readOnly} onChange={(v) => setCfg(f.key, v)} />
                    {f.help && <span className="text-[11px] text-zinc-500">{f.help}</span>}
                </label>
            ))}

            {node.type !== 'trigger' && (
                <div className="border-t border-zinc-200 pt-3 space-y-2">
                    <label className="flex items-center gap-2 text-xs text-zinc-700">
                        <input type="checkbox" disabled={readOnly} checked={!!node.continue_on_error}
                            onChange={(e) => onChange({ ...node, continue_on_error: e.target.checked })} /> Continue on error (output.error is set)
                    </label>
                    {node.type === 'http_request' && (
                        <>
                            <label className="block"><span className="label-mono">retries (0-2)</span>
                                <input type="number" min={0} max={2} className={inputCls} disabled={readOnly} value={node.retries ?? 2}
                                    onChange={(e) => onChange({ ...node, retries: Number(e.target.value) })} /></label>
                            <label className="flex items-center gap-2 text-xs text-zinc-700">
                                <input type="checkbox" disabled={readOnly} checked={!!node.execute_in_dry_run}
                                    onChange={(e) => onChange({ ...node, execute_in_dry_run: e.target.checked })} /> Really execute during dry runs (read-only lookups only)
                            </label>
                        </>
                    )}
                    {def?.sideEffect && (
                        <label className="block"><span className="label-mono">dry-run mock output (JSON)</span>
                            <JsonField value={node.mock_output} disabled={readOnly}
                                onChange={(v) => onChange({ ...node, mock_output: (v && typeof v === 'object') ? v as Record<string, unknown> : null })} />
                        </label>
                    )}
                </div>
            )}

            <div className="border-t border-zinc-200 pt-3 text-[11px] text-zinc-500 space-y-0.5">
                <p className="label-mono">available in templates</p>
                <p className="font-mono">case.* · alert.* · trigger.* · steps.&lt;id&gt;.output · loop.item · loop.index · dry_run</p>
            </div>
        </div>
    );
}
```

- [ ] **Step 4: Editor**

`frontend/src/features/automations/WorkflowEditor.tsx`:
```tsx
import { useCallback, useEffect, useMemo, useState, type DragEvent } from 'react';
import { useNavigate, useParams, Link } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
    ReactFlow, ReactFlowProvider, Background, Controls, MiniMap, addEdge, useEdgesState, useNodesState, useReactFlow,
    type Connection, type Edge,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import { ArrowLeft, Save, CheckCircle2, AlertTriangle } from 'lucide-react';
import { api } from '../../api/client';
import { cn } from '../../lib/utils';
import type { User } from '../../types';
import type { TriggerType, ValidationError, Workflow, WfGraph, WfNode } from './types';
import { NODE_DEFS, NODE_DEF, TRIGGER_LABEL } from './nodeCatalog';
import { fromFlow, nextNodeId, toFlow, type WfFlowNode } from './flow';
import WorkflowNode from './WorkflowNode';
import NodeInspector from './NodeInspector';
import RunsTable from './RunsTable';

const nodeTypes = { wf: WorkflowNode };
const EMPTY_GRAPH: WfGraph = { nodes: [{ id: 'start', type: 'trigger', position: { x: 250, y: 40 }, config: {} }], edges: [] };

interface Meta { name: string; description: string; trigger_type: TriggerType; trigger_filter: string }

function Editor() {
    const { id } = useParams();
    const isNew = id === 'new';
    const navigate = useNavigate();
    const qc = useQueryClient();
    const { screenToFlowPosition } = useReactFlow();

    const { data: me } = useQuery({ queryKey: ['currentUser'], queryFn: async () => (await api.get('/users/me')).data as User, staleTime: 300_000 });
    const readOnly = !(me?.role === 'admin' || me?.is_super_admin);

    const { data: wf } = useQuery({
        queryKey: ['workflow', id],
        queryFn: async () => (await api.get(`/workflows/${id}`)).data as Workflow,
        enabled: !isNew,
    });

    const [meta, setMeta] = useState<Meta>({ name: 'Untitled workflow', description: '', trigger_type: 'case.created', trigger_filter: '' });
    const [nodes, setNodes, onNodesChange] = useNodesState<WfFlowNode>([]);
    const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);
    const [selectedId, setSelectedId] = useState<string | null>(null);
    const [errors, setErrors] = useState<ValidationError[]>([]);
    const [tab, setTab] = useState<'editor' | 'runs'>('editor');
    const [banner, setBanner] = useState<string | null>(null);
    const [loadedVersion, setLoadedVersion] = useState<number | null>(null);

    useEffect(() => {
        if (isNew && loadedVersion === null) {
            const f = toFlow(EMPTY_GRAPH); setNodes(f.nodes); setEdges(f.edges); setLoadedVersion(0);
        }
        if (wf && wf.version !== loadedVersion) {
            setMeta({ name: wf.name, description: wf.description ?? '', trigger_type: wf.trigger_type, trigger_filter: wf.trigger_filter ?? '' });
            const invalid = new Set(wf.validation_errors.map((e) => e.node_id).filter(Boolean) as string[]);
            const f = toFlow(wf.graph, { invalidIds: invalid });
            setNodes(f.nodes); setEdges(f.edges); setErrors(wf.validation_errors); setLoadedVersion(wf.version);
        }
    }, [wf, isNew, loadedVersion, setNodes, setEdges]);

    const onConnect = useCallback((c: Connection) => setEdges((eds) => addEdge({ ...c, id: `e_${c.source}_${c.target}_${c.sourceHandle ?? 'out'}`, label: c.sourceHandle ?? undefined }, eds)), [setEdges]);

    const addNode = useCallback((type: string, flowPos: { x: number; y: number }) => {
        setNodes((ns) => {
            const taken = new Set(ns.map((n) => n.id));
            const nid = nextNodeId(type, taken);
            const wfNode: WfNode = { id: nid, type, position: flowPos, config: type === 'http_request' ? { method: 'GET' } : {} };
            return [...ns, ...toFlow({ nodes: [wfNode], edges: [] }).nodes];
        });
    }, [setNodes]);

    const onDrop = useCallback((e: DragEvent) => {
        e.preventDefault();
        const type = e.dataTransfer.getData('application/wf-node');
        if (!type) return;
        addNode(type, screenToFlowPosition({ x: e.clientX, y: e.clientY }));
    }, [addNode, screenToFlowPosition]);

    const selected = nodes.find((n) => n.id === selectedId)?.data.wf;
    const selectedErrors = errors.filter((e) => e.node_id === selectedId).map((e) => e.message);
    const graphErrors = errors.filter((e) => !e.node_id);

    const updateNode = (updated: WfNode) =>
        setNodes((ns) => ns.map((n) => (n.id === updated.id ? { ...n, data: { ...n.data, wf: updated } } : n)));
    const renameNode = (oldId: string, newId: string) => {
        if (nodes.some((n) => n.id === newId)) { setBanner(`Node id "${newId}" already exists`); return; }
        setNodes((ns) => ns.map((n) => {
            if (n.id === oldId) return { ...n, id: newId, data: { ...n.data, wf: { ...n.data.wf, id: newId } } };
            if (n.parentId === oldId) return { ...n, parentId: newId };
            return n;
        }));
        setEdges((es) => es.map((e) => ({ ...e, source: e.source === oldId ? newId : e.source, target: e.target === oldId ? newId : e.target })));
        setSelectedId(newId);
    };
    const deleteNode = (nid: string) => {
        setNodes((ns) => ns.filter((n) => n.id !== nid && n.parentId !== nid));
        setEdges((es) => es.filter((e) => e.source !== nid && e.target !== nid));
        setSelectedId(null);
    };

    const save = useMutation({
        mutationFn: async () => {
            const body = { ...meta, trigger_filter: meta.trigger_filter || null, description: meta.description || null, graph: fromFlow(nodes, edges) };
            return (isNew ? await api.post('/workflows/', body) : await api.put(`/workflows/${id}`, body)).data as Workflow;
        },
        onSuccess: (saved) => {
            setErrors(saved.validation_errors);
            setBanner(saved.validation_errors.length ? `Saved with ${saved.validation_errors.length} problem(s)` : 'Saved');
            qc.invalidateQueries({ queryKey: ['workflows'] });
            qc.setQueryData(['workflow', String(saved.id)], saved);
            if (isNew) navigate(`/automations/${saved.id}`, { replace: true });
        },
        onError: (err: any) => {
            const detail = err?.response?.data?.detail;
            if (detail?.errors) setErrors(detail.errors);
            setBanner(detail?.message ?? 'Save failed');
        },
    });

    const toggle = useMutation({
        mutationFn: async () => (await api.post(`/workflows/${id}/${wf?.enabled ? 'disable' : 'enable'}`)).data as Workflow,
        onSuccess: (w) => { qc.setQueryData(['workflow', id], w); qc.invalidateQueries({ queryKey: ['workflows'] }); setBanner(w.enabled ? 'Enabled' : 'Disabled'); },
        onError: (err: any) => { setErrors(err?.response?.data?.detail?.errors ?? []); setBanner('Fix the problems below before enabling'); },
    });

    const invalidIds = useMemo(() => new Set(errors.map((e) => e.node_id).filter(Boolean) as string[]), [errors]);
    const displayNodes = useMemo(() => nodes.map((n) => ({ ...n, data: { ...n.data, invalid: invalidIds.has(n.id) } })), [nodes, invalidIds]);

    const palette = NODE_DEFS.filter((d) => d.type !== 'trigger' && (!d.alertOnly || meta.trigger_type === 'alert.ingested'));

    return (
        <div className="flex flex-col h-[calc(100vh-56px)]">
            <div className="flex items-center gap-3 px-4 py-2 border-b border-zinc-200 bg-white flex-wrap">
                <Link to="/automations" className="p-1 text-zinc-500 hover:text-zinc-900" aria-label="Back"><ArrowLeft size={18} /></Link>
                <input className="text-base font-semibold text-zinc-900 border-b border-transparent focus:border-accent-500 outline-none min-w-[220px]"
                    disabled={readOnly} value={meta.name} onChange={(e) => setMeta({ ...meta, name: e.target.value })} aria-label="Workflow name" />
                {wf && <span className="num text-xs text-zinc-400">v{wf.version}</span>}
                <div className="flex gap-3 ml-2">
                    {(['editor', 'runs'] as const).map((t) => (
                        <button key={t} disabled={isNew && t === 'runs'} onClick={() => setTab(t)}
                            className={cn('text-sm capitalize', tab === t ? 'text-accent-700 font-medium' : 'text-zinc-500')}>{t}</button>
                    ))}
                </div>
                <div className="ml-auto flex items-center gap-2">
                    {banner && <span className="text-xs text-zinc-600">{banner}</span>}
                    {!readOnly && (
                        <button onClick={() => save.mutate()} disabled={save.isPending}
                            className="inline-flex items-center gap-1.5 h-8 px-3 bg-accent-600 text-white text-sm hover:bg-accent-700 disabled:opacity-50">
                            <Save size={14} /> Save
                        </button>
                    )}
                    {!readOnly && !isNew && (
                        <button onClick={() => toggle.mutate()} disabled={toggle.isPending}
                            className={cn('h-8 px-3 text-sm border', wf?.enabled ? 'border-emerald-300 text-emerald-700 bg-emerald-50' : 'border-zinc-300 text-zinc-700')}>
                            {wf?.enabled ? 'Enabled' : 'Disabled'}
                        </button>
                    )}
                </div>
            </div>

            {tab === 'runs' && wf ? <div className="p-4 overflow-auto"><RunsTable workflowId={wf.id} /></div> : (
                <div className="flex flex-1 min-h-0">
                    <aside className="w-56 border-r border-zinc-200 bg-white overflow-y-auto p-3 space-y-4">
                        <div className="space-y-2">
                            <p className="label-mono">trigger</p>
                            <select className="w-full border border-zinc-300 px-2 py-1.5 text-sm" disabled={readOnly} value={meta.trigger_type}
                                onChange={(e) => setMeta({ ...meta, trigger_type: e.target.value as TriggerType })}>
                                {Object.entries(TRIGGER_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
                            </select>
                            <textarea rows={2} className="w-full border border-zinc-300 px-2 py-1.5 font-mono text-xs" disabled={readOnly}
                                placeholder="filter, e.g. 'x' in case.tags" value={meta.trigger_filter}
                                onChange={(e) => setMeta({ ...meta, trigger_filter: e.target.value })} />
                        </div>
                        {!readOnly && (['Logic', 'Cases', 'Alerts', 'HTTP', 'Slack'] as const).map((cat) => {
                            const items = palette.filter((d) => d.category === cat);
                            if (!items.length) return null;
                            return (
                                <div key={cat}>
                                    <p className="label-mono mb-1">{cat}</p>
                                    {items.map((d) => (
                                        <div key={d.type} draggable onDragStart={(e) => { e.dataTransfer.setData('application/wf-node', d.type); e.dataTransfer.effectAllowed = 'move'; }}
                                            onClick={() => addNode(d.type, screenToFlowPosition({ x: window.innerWidth / 2, y: window.innerHeight / 2 }))}
                                            className="flex items-center gap-2 px-2 py-1.5 mb-1 border border-zinc-200 text-sm cursor-grab hover:border-accent-400 bg-zinc-50">
                                            <d.icon size={14} className="text-accent-600" />{d.label}
                                        </div>
                                    ))}
                                </div>
                            );
                        })}
                    </aside>

                    <div className="flex-1 relative" onDragOver={(e) => { e.preventDefault(); e.dataTransfer.dropEffect = 'move'; }} onDrop={onDrop}>
                        {graphErrors.length > 0 && (
                            <div className="absolute z-10 top-2 left-2 right-2 bg-red-50 border border-red-200 text-red-700 text-xs p-2">
                                <AlertTriangle size={12} className="inline mr-1" />{graphErrors.map((e) => e.message).join(' · ')}
                            </div>
                        )}
                        {errors.length === 0 && loadedVersion !== null && loadedVersion > 0 && (
                            <div className="absolute z-10 top-2 right-2 text-xs text-emerald-700 flex items-center gap-1"><CheckCircle2 size={12} />valid</div>
                        )}
                        <ReactFlow nodes={displayNodes} edges={edges} nodeTypes={nodeTypes}
                            onNodesChange={readOnly ? undefined : onNodesChange} onEdgesChange={readOnly ? undefined : onEdgesChange}
                            onConnect={readOnly ? undefined : onConnect}
                            onNodeClick={(_, n) => setSelectedId(n.id)} onPaneClick={() => setSelectedId(null)}
                            nodesDraggable={!readOnly} nodesConnectable={!readOnly} deleteKeyCode={null} fitView>
                            <Background gap={16} color="#e4e4e7" />
                            <Controls />
                            <MiniMap pannable zoomable />
                        </ReactFlow>
                    </div>

                    <aside className="w-80 border-l border-zinc-200 bg-white overflow-y-auto">
                        {selected ? (
                            <NodeInspector node={selected} readOnly={readOnly} errors={selectedErrors}
                                onChange={updateNode} onRename={renameNode} onDelete={() => deleteNode(selected.id)} />
                        ) : (
                            <div className="p-4 text-sm text-zinc-500 space-y-3">
                                <p>Select a node to configure it. Drag nodes from the left palette; connect handles to build the flow.</p>
                                <label className="block"><span className="label-mono">description</span>
                                    <textarea rows={3} className="w-full border border-zinc-300 px-2 py-1.5 text-sm" disabled={readOnly}
                                        value={meta.description} onChange={(e) => setMeta({ ...meta, description: e.target.value })} /></label>
                            </div>
                        )}
                    </aside>
                </div>
            )}
        </div>
    );
}

export default function WorkflowEditor() {
    return <ReactFlowProvider><Editor /></ReactFlowProvider>;
}
```

- [ ] **Step 5: Route**

`frontend/src/App.tsx`: import `WorkflowEditor`, add after the `automations` route:
```tsx
<Route path="automations/:id" element={<WorkflowEditor />} />
```

- [ ] **Step 6: Verify**

`npm run build` — no errors. Rebuild frontend container. As admin:
1. Automations → New workflow: canvas shows the `start` trigger node.
2. Drag *Condition* and *Add note* in, connect start → condition, condition **true** handle → note. Set expression `'x' in case.tags`, note content `hello {{ case.title }}`. Save → URL becomes `/automations/<id>`, banner "Saved", "valid".
3. Delete the condition's expression, Save → condition node turns red; selecting it lists "missing required field 'expression'". Enable button → banner "Fix the problems…". Restore the expression, Save, Enable → "Enabled".
4. Rename node `add_note_1` → `greet`; the edge stays connected after save + reload.
5. Change trigger to *Alert ingested* → the palette shows the Alerts category; switch back → hidden.
6. As an analyst: the editor opens read-only (no palette, no Save).

- [ ] **Step 7: Commit**
```bash
git add frontend/src/features/automations frontend/src/App.tsx
git commit -m "feat(automations): visual workflow editor"
```

---

### Task 14: Dry-run dialog + run detail view

**Files:**
- Create: `frontend/src/features/automations/DryRunDialog.tsx`, `frontend/src/features/automations/RunDetail.tsx`
- Modify: `frontend/src/features/automations/WorkflowEditor.tsx` (Dry run button), `frontend/src/App.tsx` (route)

**Interfaces:**
- Consumes: `toFlow` (Task 13), `WorkflowNode` (Task 13), `StatusBadge` (Task 12), `NODE_DEF` (Task 12), API `/workflows/{id}/dry-run`, `/workflow-runs/{id}`, `/workflow-runs/{id}/cancel`.
- Produces: `<DryRunDialog workflow onClose />`; `RunDetail` page at `/automations/runs/:runId` (Task 20 extends it with child runs).

- [ ] **Step 1: Dry-run dialog**

`frontend/src/features/automations/DryRunDialog.tsx`:
```tsx
import { useState } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import { X, FlaskConical } from 'lucide-react';
import { api } from '../../api/client';
import { NODE_DEF } from './nodeCatalog';
import type { Workflow } from './types';

type Source = 'case' | 'alert' | 'payload';

export default function DryRunDialog({ workflow, onClose }: { workflow: Workflow; onClose: () => void }) {
    const navigate = useNavigate();
    const [source, setSource] = useState<Source>(workflow.trigger_type === 'alert.ingested' ? 'alert' : 'case');
    const [caseId, setCaseId] = useState('');
    const [alertId, setAlertId] = useState('');
    const [payload, setPayload] = useState('{\n  "event": "manual"\n}');
    const [mocks, setMocks] = useState<Record<string, string>>({});
    const [answers, setAnswers] = useState<Record<string, string>>({});
    const [error, setError] = useState<string | null>(null);

    const { data: cases = [] } = useQuery({ queryKey: ['cases', 'dry-run-picker'], queryFn: async () => (await api.get('/cases/')).data as { id: number; title: string }[], enabled: source === 'case' });
    const { data: alerts = [] } = useQuery({ queryKey: ['alerts', 'dry-run-picker'], queryFn: async () => (await api.get('/alerts/')).data as { id: number; title: string; status: string }[], enabled: source === 'alert' });

    const sideEffectNodes = workflow.graph.nodes.filter((n) => NODE_DEF[n.type]?.sideEffect && n.type !== 'slack_ask_user');
    const askNodes = workflow.graph.nodes.filter((n) => n.type === 'slack_ask_user');

    const run = useMutation({
        mutationFn: async () => {
            const body: Record<string, unknown> = { mocks: {}, ask_user_answers: answers };
            for (const [nid, text] of Object.entries(mocks)) {
                if (text.trim()) (body.mocks as Record<string, unknown>)[nid] = JSON.parse(text);
            }
            if (source === 'case') body.case_id = Number(caseId);
            if (source === 'alert') body.alert_id = Number(alertId);
            if (source === 'payload') body.payload = JSON.parse(payload);
            return (await api.post(`/workflows/${workflow.id}/dry-run`, body)).data as { run_id: number };
        },
        onSuccess: ({ run_id }) => navigate(`/automations/runs/${run_id}`),
        onError: (err: any) => setError(err instanceof SyntaxError ? `Invalid JSON: ${err.message}` :
            err?.response?.data?.detail?.errors ? 'Workflow has validation errors — fix them first' :
            err?.response?.data?.detail ?? 'Dry run failed'),
    });

    const ready = (source === 'case' && caseId) || (source === 'alert' && alertId) || source === 'payload';
    const input = 'w-full border border-zinc-300 px-2 py-1.5 text-sm bg-white';

    return (
        <div className="fixed inset-0 z-50 bg-zinc-900/40 flex items-center justify-center p-4" role="dialog" aria-modal="true" aria-label="Dry run">
            <div className="bg-white border border-zinc-200 w-full max-w-lg max-h-[90vh] overflow-y-auto">
                <div className="flex items-center justify-between px-4 py-3 border-b border-zinc-200">
                    <h2 className="font-semibold text-zinc-900 flex items-center gap-2"><FlaskConical size={16} />Dry run “{workflow.name}”</h2>
                    <button onClick={onClose} aria-label="Close"><X size={16} /></button>
                </div>
                <div className="p-4 space-y-4 text-sm">
                    <p className="text-zinc-600">Conditions, loops and case search run for real. Everything else is simulated — nothing is changed, sent or called.</p>
                    <div className="flex gap-4">
                        {(['case', 'alert', 'payload'] as const).map((s) => (
                            <label key={s} className="flex items-center gap-1.5 capitalize">
                                <input type="radio" name="src" checked={source === s} onChange={() => setSource(s)} />{s === 'payload' ? 'JSON payload' : s}
                            </label>
                        ))}
                    </div>
                    {source === 'case' && <select className={input} value={caseId} onChange={(e) => setCaseId(e.target.value)}>
                        <option value="">Select a case…</option>
                        {cases.map((c) => <option key={c.id} value={c.id}>#{c.id} {c.title}</option>)}
                    </select>}
                    {source === 'alert' && <select className={input} value={alertId} onChange={(e) => setAlertId(e.target.value)}>
                        <option value="">Select an alert…</option>
                        {alerts.map((a) => <option key={a.id} value={a.id}>#{a.id} {a.title} ({a.status})</option>)}
                    </select>}
                    {source === 'payload' && <textarea rows={6} className={input + ' font-mono text-xs'} value={payload} onChange={(e) => setPayload(e.target.value)} />}

                    {askNodes.length > 0 && (
                        <div className="space-y-2">
                            <p className="label-mono">slack answers</p>
                            {askNodes.map((n) => {
                                const raw = n.config.buttons;
                                const buttons = Array.isArray(raw) ? (raw as string[])
                                    : typeof raw === 'string' && raw.trim() ? raw.split(',').map((x) => x.trim()).filter(Boolean) : ['Yes', 'No'];
                                return (
                                    <label key={n.id} className="flex items-center gap-2">
                                        <span className="font-mono text-xs w-32 truncate">{n.id}</span>
                                        <select className={input} value={answers[n.id] ?? ''} onChange={(e) => setAnswers({ ...answers, [n.id]: e.target.value })}>
                                            <option value="">{buttons[0]} (default)</option>
                                            {buttons.slice(1).map((b) => <option key={b} value={b}>{b}</option>)}
                                            <option value="timeout">— times out —</option>
                                        </select>
                                    </label>
                                );
                            })}
                        </div>
                    )}

                    {sideEffectNodes.length > 0 && (
                        <details>
                            <summary className="label-mono cursor-pointer">mock outputs (optional)</summary>
                            <div className="mt-2 space-y-2">
                                {sideEffectNodes.map((n) => (
                                    <label key={n.id} className="block">
                                        <span className="font-mono text-xs">{n.id}</span>
                                        <textarea rows={2} className={input + ' font-mono text-xs'} placeholder='{"status": 200, "body": {}}'
                                            value={mocks[n.id] ?? ''} onChange={(e) => setMocks({ ...mocks, [n.id]: e.target.value })} />
                                    </label>
                                ))}
                            </div>
                        </details>
                    )}
                    {error && <p className="text-red-700 text-xs">{error}</p>}
                </div>
                <div className="flex justify-end gap-2 px-4 py-3 border-t border-zinc-200">
                    <button onClick={onClose} className="h-8 px-3 border border-zinc-300 text-sm">Cancel</button>
                    <button disabled={!ready || run.isPending} onClick={() => { setError(null); run.mutate(); }}
                        className="h-8 px-3 bg-violet-600 text-white text-sm hover:bg-violet-700 disabled:opacity-50">Start dry run</button>
                </div>
            </div>
        </div>
    );
}
```

- [ ] **Step 2: Run detail page**

`frontend/src/features/automations/RunDetail.tsx`:
```tsx
import { useMemo, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ReactFlow, ReactFlowProvider, Background, Controls } from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import { ArrowLeft } from 'lucide-react';
import { api } from '../../api/client';
import type { RunDetail as Run, RunStep } from './types';
import { toFlow } from './flow';
import WorkflowNode from './WorkflowNode';
import { StatusBadge } from './RunsTable';

const nodeTypes = { wf: WorkflowNode };
const LIVE = ['running', 'waiting', 'queued'];

function Json({ value }: { value: unknown }) {
    return <pre className="text-[11px] font-mono bg-zinc-50 border border-zinc-200 p-2 overflow-x-auto whitespace-pre-wrap break-all">{JSON.stringify(value, null, 2)}</pre>;
}

export function StepPanel({ step }: { step: RunStep }) {
    const out = step.output ?? {};
    const simulated = (out as Record<string, unknown>).simulated === true;
    return (
        <div className="p-4 space-y-3 text-sm">
            <div className="flex items-center gap-2">
                <span className="font-mono text-zinc-900">{step.node_id}</span>
                <StatusBadge status={step.status} />
                {simulated && <span className="px-1.5 py-0.5 text-[10px] font-bold border border-violet-200 bg-violet-50 text-violet-700">SIMULATED</span>}
            </div>
            {step.attempt > 0 && <p className="text-xs text-zinc-500">retried {step.attempt}×</p>}
            {step.error && <p className="text-xs text-red-700 bg-red-50 border border-red-200 p-2">{step.error}</p>}
            {simulated ? (
                <><p className="label-mono">would do</p><Json value={(out as Record<string, unknown>).would_do} /></>
            ) : (
                <><p className="label-mono">input</p><Json value={step.input} /></>
            )}
            <p className="label-mono">output</p><Json value={out} />
        </div>
    );
}

function Detail() {
    const { runId } = useParams();
    const qc = useQueryClient();
    const [selected, setSelected] = useState<string | null>(null);

    const { data: run } = useQuery({
        queryKey: ['workflow-run', runId],
        queryFn: async () => (await api.get(`/workflow-runs/${runId}`)).data as Run,
        refetchInterval: (q) => (LIVE.includes((q.state.data as Run | undefined)?.status ?? '') ? 2000 : false),
    });

    const cancel = useMutation({
        mutationFn: async () => api.post(`/workflow-runs/${runId}/cancel`),
        onSuccess: () => qc.invalidateQueries({ queryKey: ['workflow-run', runId] }),
    });

    const flow = useMemo(() => {
        if (!run) return { nodes: [], edges: [] };
        const statusByNode = Object.fromEntries(run.steps.map((s) => [s.node_id, s.status]));
        return toFlow(run.graph_snapshot, { statusByNode });
    }, [run]);

    if (!run) return <div className="p-6 text-zinc-500">Loading…</div>;
    const step = run.steps.find((s) => s.node_id === selected);

    return (
        <div className="flex flex-col h-[calc(100vh-56px)]">
            <div className="flex items-center gap-3 px-4 py-2 border-b border-zinc-200 bg-white flex-wrap">
                <Link to={run.parent_run_id ? `/automations/runs/${run.parent_run_id}` : `/automations/${run.workflow_id}`} className="p-1 text-zinc-500 hover:text-zinc-900" aria-label="Back"><ArrowLeft size={18} /></Link>
                <span className="font-semibold text-zinc-900">{run.workflow_name}</span>
                <span className="num text-xs text-zinc-400">run {run.id} · v{run.workflow_version}{run.loop_index !== null && ` · item ${run.loop_index}`}</span>
                <StatusBadge status={run.status} />
                {run.is_dry_run && <span className="px-1.5 py-0.5 text-[10px] font-bold border border-violet-200 bg-violet-50 text-violet-700">DRY RUN</span>}
                {run.case_id && <Link to={`/cases/${run.case_id}`} className="text-xs text-accent-600 hover:underline">case {run.case_id}</Link>}
                {run.alert_id && <span className="text-xs text-zinc-500">alert {run.alert_id}</span>}
                {run.error && <span className="text-xs text-red-700">{run.error}</span>}
                {LIVE.includes(run.status) && (
                    <button onClick={() => cancel.mutate()} className="ml-auto h-8 px-3 border border-red-300 text-red-700 text-sm hover:bg-red-50">Cancel run</button>
                )}
            </div>
            <div className="flex flex-1 min-h-0">
                <div className="flex-1">
                    <ReactFlow nodes={flow.nodes} edges={flow.edges} nodeTypes={nodeTypes} nodesDraggable={false} nodesConnectable={false}
                        onNodeClick={(_, n) => setSelected(n.id)} fitView>
                        <Background gap={16} color="#e4e4e7" />
                        <Controls showInteractive={false} />
                    </ReactFlow>
                </div>
                <aside className="w-96 border-l border-zinc-200 bg-white overflow-y-auto">
                    {step ? <StepPanel step={step} /> : (
                        <div className="p-4 space-y-3 text-sm">
                            <p className="text-zinc-500">Click a node to see its input and output.</p>
                            <p className="label-mono">trigger payload</p><Json value={run.trigger_payload} />
                            {run.loop_item !== null && run.loop_item !== undefined && <><p className="label-mono">loop item</p><Json value={run.loop_item} /></>}
                        </div>
                    )}
                </aside>
            </div>
        </div>
    );
}

export default function RunDetail() {
    return <ReactFlowProvider><Detail /></ReactFlowProvider>;
}
```

- [ ] **Step 3: Wire up**

`App.tsx`: import `RunDetail`; add **before** the `automations/:id` route:
```tsx
<Route path="automations/runs/:runId" element={<RunDetail />} />
```
`WorkflowEditor.tsx`: import `DryRunDialog` and `FlaskConical`; add state `const [dryRunOpen, setDryRunOpen] = useState(false);`; in the toolbar after the Save button:
```tsx
{!readOnly && !isNew && wf && (
    <button onClick={() => setDryRunOpen(true)}
        className="inline-flex items-center gap-1.5 h-8 px-3 border border-violet-300 text-violet-700 text-sm hover:bg-violet-50">
        <FlaskConical size={14} /> Dry run
    </button>
)}
```
and at the end of the returned root `<div>`:
```tsx
{dryRunOpen && wf && <DryRunDialog workflow={wf} onClose={() => setDryRunOpen(false)} />}
```
(Dry run uses the **saved** graph — the dialog is opened from `wf`, so the user saves first. Add `title="Uses the last saved version"` to the button.)

- [ ] **Step 4: Verify**

`npm run build`; rebuild frontend. In the editor from Task 13: Dry run → pick a case with tag `x` → lands on the run detail; nodes turn green; clicking the note node shows SIMULATED + `would_do.content` rendered with the case title; the case's timeline is unchanged. Dry run a case without tag `x` → the note node is gray (skipped). Invalid JSON in a mock textarea → inline "Invalid JSON" error, no request sent.

- [ ] **Step 5: Commit**
```bash
git add frontend/src/features/automations frontend/src/App.tsx
git commit -m "feat(automations): dry-run dialog and run detail view"
```

---

### Task 15: CaseDetail automation tab, alert run links, HTTP allowlist card

**Files:**
- Create: `frontend/src/features/automations/CaseAutomation.tsx`, `frontend/src/features/integrations/HttpAllowlistCard.tsx`
- Modify: `frontend/src/features/cases/CaseDetail.tsx`, `frontend/src/features/alerts/AlertsList.tsx`, `frontend/src/features/integrations/Integrations.tsx`

**Interfaces:**
- Consumes: `RunsTable` (Task 12), API `/workflows/`, `/workflows/{id}/run`, `/workflow-runs?alert_id=`, `/workflows/settings/http-allowlist`.
- Produces: `<CaseAutomation caseId />`, `<AlertRuns alertId />` (exported from `CaseAutomation.tsx`), `<HttpAllowlistCard />`.

- [ ] **Step 1: Case automation panel + alert runs**

`frontend/src/features/automations/CaseAutomation.tsx`:
```tsx
import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Link, useNavigate } from 'react-router-dom';
import { Play } from 'lucide-react';
import { api } from '../../api/client';
import type { User } from '../../types';
import type { RunSummary, WorkflowSummary } from './types';
import RunsTable, { StatusBadge } from './RunsTable';

export default function CaseAutomation({ caseId }: { caseId: number }) {
    const qc = useQueryClient();
    const navigate = useNavigate();
    const [picked, setPicked] = useState('');
    const [error, setError] = useState<string | null>(null);
    const { data: me } = useQuery({ queryKey: ['currentUser'], queryFn: async () => (await api.get('/users/me')).data as User, staleTime: 300_000 });
    const canRun = !!me && (me.is_super_admin || me.role === 'admin' || me.role === 'analyst');

    const { data: workflows = [] } = useQuery({
        queryKey: ['workflows'],
        queryFn: async () => (await api.get('/workflows/')).data as WorkflowSummary[],
    });
    const manual = workflows.filter((w) => w.trigger_type === 'manual' && w.enabled);

    const run = useMutation({
        mutationFn: async () => (await api.post(`/workflows/${picked}/run`, { case_id: caseId })).data as { run_id: number },
        onSuccess: ({ run_id }) => { qc.invalidateQueries({ queryKey: ['workflow-runs'] }); navigate(`/automations/runs/${run_id}`); },
        onError: (err: any) => setError(err?.response?.data?.detail ?? 'Run failed'),
    });

    return (
        <div className="space-y-4">
            {canRun && (
                <div className="flex items-center gap-2">
                    <select className="border border-zinc-300 px-2 py-1.5 text-sm bg-white min-w-[240px]" value={picked} onChange={(e) => setPicked(e.target.value)}
                        aria-label="Manual workflow">
                        <option value="">{manual.length ? 'Run a workflow on this case…' : 'No manual workflows enabled'}</option>
                        {manual.map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}
                    </select>
                    <button disabled={!picked || run.isPending} onClick={() => { setError(null); run.mutate(); }}
                        className="inline-flex items-center gap-1.5 h-8 px-3 bg-accent-600 text-white text-sm disabled:opacity-50"><Play size={13} />Run</button>
                    {error && <span className="text-xs text-red-700">{error}</span>}
                </div>
            )}
            <RunsTable caseId={caseId} />
        </div>
    );
}

export function AlertRuns({ alertId }: { alertId: number }) {
    const { data: runs = [] } = useQuery({
        queryKey: ['workflow-runs', { alert_id: alertId }],
        queryFn: async () => (await api.get('/workflow-runs/', { params: { alert_id: alertId, dry_run: false } })).data as RunSummary[],
    });
    if (!runs.length) return null;
    return (
        <div className="flex items-center gap-3 flex-wrap text-xs mb-2">
            <span className="label-mono">automation</span>
            {runs.map((r) => (
                <Link key={r.id} to={`/automations/runs/${r.id}`} className="flex items-center gap-1.5 text-accent-600 hover:underline">
                    {r.workflow_name} <StatusBadge status={r.status} />
                </Link>
            ))}
        </div>
    );
}
```

- [ ] **Step 2: CaseDetail tab**

In `frontend/src/features/cases/CaseDetail.tsx`:
- extend the tab state type: `useState<'timeline' | 'tasks' | 'artifacts' | 'network' | 'audit' | 'automation'>('timeline')`
- add `'automation'` to the tab array: `['timeline', 'tasks', 'artifacts', 'network', 'audit', 'automation']`
- import `CaseAutomation from '../automations/CaseAutomation'` and add next to the other tab bodies:
```tsx
{activeTab === 'automation' && id && <CaseAutomation caseId={parseInt(id)} />}
```

- [ ] **Step 3: Alert rows**

In `frontend/src/features/alerts/AlertsList.tsx`, import `{ AlertRuns } from '../automations/CaseAutomation'`, and inside the expanded row (above the `<pre>` payload):
```tsx
<AlertRuns alertId={a.id} />
```

- [ ] **Step 4: HTTP allowlist card**

`frontend/src/features/integrations/HttpAllowlistCard.tsx`:
```tsx
import { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../../api/client';

export default function HttpAllowlistCard() {
    const qc = useQueryClient();
    const { data } = useQuery({ queryKey: ['http-allowlist'], queryFn: async () => (await api.get('/workflows/settings/http-allowlist')).data as { hosts: string[] } });
    const [text, setText] = useState('');
    useEffect(() => { if (data) setText(data.hosts.join('\n')); }, [data]);
    const save = useMutation({
        mutationFn: async () => (await api.put('/workflows/settings/http-allowlist', { hosts: text.split('\n') })).data,
        onSuccess: () => qc.invalidateQueries({ queryKey: ['http-allowlist'] }),
    });
    return (
        <div className="bg-white border border-zinc-200 p-5">
            <h2 className="font-semibold text-zinc-800">Automation HTTP allowlist</h2>
            <p className="text-xs text-zinc-500 mt-1 mb-3">
                Workflow HTTP requests to private or internal addresses are blocked. List internal hostnames (one per line) that workflows may call.
            </p>
            <textarea rows={4} className="w-full border border-zinc-300 px-2 py-1.5 font-mono text-xs" value={text}
                placeholder="soar.internal.corp" onChange={(e) => setText(e.target.value)} />
            <button onClick={() => save.mutate()} disabled={save.isPending}
                className="mt-2 h-8 px-3 bg-accent-600 text-white text-sm disabled:opacity-50">{save.isSuccess ? 'Saved' : 'Save'}</button>
        </div>
    );
}
```
In `Integrations.tsx`, render `<HttpAllowlistCard />` inside the admin-only branch, after the webhooks section.

- [ ] **Step 5: Verify**

`npm run build`; rebuild frontend. Create + enable a **manual** workflow (start → add note). On a case: Automation tab → pick it → Run → lands on a succeeded run; the case timeline has the note; the tab lists the run. Viewer: no Run control. Ingest an alert while an `alert.ingested` workflow is enabled (curl `POST /api/v1/alerts/webhook` with a webhook key) → expanding the alert shows the automation run link. Allowlist: save `internal.corp`, reload → persists.

- [ ] **Step 6: Commit**
```bash
git add frontend/src/features
git commit -m "feat(automations): case automation tab, alert run links, HTTP allowlist"
```

---

### Task 16: Phase 1 end-to-end check + docs

**Files:**
- Create: `docs/workflows/README.md`

- [ ] **Step 1: Alert-to-case E2E (no Slack yet)**

Build in the UI (admin), trigger **Alert ingested**, filter `alert.payload.severity is defined and alert.payload.severity >= 7`:
- `promote` (alert_promote, mode `group`, title `{{ alert.title }}`, severity `high`, tags `edr`, group_key `host:{{ alert.payload.host }}`, window 6)
- `note` (case_add_note, content `Alert {{ alert.external_id }} grouped ({{ 'new case' if steps.promote.output.created else 'existing case' }})`)
- edges start → promote → note

And a second workflow, same trigger, filter `alert.payload.severity is defined and alert.payload.severity < 7` → `alert_dismiss` (reason `auto: low severity`).

Dry-run the first against a pending alert → run shows promote SIMULATED with `would_do.group_key` rendered. Enable both. Then:
```bash
for i in 1 2 3; do curl -s -X POST localhost:8000/api/v1/alerts/webhook -H "X-API-Key: <key>" -H 'Content-Type: application/json' \
  -d "{\"external_id\":\"edr-$i\",\"title\":\"Beacon on web-1\",\"payload\":{\"host\":\"web-1\",\"severity\":9}}" & done; wait
curl -s -X POST localhost:8000/api/v1/alerts/webhook -H "X-API-Key: <key>" -H 'Content-Type: application/json' \
  -d '{"external_id":"edr-low","title":"noise","payload":{"host":"web-2","severity":2}}'
```
Expected: the three concurrent alerts are **all promoted into one case** (one note says "new case", two say "existing case"); `edr-low` is dismissed with reason; an alert without `severity` matches neither workflow and stays pending. Clean up the created case(s)/alerts by id afterwards.

- [ ] **Step 2: Write the docs**

`docs/workflows/README.md` — cover, with one worked example each: concepts (trigger, filter, nodes, edges, condition handles); template context table (`case`, `alert`, `trigger`, `steps.<id>.output`, `loop.*`, `dry_run`); node reference (one line per node with config keys and output shape, copied from the spec); join/skip semantics in two sentences; retries/timeouts; dry-run (what executes vs simulates, mocks, `execute_in_dry_run`); loop guard (depth 3); HTTP allowlist/SSRF; the alert-grouping example above.

- [ ] **Step 3: Commit**
```bash
git add docs/workflows/README.md
git commit -m "docs(workflows): user guide for automations"
```

---

# Phase 2 — for_each loops

### Task 17: Loop pure logic + validation rules

**Files:**
- Create: `backend/app/workflows/loops.py`
- Modify: `backend/app/workflows/node_types.py`, `backend/app/workflows/validation.py`, `backend/app/workflows/graph.py`
- Test: `backend/tests/test_wf_loops.py`, `backend/tests/test_wf_validation.py` (append)

**Interfaces:**
- Produces:
  - `loops.py`: `MAX_ITEMS_CAP = 500`, `MAX_CONCURRENCY = 20`, `coerce_items(value, max_items) -> list` (raises `ValueError`), `next_children_to_start(statuses: list[str], concurrency: int) -> list[int]` (indexes into `statuses`), `aggregate_children(children: list[dict]) -> dict | None` — children are `{"index", "status", "steps": {node_id: output}}`; returns `None` while any child is non-terminal, else `{"results": [...sorted by index], "succeeded": int, "failed": int}`.
  - `graph.py`: `body_graph(graph: dict, loop_id: str) -> dict` — body nodes (with `parent_id` cleared) + an implicit `__start__` trigger node wired to every body node that has no in-body parent.
  - `NODE_TYPES["for_each"]`.

- [ ] **Step 1: Write failing loop tests**

`backend/tests/test_wf_loops.py`:
```python
import pytest

from app.workflows.graph import body_graph, ready_nodes
from app.workflows.loops import aggregate_children, coerce_items, next_children_to_start


@pytest.mark.parametrize("value", ["abc", {"a": 1}, None, 5])
def test_items_must_be_list(value):
    with pytest.raises(ValueError, match="must evaluate to a list"):
        coerce_items(value, 100)


def test_items_cap():
    assert coerce_items([1, 2], None) == [1, 2]
    with pytest.raises(ValueError, match="max_items"):
        coerce_items(list(range(11)), 10)
    with pytest.raises(ValueError, match="max_items"):
        coerce_items(list(range(501)), 10_000)  # hard cap 500


def test_next_children_respects_concurrency():
    assert next_children_to_start(["queued"] * 5, 2) == [0, 1]
    assert next_children_to_start(["running", "waiting", "queued", "queued"], 3) == [2]
    assert next_children_to_start(["succeeded", "running", "queued", "queued"], 2) == [2]
    assert next_children_to_start(["running", "running"], 2) == []


def test_aggregate_waits_for_all_terminal():
    assert aggregate_children([{"index": 0, "status": "succeeded", "steps": {}},
                               {"index": 1, "status": "running", "steps": {}}]) is None


def test_aggregate_counts_and_orders():
    agg = aggregate_children([
        {"index": 1, "status": "failed", "steps": {"a": None}},
        {"index": 0, "status": "succeeded", "steps": {"a": {"ok": 1}}},
        {"index": 2, "status": "cancelled", "steps": {}},
    ])
    assert agg["succeeded"] == 1 and agg["failed"] == 2
    assert [r["index"] for r in agg["results"]] == [0, 1, 2]
    assert agg["results"][0]["steps"] == {"a": {"ok": 1}}


def test_body_graph_adds_start_to_roots():
    g = {"nodes": [{"id": "t", "type": "trigger"}, {"id": "loop", "type": "for_each"},
                   {"id": "a", "type": "case_add_note", "parent_id": "loop"},
                   {"id": "b", "type": "case_add_note", "parent_id": "loop"},
                   {"id": "c", "type": "case_add_note", "parent_id": "loop"},
                   {"id": "after", "type": "case_add_note"}],
         "edges": [{"id": "1", "source": "t", "target": "loop", "source_handle": None},
                   {"id": "2", "source": "a", "target": "b", "source_handle": None},
                   {"id": "3", "source": "loop", "target": "after", "source_handle": None}]}
    body = body_graph(g, "loop")
    ids = {n["id"] for n in body["nodes"]}
    assert ids == {"__start__", "a", "b", "c"}
    assert all(n.get("parent_id") is None for n in body["nodes"])
    starts = {e["target"] for e in body["edges"] if e["source"] == "__start__"}
    assert starts == {"a", "c"}
    ready, _ = ready_nodes(body, {"__start__": {"status": "succeeded", "output": {}}})
    assert ready == {"a", "c"}
```

- [ ] **Step 2: Append failing validation tests**

Append to `backend/tests/test_wf_validation.py`:
```python
def loop_graph(**overrides):
    g = {"nodes": [n("start", "trigger"), n("loop", "for_each", config={"items": "steps.start.output.users"}),
                   n("inner", "case_add_note", parent_id="loop", config={"content": "{{ loop.item }}"})],
         "edges": [e("start", "loop")]}
    g.update(overrides)
    return g


def test_valid_loop():
    assert validate_graph(loop_graph(), "manual") == []


def test_edge_cannot_cross_loop_boundary():
    g = loop_graph()
    g["edges"].append(e("start", "inner"))
    assert "loop boundary" in msgs(validate_graph(g, "manual"))


def test_no_nested_loops_and_parent_must_be_loop():
    g = loop_graph()
    g["nodes"].append(n("inner_loop", "for_each", parent_id="loop", config={"items": "[1]"}))
    g["nodes"].append(n("x", "case_add_note", parent_id="inner", config={"content": "x"}))
    m = msgs(validate_graph(g, "manual"))
    assert "nested" in m and "for_each" in m


def test_empty_loop_body():
    g = loop_graph()
    g["nodes"] = g["nodes"][:2]
    assert "empty" in msgs(validate_graph(g, "manual"))


def test_loop_limits():
    g = loop_graph()
    g["nodes"][1]["config"].update({"concurrency": 50, "max_items": 900})
    m = msgs(validate_graph(g, "manual"))
    assert "concurrency" in m and "max_items" in m
```

- [ ] **Step 3: Run — expect FAIL.**

Run: `docker compose exec backend python -m pytest tests/test_wf_loops.py tests/test_wf_validation.py -v`

- [ ] **Step 4: Implement `loops.py`**

`backend/app/workflows/loops.py`:
```python
"""Pure helpers for for_each: item coercion, child scheduling, aggregation."""
from typing import List, Optional

MAX_ITEMS_CAP = 500
MAX_CONCURRENCY = 20
_TERMINAL = {"succeeded", "failed", "cancelled"}


def coerce_items(value, max_items) -> list:
    if not isinstance(value, list):  # strings/dicts are iterable but never what the author meant
        raise ValueError(f"items must evaluate to a list, got {type(value).__name__}")
    cap = min(int(max_items or 100), MAX_ITEMS_CAP)
    if len(value) > cap:
        raise ValueError(f"{len(value)} items exceeds max_items ({cap})")
    return value


def next_children_to_start(statuses: List[str], concurrency: int) -> List[int]:
    active = sum(1 for s in statuses if s in ("running", "waiting"))
    free = max(0, concurrency - active)
    return [i for i, s in enumerate(statuses) if s == "queued"][:free]


def aggregate_children(children: List[dict]) -> Optional[dict]:
    if any(c["status"] not in _TERMINAL for c in children):
        return None
    results = sorted(({"index": c["index"], "status": c["status"], "steps": c["steps"]} for c in children),
                     key=lambda r: r["index"])
    succeeded = sum(1 for c in children if c["status"] == "succeeded")
    return {"results": results, "succeeded": succeeded, "failed": len(children) - succeeded}
```

- [ ] **Step 5: `body_graph` in `graph.py`**

Append to `backend/app/workflows/graph.py`:
```python
def body_graph(graph: dict, loop_id: str) -> dict:
    body = [{**n, "parent_id": None} for n in graph["nodes"] if n.get("parent_id") == loop_id]
    ids = {n["id"] for n in body}
    edges = [e for e in graph["edges"] if e["source"] in ids and e["target"] in ids]
    targets = {e["target"] for e in edges}
    start = {"id": "__start__", "type": "trigger", "config": {}, "parent_id": None, "position": {"x": 0, "y": 0}}
    edges += [{"id": f"__start__{nid}", "source": "__start__", "target": nid, "source_handle": None}
              for nid in sorted(ids - targets)]
    return {"nodes": [start] + body, "edges": edges}
```

- [ ] **Step 6: Registry + validation rules**

`node_types.py` — add to `NODE_TYPES`:
```python
    "for_each":            {"required": ["items"], "raw": ["items"], "alert_only": False},
```
`validation.py` — add import `from app.workflows.loops import MAX_CONCURRENCY, MAX_ITEMS_CAP` and, in `validate_graph` **before** the cycle check, append:
```python
    for node in nodes:
        pid = node.get("parent_id")
        if pid:
            parent = by_id.get(pid)
            if not parent or parent.get("type") != "for_each":
                _err(errors, node.get("id"), "parent_id must reference a for_each node")
            if node.get("type") == "for_each":
                _err(errors, node.get("id"), "for_each loops cannot be nested")
            if node.get("type") == "trigger":
                _err(errors, node.get("id"), "the trigger cannot be inside a loop")
        if node.get("type") == "for_each":
            cfg = node.get("config") or {}
            if not any(c.get("parent_id") == node.get("id") for c in nodes):
                _err(errors, node.get("id"), "loop body is empty — drop nodes inside the loop")
            for key, hi in (("concurrency", MAX_CONCURRENCY), ("max_items", MAX_ITEMS_CAP)):
                if cfg.get(key) not in (None, ""):
                    try:
                        ok = 1 <= int(cfg[key]) <= hi
                    except (TypeError, ValueError):
                        ok = False
                    if not ok:
                        _err(errors, node.get("id"), f"{key} must be between 1 and {hi}")

    for e in edges:
        src, tgt = by_id.get(e.get("source")), by_id.get(e.get("target"))
        if src and tgt and src.get("parent_id") != tgt.get("parent_id"):
            _err(errors, None, f"edge {e.get('id')} crosses a loop boundary")
```

- [ ] **Step 7: Run — expect PASS** (all of `tests/`).

- [ ] **Step 8: Commit**
```bash
git add backend/app/workflows backend/tests/test_wf_loops.py backend/tests/test_wf_validation.py
git commit -m "feat(workflows): for_each pure logic and loop validation"
```

---

### Task 18: for_each runtime (child runs, scheduling, aggregation)

**Files:**
- Modify: `backend/app/workflows/nodes/logic.py`, `backend/app/workflows/runtime.py`, `backend/app/tasks/workflows.py`

**Interfaces:**
- Consumes: `coerce_items`, `next_children_to_start`, `aggregate_children` (Task 17), `body_graph` (Task 17), `eval_expr` (Task 2), `WAIT` (Task 8).
- Produces: executor `for_each`; `async loop_tick(parent_step_id: int)` in `runtime.py`; Celery `loop_tick_task` (replaces the Task 8 stub).

Flow: `for_each` creates all child runs as `queued`, stores `{"concurrency", "total"}` in its step output, returns `WAIT`, and schedules `loop_tick`. `loop_tick` (under a row lock on the parent step) starts queued children up to the concurrency limit; each child's `advance_run` calls `loop_tick` again when the child finishes (already wired in Task 8); when all children are terminal, `loop_tick` completes the parent step and advances the parent run.

- [ ] **Step 1: Executor**

Append to `backend/app/workflows/nodes/logic.py`:
```python
from app.models.workflow import WorkflowRun
from app.workflows.graph import body_graph
from app.workflows.loops import MAX_CONCURRENCY, coerce_items
from app.workflows.nodes import WAIT


@executor("for_each")
async def run_for_each(nctx: NodeContext, config: dict):
    from app.tasks.workflows import loop_tick_task
    try:
        items = coerce_items(eval_expr(config["items"], nctx.ctx), config.get("max_items"))
    except (TemplateError, ValueError) as e:
        raise NodeError(str(e))
    if not items:
        return {"results": [], "succeeded": 0, "failed": 0}
    concurrency = max(1, min(int(config.get("concurrency") or 5), MAX_CONCURRENCY))
    run, body = nctx.run, body_graph(nctx.run.graph_snapshot, nctx.node["id"])
    for i, item in enumerate(items):
        nctx.db.add(WorkflowRun(
            tenant_id=run.tenant_id, workflow_id=run.workflow_id, workflow_version=run.workflow_version,
            case_id=run.case_id, alert_id=run.alert_id, status="queued", trigger_payload=run.trigger_payload,
            graph_snapshot=body, depth=run.depth, is_dry_run=run.is_dry_run, dry_run_mocks=run.dry_run_mocks,
            parent_run_id=run.id, parent_step_id=nctx.step.id, loop_index=i, loop_item=item))
    nctx.step.output = {"concurrency": concurrency, "total": len(items)}
    step_id = nctx.step.id
    nctx.after_commit.append(lambda: loop_tick_task.delay(step_id))
    return WAIT
```

- [ ] **Step 2: `loop_tick` in runtime**

Append to `backend/app/workflows/runtime.py`:
```python
from app.workflows.loops import aggregate_children, next_children_to_start


async def loop_tick(parent_step_id: int) -> None:
    from app.tasks.workflows import advance_run_task

    start_ids, resume_run = [], None
    async with AsyncSessionLocal() as db:
        step = (await db.execute(select(WorkflowRunStep).where(WorkflowRunStep.id == parent_step_id).with_for_update())).scalars().first()
        if not step or step.status != "waiting":
            return
        parent = (await db.execute(select(WorkflowRun).where(WorkflowRun.id == step.run_id))).scalars().first()
        if parent.status in _TERMINAL_RUN:
            return
        children = (await db.execute(select(WorkflowRun).where(WorkflowRun.parent_step_id == step.id)
                                     .order_by(WorkflowRun.loop_index))).scalars().all()
        statuses = [c.status for c in children]

        if all(s in _TERMINAL_RUN for s in statuses):
            rows = (await db.execute(select(WorkflowRunStep).where(
                WorkflowRunStep.run_id.in_([c.id for c in children])))).scalars().all()
            outputs = {}
            for r in rows:
                if r.node_id != "__start__":
                    outputs.setdefault(r.run_id, {})[r.node_id] = r.output
            agg = aggregate_children([{"index": c.loop_index, "status": c.status, "steps": outputs.get(c.id, {})} for c in children])
            node = _node(parent.graph_snapshot, step.node_id)
            if agg["failed"] and not node.get("continue_on_error"):
                step.status, step.error = "failed", f"{agg['failed']} of {len(children)} items failed"
                step.output = agg
            else:
                step.status = "succeeded"
                step.output = {**agg, **({"error": f"{agg['failed']} items failed"} if agg["failed"] else {})}
            step.finished_at = _now()
            resume_run = parent.id
        else:
            concurrency = int((step.output or {}).get("concurrency", 5))
            for i in next_children_to_start(statuses, concurrency):
                child = children[i]
                child.status = "running"
                db.add(WorkflowRunStep(run_id=child.id, node_id="__start__", status="succeeded",
                                       output=child.trigger_payload, started_at=_now(), finished_at=_now()))
                start_ids.append(child.id)
        await db.commit()

    for cid in start_ids:
        advance_run_task.delay(cid)
    if resume_run:
        advance_run_task.delay(resume_run)
```

- [ ] **Step 3: Replace the task stub**

In `backend/app/tasks/workflows.py` replace the `loop_tick_task` stub with:
```python
@celery_app.task(acks_late=True)
def loop_tick_task(parent_step_id):
    from app.workflows.runtime import loop_tick
    _run_async(loop_tick(parent_step_id))
```

- [ ] **Step 4: Smoke test**

Restart backend + worker. Using the `/tmp/wf_smoke.py` pattern from Task 8, create a manual workflow with:
```python
{"nodes": [{"id": "start", "type": "trigger", "config": {}},
           {"id": "loop", "type": "for_each", "config": {"items": "['a', 'b', 'c']", "concurrency": 2}},
           {"id": "note", "type": "case_add_note", "parent_id": "loop", "config": {"content": "item {{ loop.index }}={{ loop.item }}"}},
           {"id": "done", "type": "case_add_note", "config": {"content": "loop ok: {{ steps.loop.output.succeeded }}"}}],
 "edges": [{"id": "1", "source": "start", "target": "loop", "source_handle": None},
           {"id": "2", "source": "loop", "target": "done", "source_handle": None}]}
```
Expected: 3 child runs (`select id, loop_index, status from workflow_runs where parent_run_id=<RUN_ID>`) all `succeeded`; the case timeline has `item 0=a`, `item 1=b`, `item 2=c` and `loop ok: 3`; the parent run is `succeeded`.
Then set `items` to `"case.title"` → the `loop` step is `failed` with "items must evaluate to a list, got str" and no child runs exist.
Then dry-run the first version via the API → children are created with `is_dry_run = true`, notes are simulated, and the case timeline is unchanged.
Delete the scratch workflow by id.

- [ ] **Step 5: Commit**
```bash
git add backend/app/workflows backend/app/tasks/workflows.py
git commit -m "feat(workflows): for_each child runs with concurrency and aggregation"
```

---

### Task 19: for_each container node in the UI

**Files:**
- Modify: `frontend/src/features/automations/nodeCatalog.ts`, `frontend/src/features/automations/WorkflowNode.tsx`

**Interfaces:**
- Consumes: `NodeDef.container` flag (Task 12), `toFlow` container sizing (Task 13).
- Produces: a `for_each` palette entry that renders as a resizable dashed container.

- [ ] **Step 1: Catalog entry**

In `nodeCatalog.ts` add `Repeat` to the lucide import and, after the `condition` entry:
```ts
    {
        type: 'for_each', label: 'For each', category: 'Logic', icon: Repeat, container: true,
        fields: [
            { key: 'items', label: 'Items', kind: 'expression', required: true, placeholder: 'steps.lookup.output.body.users', help: 'Expression that evaluates to a list; drop nodes inside the box to run them per item' },
            { key: 'concurrency', label: 'Concurrency (1-20)', kind: 'number', placeholder: '5' },
            { key: 'max_items', label: 'Max items (≤ 500)', kind: 'number', placeholder: '100' },
        ],
    },
```

- [ ] **Step 2: Container rendering**

In `WorkflowNode.tsx` add `NodeResizer` to the `@xyflow/react` import and, at the top of the component body right after `const isTrigger = ...`:
```tsx
    if (def?.container) {
        return (
            <div className={cn('w-full h-full border-2 border-dashed bg-accent-50/40',
                data.status ? RING[data.status] : 'border-accent-300', data.invalid && 'border-red-500', selected && 'ring-2 ring-accent-400')}>
                <NodeResizer isVisible={selected} minWidth={260} minHeight={160} />
                <Handle type="target" position={Position.Top} className="!bg-zinc-500 !w-2.5 !h-2.5 !rounded-none" />
                <div className="flex items-center gap-2 px-3 py-1.5 border-b border-dashed border-accent-200 bg-white/70">
                    {Icon && <Icon size={14} className="text-accent-600" />}
                    <span className="text-sm font-medium text-zinc-900">{def.label}</span>
                    <span className="font-mono text-[10px] text-zinc-500">{id}</span>
                    {data.status && <span className="ml-auto font-mono text-[10px] uppercase text-zinc-500">{data.status}</span>}
                </div>
                <p className="px-3 py-1 text-[10px] font-mono text-zinc-500">runs once per item · loop.item · loop.index</p>
                <Handle type="source" position={Position.Bottom} className="!bg-zinc-500 !w-2.5 !h-2.5 !rounded-none" />
            </div>
        );
    }
```

- [ ] **Step 3: Verify** — `npm run build`; rebuild. Drag *For each* onto the canvas → a dashed box appears; select it → resize handles; resize, Save, reload → size persists.

- [ ] **Step 4: Commit**
```bash
git add frontend/src/features/automations
git commit -m "feat(automations): for_each container node"
```

---

### Task 20: Drop nodes into loops + child-run drill-down

**Files:**
- Modify: `frontend/src/features/automations/WorkflowEditor.tsx`, `frontend/src/features/automations/RunDetail.tsx`

**Interfaces:**
- Consumes: `getIntersectingNodes`, `getInternalNode` from `useReactFlow` (React Flow v12), `ChildRun` type (Task 12), `StepPanel` (Task 14).
- Produces: palette drops inside a `for_each` box create body nodes (`parent_id` set, position relative to the container); run detail lists a for_each step's child runs.

- [ ] **Step 1: Parent-aware add**

In `WorkflowEditor.tsx` change the hook line to `const { screenToFlowPosition, getIntersectingNodes, getInternalNode } = useReactFlow();`, then replace `addNode` and `onDrop` with:
```tsx
    const addNode = useCallback((type: string, flowPos: { x: number; y: number }, parentId?: string) => {
        setNodes((ns) => {
            const taken = new Set(ns.map((n) => n.id));
            const nid = nextNodeId(type, taken);
            let position = flowPos;
            if (parentId) {
                const abs = getInternalNode(parentId)?.internals.positionAbsolute ?? { x: 0, y: 0 };
                position = { x: flowPos.x - abs.x, y: flowPos.y - abs.y };
            }
            const wfNode: WfNode = { id: nid, type, position, parent_id: parentId ?? null,
                                     config: type === 'http_request' ? { method: 'GET' } : {} };
            return [...ns, ...toFlow({ nodes: [wfNode], edges: [] }).nodes];
        });
    }, [setNodes, getInternalNode]);

    const onDrop = useCallback((e: DragEvent) => {
        e.preventDefault();
        const type = e.dataTransfer.getData('application/wf-node');
        if (!type) return;
        const pos = screenToFlowPosition({ x: e.clientX, y: e.clientY });
        // ponytail: only palette drops can enter a loop; moving an existing node in/out means delete + re-add.
        const container = type === 'for_each' ? undefined : getIntersectingNodes({ x: pos.x, y: pos.y, width: 1, height: 1 })
            .find((n) => NODE_DEF[(n as WfFlowNode).data.wf.type]?.container);
        addNode(type, pos, container?.id);
    }, [addNode, screenToFlowPosition, getIntersectingNodes]);
```

- [ ] **Step 2: Child runs in the step panel**

In `RunDetail.tsx`, change `StepPanel` to accept children:
```tsx
export function StepPanel({ step, children = [] }: { step: RunStep; children?: ChildRun[] }) {
```
(import `ChildRun` from `./types`), and at the end of its returned `<div>` add:
```tsx
            {children.length > 0 && (
                <>
                    <p className="label-mono">items ({children.length})</p>
                    <ul className="divide-y divide-zinc-100 border border-zinc-200">
                        {children.map((c) => (
                            <li key={c.id} className="flex items-center gap-2 px-2 py-1.5 text-xs">
                                <span className="num w-10">#{c.loop_index}</span>
                                <StatusBadge status={c.status} />
                                <Link to={`/automations/runs/${c.id}`} className="ml-auto text-accent-600 hover:underline">open</Link>
                            </li>
                        ))}
                    </ul>
                </>
            )}
```
and in `Detail` render it as:
```tsx
{step ? <StepPanel step={step} children={run.children.filter((c) => c.parent_step_id === step.id)} /> : ( ... )}
```

- [ ] **Step 3: Verify**

`npm run build`; rebuild. In the editor: add *For each*, drag *Add note* into the box → the note moves with the box; connect nothing to it (body roots start automatically); set items `['a','b']`, note content `{{ loop.item }}`; connect the trigger → loop. Save → valid. Drag a note from the palette and drop it *outside* the box, then try to connect it to the inner note → Save shows "crosses a loop boundary". Dry-run on a case → the loop's step panel lists 2 items; "open" shows each child run with its simulated note.

- [ ] **Step 4: Commit**
```bash
git add frontend/src/features/automations
git commit -m "feat(automations): loop bodies in editor and child-run drill-down"
```

---

# Phase 3 — Slack integration

### Task 21: Slack model, migration, and service (signature, API calls, blocks)

**Files:**
- Create: `backend/app/models/slack_integration.py`, `backend/alembic/versions/b1s2l3a4c5k6_slack.py`, `backend/app/services/slack_service.py`
- Modify: `backend/app/db/base.py`
- Test: `backend/tests/test_slack_signature.py`

**Interfaces:**
- Consumes: `encrypt`/`decrypt` (Task 1).
- Produces:
  - model `SlackIntegration(id, tenant_id unique, team_id, bot_token_enc, signing_secret_enc, default_channel, created_at, updated_at)`
  - `slack_service`: `class SlackError(Exception)`; `verify_signature(signing_secret: str, timestamp: str, body: bytes, signature: str, now: float | None = None) -> bool`; `async slack_call(token: str, method: str, **args) -> dict`; `async respond(response_url: str, payload: dict) -> None`; `parse_action_value(value: str) -> tuple | None` → `("wf", wait_token: str, index: int)` or `("case", tenant_id: int, case_id: int, action: "ack"|"assign"|"close")`; `case_buttons_block(tenant_id: int, case_id: int) -> dict`; `ask_blocks(message: str, buttons: list[str], wait_token: str) -> list[dict]`; `async load_integration(db, tenant_id: int) -> tuple[SlackIntegration, str]` (decrypted bot token; raises `SlackError("Slack is not configured for this tenant")`).

- [ ] **Step 1: Write failing tests**

`backend/tests/test_slack_signature.py`:
```python
import hashlib
import hmac

from app.services.slack_service import parse_action_value, verify_signature

SECRET = "8f742231b10e8888abcd99yyyzzz85a5"
BODY = b"payload=%7B%22type%22%3A%22block_actions%22%7D"
TS = "1531420618"


def sign(secret, ts, body):
    return "v0=" + hmac.new(secret.encode(), b"v0:" + ts.encode() + b":" + body, hashlib.sha256).hexdigest()


def test_valid_signature():
    assert verify_signature(SECRET, TS, BODY, sign(SECRET, TS, BODY), now=int(TS) + 10)


def test_wrong_secret_or_tampered_body():
    assert not verify_signature(SECRET, TS, BODY, sign("other", TS, BODY), now=int(TS))
    assert not verify_signature(SECRET, TS, BODY + b"x", sign(SECRET, TS, BODY), now=int(TS))


def test_stale_timestamp_rejected():
    assert not verify_signature(SECRET, TS, BODY, sign(SECRET, TS, BODY), now=int(TS) + 301)


def test_garbage_headers_rejected():
    assert not verify_signature(SECRET, "not-a-number", BODY, "v0=abc", now=0)
    assert not verify_signature(SECRET, TS, BODY, "", now=int(TS))


def test_parse_action_value():
    assert parse_action_value("wf:tok_123-x:1") == ("wf", "tok_123-x", 1)
    assert parse_action_value("case:3:10:ack") == ("case", 3, 10, "ack")
    assert parse_action_value("case:3:10:delete") is None
    assert parse_action_value("wf:tok") is None
    assert parse_action_value("junk") is None
    assert parse_action_value("case:a:b:ack") is None
```

- [ ] **Step 2: Run — expect FAIL.** `docker compose exec backend python -m pytest tests/test_slack_signature.py -v`

- [ ] **Step 3: Model + migration**

`backend/app/models/slack_integration.py`:
```python
from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.sql import func

from app.db.base_class import Base


class SlackIntegration(Base):
    """A tenant's bring-your-own Slack app. Secrets are Fernet-encrypted (app/utils/crypto.py)."""
    __tablename__ = "slack_integrations"

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, unique=True)
    team_id = Column(String, nullable=True, index=True)
    bot_token_enc = Column(Text, nullable=False)
    signing_secret_enc = Column(Text, nullable=False)
    default_channel = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
```
`base.py`: `from app.models.slack_integration import SlackIntegration  # noqa: F401`

`backend/alembic/versions/b1s2l3a4c5k6_slack.py`:
```python
"""slack_integrations

Revision ID: b1s2l3a4c5k6
Revises: a1w2f3l4o5w6
"""
import sqlalchemy as sa
from alembic import op

revision = "b1s2l3a4c5k6"
down_revision = "a1w2f3l4o5w6"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "slack_integrations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("team_id", sa.String()),
        sa.Column("bot_token_enc", sa.Text(), nullable=False),
        sa.Column("signing_secret_enc", sa.Text(), nullable=False),
        sa.Column("default_channel", sa.String()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_slack_integrations_team_id", "slack_integrations", ["team_id"])


def downgrade():
    op.drop_table("slack_integrations")
```
Run: `docker restart case_management-backend-1 && docker compose exec backend alembic upgrade head` → `a1w2f3l4o5w6 -> b1s2l3a4c5k6`.

- [ ] **Step 4: Service**

`backend/app/services/slack_service.py`:
```python
"""Slack Web API client (httpx, form-encoded), request verification and Block Kit builders."""
import hashlib
import hmac
import json
import re
import time
from typing import List, Optional, Tuple

import httpx
from sqlalchemy import select

from app.models.slack_integration import SlackIntegration
from app.utils.crypto import decrypt

SLACK_API = "https://slack.com/api/"
_CASE_ACTIONS = {"ack", "assign", "close"}
_WF_RE = re.compile(r"^wf:([A-Za-z0-9_\-]+):(\d+)$")
_CASE_RE = re.compile(r"^case:(\d+):(\d+):([a-z]+)$")


class SlackError(Exception):
    pass


def verify_signature(signing_secret: str, timestamp: str, body: bytes, signature: str, now: Optional[float] = None) -> bool:
    try:
        ts = int(timestamp)
    except (TypeError, ValueError):
        return False
    if abs((now if now is not None else time.time()) - ts) > 300:
        return False
    expected = "v0=" + hmac.new(signing_secret.encode(), b"v0:" + timestamp.encode() + b":" + body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature or "")


async def slack_call(token: str, method: str, **args) -> dict:
    data = {k: (json.dumps(v) if isinstance(v, (dict, list)) else v) for k, v in args.items() if v is not None}
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(SLACK_API + method, data=data, headers={"Authorization": f"Bearer {token}"})
        body = resp.json()
    except (httpx.HTTPError, ValueError) as e:
        raise SlackError(f"{method}: {e}") from e
    if not body.get("ok"):
        raise SlackError(f"{method}: {body.get('error', 'unknown_error')}")
    return body


async def respond(response_url: str, payload: dict) -> None:
    async with httpx.AsyncClient(timeout=10) as client:
        await client.post(response_url, json=payload)


def parse_action_value(value: str) -> Optional[Tuple]:
    m = _WF_RE.match(value or "")
    if m:
        return ("wf", m.group(1), int(m.group(2)))
    m = _CASE_RE.match(value or "")
    if m and m.group(3) in _CASE_ACTIONS:
        return ("case", int(m.group(1)), int(m.group(2)), m.group(3))
    return None


def case_buttons_block(tenant_id: int, case_id: int) -> dict:
    def btn(text, action, style=None):
        b = {"type": "button", "text": {"type": "plain_text", "text": text},
             "action_id": f"case_{action}", "value": f"case:{tenant_id}:{case_id}:{action}"}
        if style:
            b["style"] = style
        return b
    return {"type": "actions", "elements": [btn("Acknowledge", "ack", "primary"), btn("Assign to me", "assign"), btn("Close", "close", "danger")]}


def ask_blocks(message: str, buttons: List[str], wait_token: str) -> List[dict]:
    return [
        {"type": "section", "text": {"type": "mrkdwn", "text": message}},
        {"type": "actions", "elements": [
            {"type": "button", "text": {"type": "plain_text", "text": label[:75]},
             "action_id": f"wf_answer_{i}", "value": f"wf:{wait_token}:{i}"}
            for i, label in enumerate(buttons)
        ]},
    ]


async def load_integration(db, tenant_id: int) -> Tuple[SlackIntegration, str]:
    integ = (await db.execute(select(SlackIntegration).where(SlackIntegration.tenant_id == tenant_id))).scalars().first()
    if not integ:
        raise SlackError("Slack is not configured for this tenant")
    return integ, decrypt(integ.bot_token_enc)
```

- [ ] **Step 5: Run — expect PASS.**

- [ ] **Step 6: Commit**
```bash
git add backend/app/models/slack_integration.py backend/app/db/base.py backend/alembic/versions/b1s2l3a4c5k6_slack.py \
        backend/app/services/slack_service.py backend/tests/test_slack_signature.py
git commit -m "feat(slack): integration model, signature verification, Web API client"
```

---

### Task 22: Slack config API, Integrations card, app manifest + setup doc

**Files:**
- Create: `backend/app/api/v1/slack.py`, `frontend/src/features/integrations/SlackCard.tsx`, `docs/slack/slack-app-manifest.yml`, `docs/slack/setup.md`
- Modify: `backend/app/api/api.py`, `frontend/src/features/integrations/Integrations.tsx`

**Interfaces:**
- Consumes: `SlackIntegration`, `slack_call`, `SlackError` (Task 21), `encrypt`/`decrypt` (Task 1).
- Produces (admin only): `GET /slack/config` → `{configured, team_id, default_channel, has_bot_token, has_signing_secret}`; `PUT /slack/config` `{bot_token?, signing_secret?, default_channel?}` (blank = keep); `POST /slack/config/test` → `{ok, team, team_id}` or 400 with Slack's error; `DELETE /slack/config` → 204. `router` object in `app/api/v1/slack.py` (Task 23 adds `/interactions` to it).

- [ ] **Step 1: Router**

`backend/app/api/v1/slack.py`:
```python
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from app.models.slack_integration import SlackIntegration
from app.models.user import User
from app.services.slack_service import SlackError, slack_call
from app.utils.audit import create_audit_log
from app.utils.crypto import decrypt, encrypt

router = APIRouter()


class SlackConfigIn(BaseModel):
    bot_token: Optional[str] = None
    signing_secret: Optional[str] = None
    default_channel: Optional[str] = None


async def _get(db, tenant_id: int) -> Optional[SlackIntegration]:
    return (await db.execute(select(SlackIntegration).where(SlackIntegration.tenant_id == tenant_id))).scalars().first()


def _public(integ: Optional[SlackIntegration]) -> dict:
    if not integ:
        return {"configured": False, "team_id": None, "default_channel": None, "has_bot_token": False, "has_signing_secret": False}
    return {"configured": True, "team_id": integ.team_id, "default_channel": integ.default_channel,
            "has_bot_token": True, "has_signing_secret": True}


@router.get("/config")
async def get_config(db: AsyncSession = Depends(deps.get_db), current_user: User = Depends(deps.require_admin),
                     tenant_id: int = Depends(deps.get_effective_tenant_id)) -> Any:
    return _public(await _get(db, tenant_id))


@router.put("/config")
async def put_config(body: SlackConfigIn, db: AsyncSession = Depends(deps.get_db),
                     current_user: User = Depends(deps.require_admin),
                     tenant_id: int = Depends(deps.get_effective_tenant_id)) -> Any:
    integ = await _get(db, tenant_id)
    token, secret = (body.bot_token or "").strip(), (body.signing_secret or "").strip()
    if not integ:
        if not token or not secret:
            raise HTTPException(status_code=400, detail="Bot token and signing secret are required")
        integ = SlackIntegration(tenant_id=tenant_id, bot_token_enc=encrypt(token), signing_secret_enc=encrypt(secret))
        db.add(integ)
    else:
        if token:
            integ.bot_token_enc, integ.team_id = encrypt(token), None  # re-run Test to learn the team
        if secret:
            integ.signing_secret_enc = encrypt(secret)
    if body.default_channel is not None:
        integ.default_channel = body.default_channel.strip() or None
    await db.flush()
    await create_audit_log(db=db, entity_type="slack_integration", entity_id=integ.id, action="update",
                           tenant_id=tenant_id, user_id=current_user.id,
                           changes={"token_changed": bool(token), "secret_changed": bool(secret)})
    await db.commit()
    return _public(integ)


@router.post("/config/test")
async def test_config(db: AsyncSession = Depends(deps.get_db), current_user: User = Depends(deps.require_admin),
                      tenant_id: int = Depends(deps.get_effective_tenant_id)) -> Any:
    integ = await _get(db, tenant_id)
    if not integ:
        raise HTTPException(status_code=400, detail="Save a bot token and signing secret first")
    token = decrypt(integ.bot_token_enc)
    try:
        auth = await slack_call(token, "auth.test")
        integ.team_id = auth["team_id"]
        await db.commit()
        if integ.default_channel:
            await slack_call(token, "chat.postMessage", channel=integ.default_channel,
                             text=":white_check_mark: SOC Hub is connected to this channel.")
    except SlackError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "team": auth.get("team"), "team_id": integ.team_id}


@router.delete("/config", status_code=204)
async def delete_config(db: AsyncSession = Depends(deps.get_db), current_user: User = Depends(deps.require_admin),
                        tenant_id: int = Depends(deps.get_effective_tenant_id)) -> Response:
    integ = await _get(db, tenant_id)
    if integ:
        await create_audit_log(db=db, entity_type="slack_integration", entity_id=integ.id, action="delete",
                               tenant_id=tenant_id, user_id=current_user.id)
        await db.delete(integ)
        await db.commit()
    return Response(status_code=204)
```
`api.py`: add `slack` to imports and `api_router.include_router(slack.router, prefix="/slack", tags=["slack"])`.

- [ ] **Step 2: Manifest + setup doc**

`docs/slack/slack-app-manifest.yml`:
```yaml
display_information:
  name: SOC Hub
  description: Case notifications and end-user confirmations from SOC Hub
features:
  bot_user:
    display_name: SOC Hub
    always_online: true
oauth_config:
  scopes:
    bot:
      - chat:write
      - im:write
      - users:read
      - users:read.email
settings:
  interactivity:
    is_enabled: true
    request_url: https://YOUR-SOC-HUB-HOST/api/v1/slack/interactions
  org_deploy_enabled: false
  socket_mode_enabled: false
  token_rotation_enabled: false
```
`docs/slack/setup.md` — numbered steps: (1) api.slack.com/apps → Create New App → From a manifest → paste the manifest with your host; (2) Install to Workspace; (3) copy **Bot User OAuth Token** (`xoxb-…`) and **Signing Secret** (Basic Information); (4) SOC Hub → Integrations → Slack: paste both + default channel (e.g. `#soc-alerts`) → Save → **Test** (expects a message in the channel); (5) `/invite @SOC Hub` in every channel workflows post to; (6) local dev: `ngrok http 80`, set the interactivity URL to `https://<ngrok-id>.ngrok.app/api/v1/slack/interactions`; (7) troubleshooting table: `not_in_channel` → invite the bot; `users_not_found` → the email isn't a member of the workspace; `invalid_auth` → wrong token; buttons do nothing → the interactivity URL isn't reachable or the signing secret is wrong (check backend logs for 401s).

- [ ] **Step 3: Frontend card**

`frontend/src/features/integrations/SlackCard.tsx`:
```tsx
import { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { MessageSquare } from 'lucide-react';
import { api } from '../../api/client';

interface SlackConfig { configured: boolean; team_id: string | null; default_channel: string | null }

export default function SlackCard() {
    const qc = useQueryClient();
    const { data } = useQuery({ queryKey: ['slack-config'], queryFn: async () => (await api.get('/slack/config')).data as SlackConfig });
    const [token, setToken] = useState('');
    const [secret, setSecret] = useState('');
    const [channel, setChannel] = useState('');
    const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
    useEffect(() => { if (data) setChannel(data.default_channel ?? ''); }, [data]);

    const save = useMutation({
        mutationFn: async () => (await api.put('/slack/config', { bot_token: token || null, signing_secret: secret || null, default_channel: channel })).data,
        onSuccess: () => { setToken(''); setSecret(''); setMsg({ ok: true, text: 'Saved — now click Test' }); qc.invalidateQueries({ queryKey: ['slack-config'] }); },
        onError: (e: any) => setMsg({ ok: false, text: e?.response?.data?.detail ?? 'Save failed' }),
    });
    const test = useMutation({
        mutationFn: async () => (await api.post('/slack/config/test')).data as { team: string },
        onSuccess: (r) => { setMsg({ ok: true, text: `Connected to ${r.team}` }); qc.invalidateQueries({ queryKey: ['slack-config'] }); },
        onError: (e: any) => setMsg({ ok: false, text: e?.response?.data?.detail ?? 'Test failed' }),
    });

    const input = 'w-full border border-zinc-300 px-2 py-1.5 text-sm font-mono';
    return (
        <div className="bg-white border border-zinc-200 p-5 space-y-3">
            <div className="flex items-center gap-2">
                <MessageSquare size={16} className="text-accent-600" />
                <h2 className="font-semibold text-zinc-800">Slack</h2>
                <span className="ml-auto label-mono">{data?.team_id ? `connected · ${data.team_id}` : data?.configured ? 'saved · not tested' : 'not configured'}</span>
            </div>
            <p className="text-xs text-zinc-500">
                Create a Slack app from <code className="font-mono">docs/slack/slack-app-manifest.yml</code> and paste its credentials. Interactivity URL:{' '}
                <code className="font-mono">{window.location.origin}/api/v1/slack/interactions</code>
            </p>
            <label className="block"><span className="label-mono">bot token</span>
                <input type="password" className={input} value={token} placeholder={data?.configured ? '•••••• (unchanged)' : 'xoxb-…'} onChange={(e) => setToken(e.target.value)} /></label>
            <label className="block"><span className="label-mono">signing secret</span>
                <input type="password" className={input} value={secret} placeholder={data?.configured ? '•••••• (unchanged)' : ''} onChange={(e) => setSecret(e.target.value)} /></label>
            <label className="block"><span className="label-mono">default channel</span>
                <input className={input} value={channel} placeholder="#soc-alerts" onChange={(e) => setChannel(e.target.value)} /></label>
            <div className="flex items-center gap-2">
                <button onClick={() => save.mutate()} disabled={save.isPending} className="h-8 px-3 bg-accent-600 text-white text-sm disabled:opacity-50">Save</button>
                <button onClick={() => test.mutate()} disabled={!data?.configured || test.isPending} className="h-8 px-3 border border-zinc-300 text-sm disabled:opacity-50">Test</button>
                {msg && <span className={msg.ok ? 'text-xs text-emerald-700' : 'text-xs text-red-700'}>{msg.text}</span>}
            </div>
        </div>
    );
}
```
In `Integrations.tsx` (admin branch) render `<SlackCard />` above `<HttpAllowlistCard />`.

- [ ] **Step 4: Verify**

Restart backend; rebuild frontend. Follow `docs/slack/setup.md` against a real test workspace: Save → Test → "Connected to <workspace>" and the hello message appears in the channel. `GET /slack/config` never returns the token or secret. A wrong token → Test shows `auth.test: invalid_auth`. An analyst gets 403 on `/slack/config`.

- [ ] **Step 5: Commit**
```bash
git add backend/app/api/v1/slack.py backend/app/api/api.py frontend/src/features/integrations docs/slack
git commit -m "feat(slack): tenant Slack config, test, manifest and setup guide"
```

---

### Task 23: Interactions endpoint, case buttons, slack_post_message node

**Files:**
- Create: `backend/app/services/slack_actions.py`, `backend/app/workflows/nodes/slack.py`
- Modify: `backend/app/api/v1/slack.py`, `backend/app/tasks/workflows.py`, `backend/app/workflows/nodes/__init__.py`, `backend/app/workflows/node_types.py`, `frontend/src/features/automations/nodeCatalog.ts`

**Interfaces:**
- Consumes: `verify_signature`, `slack_call`, `respond`, `parse_action_value`, `case_buttons_block`, `load_integration`, `SlackError` (Task 21); `apply_case_update` (Task 6); `emit_event` (Task 7); `load_target_case`, `NodeError`, `executor` (Task 8).
- Produces: `POST /api/v1/slack/interactions` (public); `async handle_interaction(tenant_id: int, payload: dict)` in `slack_actions.py`; Celery `handle_slack_interaction_task(tenant_id, payload)`; executor `slack_post_message` (config `text`, `channel?`, `include_case_buttons?` = `"yes"|"no"`, `case_id?`; output `{channel, ts}`).

- [ ] **Step 1: Public interactions endpoint**

Append to `backend/app/api/v1/slack.py`:
```python
import json
import logging
from urllib.parse import parse_qs

from fastapi import Request

from app.services.slack_service import verify_signature

logger = logging.getLogger(__name__)


@router.post("/interactions")
async def interactions(request: Request, db: AsyncSession = Depends(deps.get_db)) -> Response:
    """Slack interactivity callback. Unauthenticated by design — trust comes ONLY from the
    signature check below; nothing is parsed into actions before it passes."""
    from app.tasks.workflows import handle_slack_interaction_task

    body = await request.body()
    try:
        payload = json.loads(parse_qs(body.decode())["payload"][0])
        team_id = payload["team"]["id"]
    except (KeyError, IndexError, ValueError, TypeError):
        raise HTTPException(status_code=400, detail="bad payload")

    ts = request.headers.get("X-Slack-Request-Timestamp", "")
    sig = request.headers.get("X-Slack-Signature", "")
    candidates = (await db.execute(select(SlackIntegration).where(SlackIntegration.team_id == team_id))).scalars().all()
    match = next((i for i in candidates if verify_signature(decrypt(i.signing_secret_enc), ts, body, sig)), None)
    if not match:
        logger.warning("rejected Slack interaction for team %s (bad signature or unknown team)", team_id)
        raise HTTPException(status_code=401, detail="invalid signature")

    if payload.get("type") == "block_actions":
        handle_slack_interaction_task.delay(match.tenant_id, payload)
    return Response(status_code=200)  # Slack requires an ack within 3 s; work happens in Celery
```
(Several tenants may connect the same workspace; each has its own app and signing secret, so the secret that verifies identifies the tenant.)

- [ ] **Step 2: Interaction handler (case buttons)**

`backend/app/services/slack_actions.py`:
```python
"""Handles verified Slack block_actions payloads (runs in Celery)."""
import logging
from typing import Optional

from sqlalchemy import func, or_, select

from app.db.session import AsyncSessionLocal
from app.models.case import Case, CaseStatus
from app.models.membership import TenantMembership
from app.models.user import User
from app.services.case_service import apply_case_update
from app.services.slack_service import SlackError, load_integration, parse_action_value, respond, slack_call
from app.workflows.events import emit_event

logger = logging.getLogger(__name__)

_CASE_UPDATES = {
    "ack": ("Acknowledged", lambda user: {"status": CaseStatus.IN_PROGRESS}),
    "assign": ("Assigned", lambda user: {"owner_id": user.id}),
    "close": ("Closed", lambda user: {"status": CaseStatus.CLOSED}),
}


async def _ephemeral(payload: dict, text: str) -> None:
    if payload.get("response_url"):
        await respond(payload["response_url"], {"response_type": "ephemeral", "replace_original": False, "text": text})


async def _slack_email(token: str, slack_user_id: str) -> Optional[str]:
    try:
        info = await slack_call(token, "users.info", user=slack_user_id)
    except SlackError:
        return None
    return (info.get("user") or {}).get("profile", {}).get("email")


async def _platform_user(db, tenant_id: int, email: Optional[str]) -> Optional[User]:
    if not email:
        return None
    return (await db.execute(
        select(User).outerjoin(TenantMembership, (TenantMembership.user_id == User.id) & (TenantMembership.tenant_id == tenant_id))
        .where(func.lower(User.email) == email.lower(), User.is_active == True,  # noqa: E712
               or_(User.is_super_admin == True, TenantMembership.role.in_(["admin", "analyst"])))  # noqa: E712
    )).scalars().first()


async def _handle_case_action(tenant_id: int, payload: dict, btn_tenant_id: int, case_id: int, action: str) -> None:
    if btn_tenant_id != tenant_id:
        return  # button belongs to another tenant's message
    clicker = payload["user"]["id"]
    async with AsyncSessionLocal() as db:
        _, token = await load_integration(db, tenant_id)
        user = await _platform_user(db, tenant_id, await _slack_email(token, clicker))
        if not user:
            await _ephemeral(payload, "Your Slack account isn't linked to a SOC Hub user with analyst access in this tenant.")
            return
        case = (await db.execute(select(Case).where(Case.id == case_id, Case.tenant_id == tenant_id))).scalars().first()
        if not case:
            await _ephemeral(payload, f"Case #{case_id} no longer exists.")
            return
        label, build = _CASE_UPDATES[action]
        changes = await apply_case_update(db, case=case, update_data=build(user), user_id=user.id)
        await db.commit()
    if changes:
        emit_event(tenant_id, "case.updated", case_id=case_id, changes=changes)
    await respond(payload["response_url"], {"response_type": "in_channel", "replace_original": False,
                                            "text": f"{label} case #{case_id} — <@{clicker}>"})


async def handle_interaction(tenant_id: int, payload: dict) -> None:
    for action in payload.get("actions") or []:
        parsed = parse_action_value(action.get("value", ""))
        if not parsed:
            continue
        try:
            if parsed[0] == "case":
                await _handle_case_action(tenant_id, payload, *parsed[1:])
        except SlackError:
            logger.exception("Slack interaction failed for tenant %s", tenant_id)
```
Append to `backend/app/tasks/workflows.py`:
```python
@celery_app.task(acks_late=True)
def handle_slack_interaction_task(tenant_id, payload):
    from app.services.slack_actions import handle_interaction
    _run_async(handle_interaction(tenant_id, payload))
```

- [ ] **Step 3: slack_post_message node**

`backend/app/workflows/nodes/slack.py`:
```python
from app.services.slack_service import SlackError, case_buttons_block, load_integration, slack_call
from app.workflows.nodes import NodeContext, NodeError, executor, load_target_case


async def _integration(nctx: NodeContext):
    try:
        return await load_integration(nctx.db, nctx.run.tenant_id)
    except SlackError as e:
        raise NodeError(str(e))


@executor("slack_post_message")
async def run_post_message(nctx: NodeContext, config: dict) -> dict:
    integ, token = await _integration(nctx)
    channel = config.get("channel") or integ.default_channel
    if not channel:
        raise NodeError("no channel given and no default channel configured")
    text = str(config["text"])
    blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": text}}]
    if str(config.get("include_case_buttons", "no")).lower() in ("yes", "true", "1"):
        case = await load_target_case(nctx, config)
        blocks.append(case_buttons_block(nctx.run.tenant_id, case.id))
    try:
        res = await slack_call(token, "chat.postMessage", channel=channel, text=text, blocks=blocks)
    except SlackError as e:
        raise NodeError(str(e))
    return {"channel": res["channel"], "ts": res["ts"]}
```
`nodes/__init__.py`: change the registration import to `from app.workflows.nodes import logic, http, cases, alerts, slack  # noqa: E402,F401`.
`node_types.py`: add `"slack_post_message": {"required": ["text"], "raw": [], "alert_only": False},`.
`nodeCatalog.ts`: add `MessageSquare` to imports and:
```ts
    {
        type: 'slack_post_message', label: 'Slack message', category: 'Slack', icon: MessageSquare, sideEffect: true,
        fields: [
            { key: 'channel', label: 'Channel', kind: 'text', placeholder: 'default channel' },
            { key: 'text', label: 'Message (mrkdwn)', kind: 'textarea', required: true, placeholder: ':rotating_light: *{{ case.title }}* ({{ case.severity }})' },
            { key: 'include_case_buttons', label: 'Case buttons', kind: 'select', options: ['no', 'yes'], help: 'Acknowledge / Assign to me / Close' },
            CASE_ID,
        ],
    },
```

- [ ] **Step 4: Verify with a real workspace (ngrok)**

1. Unsigned request: `curl -i -X POST localhost:8000/api/v1/slack/interactions -d 'payload={"type":"block_actions","team":{"id":"T123"}}'` → **401**. Garbage body → **400**.
2. Workflow: trigger *Case created*, filter `case.severity == 'critical'`, node `slack_post_message` text `:rotating_light: *{{ case.title }}*`, case buttons **yes**. Enable. Create a critical case → the message appears with 3 buttons.
3. Click **Acknowledge** as a Slack user whose email matches an analyst → case status becomes `in_progress`, timeline shows the status change by that user, the channel shows "Acknowledged case #N — @you".
4. Click **Assign to me** from a Slack user with no SOC Hub account → ephemeral "isn't linked" message; the case is unchanged.
5. Dry-run the workflow → the step is SIMULATED; nothing is posted.

- [ ] **Step 5: Commit**
```bash
git add backend/app frontend/src/features/automations/nodeCatalog.ts
git commit -m "feat(slack): verified interactions endpoint, case buttons, post-message node"
```

---

# Phase 4 — Ask the end user and wait

### Task 24: slack_ask_user node (send + wait)

**Files:**
- Modify: `backend/app/workflows/nodes/slack.py`, `backend/app/workflows/node_types.py`, `backend/app/workflows/validation.py`, `frontend/src/features/automations/nodeCatalog.ts`
- Test: `backend/tests/test_wf_validation.py` (append)

**Interfaces:**
- Consumes: `ask_blocks`, `slack_call` (Task 21), `WAIT` (Task 8).
- Produces: executor `slack_ask_user` — config `email`, `message`, `buttons?` (comma string or list; default `Yes, No`; max 5), `timeout_hours?` (default 24, max 168). While waiting: `step.wait_token`, `step.wait_expires_at`, `step.output = {target_slack_id, channel, message_ts, buttons}`.

- [ ] **Step 1: Append failing validation test**

```python
def test_ask_user_limits():
    g = {"nodes": [n("start", "trigger"),
                   n("ask", "slack_ask_user", config={"email": "{{ case.title }}", "message": "ok?", "timeout_hours": 200,
                                                      "buttons": "a,b,c,d,e,f"})],
         "edges": [e("start", "ask")]}
    m = msgs(validate_graph(g, "manual"))
    assert "timeout_hours" in m and "buttons" in m
```
Run: `docker compose exec backend python -m pytest tests/test_wf_validation.py -v` → FAIL.

- [ ] **Step 2: Registry + validation**

`node_types.py`: `"slack_ask_user": {"required": ["email", "message"], "raw": [], "alert_only": False},`
`validation.py` in `_check_node_config`, after the `alert_promote` block:
```python
    if node["type"] == "slack_ask_user":
        buttons = cfg.get("buttons")
        labels = [b for b in (buttons.split(",") if isinstance(buttons, str) else (buttons or [])) if str(b).strip()]
        if len(labels) > 5:
            _err(errors, nid, "at most 5 buttons")
        th = cfg.get("timeout_hours")
        if th not in (None, ""):
            try:
                ok = 0 < float(th) <= 168
            except (TypeError, ValueError):
                ok = False
            if not ok:
                _err(errors, nid, "timeout_hours must be between 0 and 168")
```
Run the tests → PASS.

- [ ] **Step 3: Executor**

Append to `backend/app/workflows/nodes/slack.py`:
```python
import secrets
from datetime import datetime, timedelta, timezone

from app.services.slack_service import ask_blocks
from app.workflows.nodes import WAIT


def button_labels(raw) -> list:
    labels = raw.split(",") if isinstance(raw, str) else (raw or [])
    labels = [str(b).strip() for b in labels if str(b).strip()]
    return labels or ["Yes", "No"]


@executor("slack_ask_user")
async def run_ask_user(nctx: NodeContext, config: dict):
    _, token = await _integration(nctx)
    buttons = button_labels(config.get("buttons"))
    if len(buttons) > 5:
        raise NodeError("at most 5 buttons")
    hours = float(config.get("timeout_hours") or 24)
    if not 0 < hours <= 168:
        raise NodeError("timeout_hours must be between 0 and 168")
    wait_token = secrets.token_urlsafe(24)
    try:
        user_id = (await slack_call(token, "users.lookupByEmail", email=str(config["email"]).strip()))["user"]["id"]
        channel = (await slack_call(token, "conversations.open", users=user_id))["channel"]["id"]
        msg = await slack_call(token, "chat.postMessage", channel=channel, text=str(config["message"]),
                               blocks=ask_blocks(str(config["message"]), buttons, wait_token))
    except SlackError as e:
        raise NodeError(f"could not ask {config['email']}: {e}")
    nctx.step.wait_token = wait_token
    nctx.step.wait_expires_at = datetime.now(timezone.utc) + timedelta(hours=hours)
    nctx.step.output = {"target_slack_id": user_id, "channel": channel, "message_ts": msg["ts"], "buttons": buttons}
    return WAIT
```

- [ ] **Step 4: Catalog entry**

`nodeCatalog.ts` (import `MessageCircleQuestion`):
```ts
    {
        type: 'slack_ask_user', label: 'Ask user (Slack)', category: 'Slack', icon: MessageCircleQuestion, sideEffect: true,
        fields: [
            { key: 'email', label: 'User email', kind: 'text', required: true, placeholder: '{{ alert.payload.user_email }}' },
            { key: 'message', label: 'Question (mrkdwn)', kind: 'textarea', required: true, placeholder: 'Did you just sign in from {{ alert.payload.country }}?' },
            { key: 'buttons', label: 'Buttons (comma-separated, ≤ 5)', kind: 'text', placeholder: 'Yes, No' },
            { key: 'timeout_hours', label: 'Timeout (hours, ≤ 168)', kind: 'number', placeholder: '24' },
        ],
    },
```
Help text for downstream conditions (add as `help` on `buttons`): `Branch on steps.<id>.output.response == 'No' or steps.<id>.output.timed_out`.

- [ ] **Step 5: Verify** — `npm run build`; restart backend/worker; rebuild frontend. A manual workflow start → ask (your own Slack email) → note `answer: {{ steps.ask.output.response }}`: run it on a case → you get a DM with the buttons; the run detail shows `ask` amber (**waiting**) and the run status is `waiting`. (Answering is wired in Task 25 — clicking now does nothing yet.) Cancel the run from the UI → status `cancelled`.

- [ ] **Step 6: Commit**
```bash
git add backend/app/workflows backend/tests/test_wf_validation.py frontend/src/features/automations/nodeCatalog.ts
git commit -m "feat(slack): ask-user node that DMs and waits"
```

---

### Task 25: Receive answers and resume the run

**Files:**
- Modify: `backend/app/services/slack_actions.py`
- Test: `backend/tests/test_slack_answers.py`

**Interfaces:**
- Consumes: `load_integration`, `slack_call`, `respond` (Task 21); `advance_run_task` (Task 8); `WorkflowRun`, `WorkflowRunStep` (Task 6).
- Produces: `authorize_answer(step_status: str, target_slack_id: str | None, clicker_id: str) -> str | None` (None = allowed, else the ephemeral message); `async handle_answer(tenant_id, payload, wait_token, index)`. After an answer, step output = `{response, responder_slack_id, responder_email, timed_out: False, channel, message_ts, buttons}`.

- [ ] **Step 1: Failing tests**

`backend/tests/test_slack_answers.py`:
```python
from app.services.slack_actions import authorize_answer


def test_authorize_answer_target_user_while_waiting():
    assert authorize_answer("waiting", "U1", "U1") is None


def test_authorize_answer_wrong_user():
    assert "isn't for you" in authorize_answer("waiting", "U1", "U2")


def test_authorize_answer_already_answered_or_expired():
    for status in ("succeeded", "cancelled", "failed"):
        assert "already" in authorize_answer(status, "U1", "U1")


def test_authorize_answer_missing_target():
    assert authorize_answer("waiting", None, "U1") is not None
```
Run → FAIL (`ImportError`).

- [ ] **Step 2: Implement**

Add to `backend/app/services/slack_actions.py`:
```python
from datetime import datetime, timezone

from app.models.workflow import WorkflowRun, WorkflowRunStep


def authorize_answer(step_status: str, target_slack_id: Optional[str], clicker_id: str) -> Optional[str]:
    if step_status != "waiting":
        return "This request was already answered or has expired."
    if not target_slack_id or target_slack_id != clicker_id:
        return "This request isn't for you."
    return None


async def handle_answer(tenant_id: int, payload: dict, wait_token: str, index: int) -> None:
    from app.tasks.workflows import advance_run_task

    clicker = payload["user"]["id"]
    async with AsyncSessionLocal() as db:
        step = (await db.execute(
            select(WorkflowRunStep).join(WorkflowRun, WorkflowRun.id == WorkflowRunStep.run_id)
            .where(WorkflowRunStep.wait_token == wait_token, WorkflowRun.tenant_id == tenant_id)
            .with_for_update(of=WorkflowRunStep)
        )).scalars().first()
        if not step:
            await _ephemeral(payload, "This request was already answered or has expired.")
            return
        waiting = dict(step.output or {})
        reason = authorize_answer(step.status, waiting.get("target_slack_id"), clicker)
        buttons = waiting.get("buttons") or []
        if reason or not 0 <= index < len(buttons):
            await _ephemeral(payload, reason or "Unknown option.")
            return
        _, token = await load_integration(db, tenant_id)
        label = buttons[index]
        step.output = {**waiting, "response": label, "responder_slack_id": clicker,
                       "responder_email": await _slack_email(token, clicker), "timed_out": False}
        step.status, step.finished_at = "succeeded", datetime.now(timezone.utc)
        await db.commit()
        run_id = step.run_id

    try:
        await slack_call(token, "chat.update", channel=waiting["channel"], ts=waiting["message_ts"],
                         text=f"You answered: {label}",
                         blocks=[{"type": "section", "text": {"type": "mrkdwn", "text": f":white_check_mark: You answered: *{label}*"}}])
    except SlackError:
        logger.warning("could not update answered Slack message for step %s", step.id)
    advance_run_task.delay(run_id)
```
In `handle_interaction`, extend the dispatch:
```python
            if parsed[0] == "case":
                await _handle_case_action(tenant_id, payload, *parsed[1:])
            elif parsed[0] == "wf":
                await handle_answer(tenant_id, payload, parsed[1], parsed[2])
```

- [ ] **Step 3: Run tests → PASS.**

- [ ] **Step 4: Verify** — restart backend + worker. Re-run the Task 24 workflow: click **No** in the DM → the message changes to "✅ You answered: *No*" (buttons gone), the run resumes, the note says `answer: No`, the ask step output shows `responder_email`. Click again via Slack's message history (if still visible elsewhere) or re-send the same payload → ephemeral "already answered". Ask a colleague's email and click the button from **your** account in a shared view (or replay the payload with a different `user.id` through a signed test request) → "isn't for you", run still waiting.

- [ ] **Step 5: Commit**
```bash
git add backend/app/services/slack_actions.py backend/tests/test_slack_answers.py
git commit -m "feat(slack): accept end-user answers and resume waiting runs"
```

---

### Task 26: Timeouts, dry-run answers, cancel of waiting runs

**Files:**
- Modify: `backend/app/workflows/dryrun.py`, `backend/app/workflows/runtime.py`, `backend/app/tasks/workflows.py`, `backend/app/worker.py`
- Test: `backend/tests/test_wf_dryrun.py` (append)

**Interfaces:**
- Consumes: `load_integration`, `slack_call` (Task 21), `button_labels` (Task 24).
- Produces: `simulated_output` handles `slack_ask_user` via `dry_run_mocks["ask_user_answers"]`; `async expire_waits()` in `runtime.py`; Celery `expire_waits_task` on a 60 s beat.

- [ ] **Step 1: Failing dry-run tests**

Append to `backend/tests/test_wf_dryrun.py`:
```python
def test_ask_user_dry_run_answers():
    node = {"id": "ask", "type": "slack_ask_user"}
    rendered = {"email": "a@x.com", "message": "ok?", "buttons": "Yes, No"}
    default = simulated_output(node, rendered, NO_MOCKS)
    assert default["response"] == "Yes" and default["timed_out"] is False and default["simulated"] is True
    no = simulated_output(node, rendered, {"mocks": {}, "ask_user_answers": {"ask": "No"}})
    assert no["response"] == "No"
    t = simulated_output(node, rendered, {"mocks": {}, "ask_user_answers": {"ask": "timeout"}})
    assert t["response"] is None and t["timed_out"] is True
```
The default answer is the **first** button. Run → FAIL.

- [ ] **Step 2: Implement dry-run answers**

In `backend/app/workflows/dryrun.py`, at the top of `simulated_output`:
```python
    if node["type"] == "slack_ask_user":
        from app.workflows.nodes.slack import button_labels
        answer = ((dry_run_mocks or {}).get("ask_user_answers") or {}).get(node["id"])
        if answer == "timeout":
            out = {"response": None, "timed_out": True}
        else:
            out = {"response": answer or button_labels(rendered_input.get("buttons"))[0], "timed_out": False}
        return {**out, "responder_slack_id": None, "responder_email": None, "simulated": True, "would_do": rendered_input}
```
Run tests → PASS.

- [ ] **Step 3: Expiry sweep**

Append to `backend/app/workflows/runtime.py`:
```python
async def expire_waits() -> None:
    from app.services.slack_service import SlackError, load_integration, slack_call
    from app.tasks.workflows import advance_run_task

    expired = []
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(
            select(WorkflowRunStep, WorkflowRun.tenant_id).join(WorkflowRun, WorkflowRun.id == WorkflowRunStep.run_id)
            .where(WorkflowRunStep.status == "waiting", WorkflowRunStep.wait_expires_at.isnot(None),
                   WorkflowRunStep.wait_expires_at < _now())
            .with_for_update(of=WorkflowRunStep, skip_locked=True).limit(200)
        )).all()
        for step, tenant_id in rows:
            out = dict(step.output or {})
            expired.append((step.run_id, tenant_id, out.get("channel"), out.get("message_ts")))
            step.output = {**out, "response": None, "responder_slack_id": None, "responder_email": None, "timed_out": True}
            step.status, step.finished_at = "succeeded", _now()
        await db.commit()

        for run_id, tenant_id, channel, ts in expired:
            if channel and ts:
                try:
                    _, token = await load_integration(db, tenant_id)
                    await slack_call(token, "chat.update", channel=channel, ts=ts, text="This request expired.",
                                     blocks=[{"type": "section", "text": {"type": "mrkdwn", "text": ":hourglass: This request expired."}}])
                except SlackError:
                    logger.warning("could not mark Slack message expired for run %s", run_id)
    for run_id, *_ in expired:
        advance_run_task.delay(run_id)
```
Append to `backend/app/tasks/workflows.py`:
```python
@celery_app.task(acks_late=True)
def expire_waits_task():
    from app.workflows.runtime import expire_waits
    _run_async(expire_waits())
```
In `backend/app/worker.py` add to `beat_schedule`:
```python
    "expire-workflow-waits": {
        "task": "app.tasks.workflows.expire_waits_task",
        "schedule": 60.0,
    },
```

- [ ] **Step 4: Cancelling a waiting run edits the Slack message**

In `runtime.py` `cancel_run`, before `await finalize_run(db, run, "cancelled")`, collect waiting ask steps, and after finalize append a best-effort message update. Replace `cancel_run` with:
```python
async def cancel_run(db, run: WorkflowRun) -> None:
    if run.status in _TERMINAL_RUN:
        return
    asks = [s for s in (await db.execute(select(WorkflowRunStep).where(
        WorkflowRunStep.run_id == run.id, WorkflowRunStep.status == "waiting",
        WorkflowRunStep.wait_token.isnot(None)))).scalars().all()]
    await finalize_run(db, run, "cancelled")
    if asks:
        from app.services.slack_service import SlackError, load_integration, slack_call
        try:
            _, token = await load_integration(db, run.tenant_id)
            for s in asks:
                out = s.output or {}
                if out.get("channel") and out.get("message_ts"):
                    await slack_call(token, "chat.update", channel=out["channel"], ts=out["message_ts"],
                                     text="This request was withdrawn.",
                                     blocks=[{"type": "section", "text": {"type": "mrkdwn", "text": ":no_entry_sign: This request was withdrawn."}}])
        except SlackError:
            logger.warning("could not withdraw Slack question for run %s", run.id)
    children = (await db.execute(select(WorkflowRun).where(WorkflowRun.parent_run_id == run.id))).scalars().all()
    for child in children:
        await cancel_run(db, child)
```

- [ ] **Step 5: Verify** — restart backend + worker. Ask-user workflow with `timeout_hours` `0.02` (~1 min): within ~2 min the DM changes to "This request expired", the step output has `timed_out: true`, and a downstream condition on `steps.ask.output.timed_out` takes its **true** branch. A new run cancelled while waiting → the DM says "withdrawn"; clicking a button (if it's still visible) → ephemeral "already answered or has expired". Dry run with "— times out —" selected → the timeout branch runs, SIMULATED, no DM.

- [ ] **Step 6: Commit**
```bash
git add backend/app backend/tests/test_wf_dryrun.py
git commit -m "feat(slack): ask-user timeouts, dry-run answers, withdraw on cancel"
```

---

### Task 27: End-to-end acceptance + docs

**Files:**
- Modify: `docs/workflows/README.md`, `docs/features.md` (add a short Automations + Slack section if the file lists features)

- [ ] **Step 1: Motivating example 1 (case → webhook → Slack confirm)**

Build: trigger *Case created*, filter `'x' in case.tags`:
`hook` http_request POST `https://webhook.site/<your-id>` body `{"case": "{{ case.id }}", "title": "{{ case.title }}"}` → `ask` slack_ask_user email `<test user email>`, message `Did you report "{{ case.title }}"?`, buttons `Yes, No`, timeout 1 → `check` condition `steps.ask.output.response == 'No' or steps.ask.output.timed_out` → **true**: `escalate` case_update severity `critical` → `esc_note` case_add_note `Escalated: user answered {{ steps.ask.output.response or 'nothing (timeout)' }}`; **false**: `ok_note` case_add_note `Confirmed by {{ steps.ask.output.responder_email }}` → `close` case_update status `closed`.
1. Dry run with answer **No** → the escalate branch runs SIMULATED; close is skipped (gray).
2. Enable. Create a case tagged `x` → webhook.site receives the POST; the DM arrives; answer **No** → the case becomes critical with the note. Repeat, answer **Yes** → note + closed. Repeat and don't answer → after ~1 h (or with `timeout_hours` 0.02 for the test) escalated with "nothing (timeout)".

- [ ] **Step 2: Motivating example 2 (alert → group promote → ask each user)**

Trigger *Alert ingested*, filter `alert.payload.severity is defined and alert.payload.severity >= 7`: `promote` (group by `host:{{ alert.payload.host }}`, window 6) → `users` for_each items `alert.payload.users`, concurrency 5, body: `ask` (email `{{ loop.item }}`, message `Unusual activity on {{ alert.payload.host }} — was this you?`) → after the loop: `check` condition `steps.users.output.results | selectattr('steps.ask.response', 'equalto', 'No') | list | length > 0` → true: case_update severity critical.
Dry run against a pending alert whose payload has `"users": ["a@x.com", "b@x.com"]` → 2 child runs, all simulated. Then ingest such an alert for real (with two real Slack users) → one case, two DMs; one "No" → case critical.
(If the `selectattr` expression is awkward for users, document the alternative: put a condition + case_update **inside** the loop body.)

- [ ] **Step 3: Security spot checks**
- Replay a captured Slack interaction body after 6 minutes → 401.
- `http_request` to `http://169.254.169.254/latest/meta-data/` → step fails with the "non-public address" message.
- Template `{{ ''.__class__.__mro__ }}` in a note → step fails with a template error; the worker keeps running.
- Viewer: can see Automations and runs; cannot save, enable, dry-run, run, or cancel (403 from the API).
- Another tenant's workflow/run ids → 404.

- [ ] **Step 4: Full backend suite** — `docker compose exec backend python -m pytest tests -v` → all PASS. Frontend: `npm run build` and `npm run lint` → clean.

- [ ] **Step 5: Docs** — extend `docs/workflows/README.md` with the Slack nodes (config, outputs, waiting/timeout/withdraw behaviour, dry-run answers) and both worked examples above; link `docs/slack/setup.md`.

- [ ] **Step 6: Commit**
```bash
git add docs
git commit -m "docs: automations + Slack end-to-end examples"
```
