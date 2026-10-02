"""HTTP node: send-time secret substitution, output/error redaction, persistence hygiene."""
import asyncio
import json
import logging
import re
import secrets as pysecrets
from types import SimpleNamespace
from urllib.parse import quote_plus

import httpx
import pytest
from sqlalchemy import delete, func, select, text

import app.db.base  # noqa: F401
from app.db.session import AsyncSessionLocal, engine
from app.models.tenant import Tenant
from app.models.tenant_secret import TenantSecret
from app.models.workflow import Workflow, WorkflowRun, WorkflowRunStep
from app.secrets.refs import placeholder
from app.utils.crypto import encrypt
from app.workflows import runtime
from app.workflows.nodes import NodeContext, NodeError, RetryableNodeError
from app.workflows.nodes import http as http_node

TOK = "s3cr3t&tok en/1"
N = "0123456789abcdef"
PH = placeholder("TOK", N)


def _install(monkeypatch, handler):
    real = httpx.AsyncClient
    monkeypatch.setattr(http_node.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    monkeypatch.setattr(http_node, "assert_url_allowed", lambda url, allow: None)


async def _with_tenant(body):
    await engine.dispose()
    h = pysecrets.token_hex(3)
    ids = {"t": [], "s": [], "w": []}
    try:
        async with AsyncSessionLocal() as db:
            t = Tenant(name="wf-test", slug=f"wf-test-{h}-hn")
            db.add(t)
            await db.flush()
            ids["t"].append(t.id)
            s = TenantSecret(tenant_id=t.id, name="TOK", value_enc=encrypt(TOK), allowed_hosts=["api.example.com"])
            db.add(s)
            await db.flush()
            ids["s"].append(s.id)
            await db.commit()
            await body(db, t.id, ids)
    finally:
        async with AsyncSessionLocal() as db:
            if ids["w"]:
                await db.execute(delete(Workflow).where(Workflow.id.in_(ids["w"])))  # runs/steps cascade
            if ids["s"]:
                await db.execute(delete(TenantSecret).where(TenantSecret.id.in_(ids["s"])))
            if ids["t"]:
                await db.execute(delete(Tenant).where(Tenant.id.in_(ids["t"])))
            await db.commit()
            left = (await db.execute(select(func.count()).select_from(Tenant).where(Tenant.slug.like("wf-test-%")))).scalar()
            assert left == 0
        await engine.dispose()


def _nctx(db, tid, nonce=N):
    return NodeContext(db=db, run=SimpleNamespace(tenant_id=tid), step=None, node={}, ctx={}, secret_nonce=nonce)


def test_substitution_and_output_redaction(monkeypatch):
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"echo": TOK, "f": "a=" + quote_plus(TOK)}, headers={"X-Echo": TOK})

    async def body(db, tid, ids):
        _install(monkeypatch, handler)
        cfg = {"method": "POST", "url": "https://api.example.com/x", "headers": {"Authorization": f"Bearer {PH}"},
               "body": {"k": PH}}
        out = await http_node.run_http(_nctx(db, tid), cfg)
        assert seen[0].headers["authorization"] == f"Bearer {TOK}"
        assert json.loads(seen[0].content) == {"k": TOK}
        assert "••••" in out["body"]["echo"] and "••••" in out["body"]["f"] and out["headers"]["x-echo"] == "••••"
        dumped = json.dumps(out)
        assert TOK not in dumped and quote_plus(TOK) not in dumped

    asyncio.run(_with_tenant(body))


def test_disallowed_host_blocked_and_logged(monkeypatch, caplog):
    calls = []

    async def body(db, tid, ids):
        _install(monkeypatch, lambda r: calls.append(r) or httpx.Response(200))
        cfg = {"method": "GET", "url": "https://evil.example.org/x", "headers": {"Authorization": PH}}
        with caplog.at_level(logging.INFO, logger=http_node.logger.name):
            with pytest.raises(NodeError):
                await http_node.run_http(_nctx(db, tid), cfg)
        assert not calls
        msgs = [r.getMessage() for r in caplog.records if "secret use" in r.getMessage()]
        assert msgs and "outcome=blocked" in msgs[0] and "names=['TOK']" in msgs[0] and TOK not in msgs[0]

    asyncio.run(_with_tenant(body))


def test_transport_error_redacted_and_logged(monkeypatch, caplog):
    def handler(request):
        raise httpx.ConnectError(f"cannot connect to {request.url}")

    async def body(db, tid, ids):
        _install(monkeypatch, handler)
        cfg = {"method": "GET", "url": "https://api.example.com/x?k=" + PH}
        with caplog.at_level(logging.INFO, logger=http_node.logger.name):
            with pytest.raises(RetryableNodeError) as ei:
                await http_node.run_http(_nctx(db, tid), cfg)
        msg = str(ei.value)
        assert "••••" in msg and TOK not in msg and quote_plus(TOK) not in msg
        logs = [r.getMessage() for r in caplog.records if "secret use" in r.getMessage()]
        assert "outcome=error" in logs[0] and "host=api.example.com" in logs[0]

    asyncio.run(_with_tenant(body))


def test_5xx_message_redacted(monkeypatch):
    async def body(db, tid, ids):
        _install(monkeypatch, lambda r: httpx.Response(502))
        with pytest.raises(RetryableNodeError) as ei:
            await http_node.run_http(_nctx(db, tid), {"url": "https://api.example.com/x?k=" + PH})
        assert "HTTP 502" in str(ei.value) and quote_plus(TOK) not in str(ei.value)

    asyncio.run(_with_tenant(body))


def test_pinned_target_uses_substituted_url(monkeypatch):
    seen = []

    async def body(db, tid, ids):
        _install(monkeypatch, lambda r: seen.append(r) or httpx.Response(200, text="ok"))
        monkeypatch.setattr(http_node, "assert_url_allowed", lambda url, allow: "93.184.216.34")
        await http_node.run_http(_nctx(db, tid), {"url": "https://api.example.com/p/" + PH + "?q=1"})
        r = seen[0]
        assert r.url.host == "93.184.216.34" and r.headers["host"] == "api.example.com"
        assert "%2F" in r.url.raw_path.decode() and "s3cr3t" in r.url.raw_path.decode()
        assert PH not in str(r.url)

    asyncio.run(_with_tenant(body))


def test_no_placeholder_no_resolution(monkeypatch):
    async def boom(*a, **k):
        raise AssertionError("resolver must not run")

    monkeypatch.setattr(http_node, "resolve_for_request", boom)
    _install(monkeypatch, lambda r: httpx.Response(200, text="ok"))
    out = asyncio.run(http_node.run_http(_nctx(SimpleNamespace(execute=_noop_exec), 1), {"url": "https://example.com/"}))
    assert out["status"] == 200


async def _noop_exec(*a, **k):
    return SimpleNamespace(scalars=lambda: SimpleNamespace(first=lambda: SimpleNamespace(workflow_http_allowlist=[])))


GRAPH = {"nodes": [{"id": "start", "type": "trigger", "config": {}},
                   {"id": "call", "type": "http_request",
                    "config": {"method": "GET", "url": "https://api.example.com/x?k={{ secrets.TOK }}",
                               "headers": {"Authorization": "Bearer {{ secrets.TOK }}"}}}],
         "edges": [{"id": "e1", "source": "start", "target": "call", "source_handle": None}]}


def _runtime_case(monkeypatch, *, dry_run, handler):
    import app.tasks.workflows as tw
    monkeypatch.setattr(tw.advance_run_task, "delay", lambda rid: None)
    _install(monkeypatch, handler)

    async def body(db, tid, ids):
        wf = Workflow(tenant_id=tid, name="wf-test", trigger_type="manual", graph=GRAPH)
        db.add(wf)
        await db.flush()
        ids["w"].append(wf.id)
        run = WorkflowRun(tenant_id=tid, workflow_id=wf.id, workflow_version=1, status="running", trigger_payload={},
                          graph_snapshot=GRAPH, is_dry_run=dry_run)
        db.add(run)
        await db.flush()
        step = WorkflowRunStep(run_id=run.id, node_id="call", status="pending")
        db.add(step)
        await db.flush()
        sid, rid = step.id, run.id
        await db.commit()
        await runtime.execute_step(sid)
        db.expire_all()
        st = (await db.execute(select(WorkflowRunStep).where(WorkflowRunStep.id == sid))).scalars().one()
        rows = (await db.execute(text("SELECT input::text, output::text, error FROM workflow_run_steps WHERE run_id=:id"),
                                 {"id": rid})).all()
        last = (await db.execute(select(TenantSecret.last_used_at).where(TenantSecret.id == ids["s"][0]))).scalar()
        return st, rows, last

    return body


def test_runtime_persists_only_placeholders(monkeypatch):
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"echo": TOK}, headers={"X-Echo": TOK})

    out = {}

    async def go():
        inner = _runtime_case(monkeypatch, dry_run=False, handler=handler)

        async def body(db, tid, ids):
            out["r"] = await inner(db, tid, ids)
        await _with_tenant(body)

    asyncio.run(go())
    st, rows, last = out["r"]
    assert st.status == "succeeded", st.error
    assert re.search(r"⟦secret:TOK#[0-9a-f]{16}⟧", st.input["headers"]["Authorization"])
    assert seen[0].headers["authorization"] == f"Bearer {TOK}"
    blob = " ".join(str(c) for r in rows for c in r)
    assert TOK not in blob and quote_plus(TOK) not in blob and "\\u2022\\u2022\\u2022\\u2022" in blob
    assert last is not None


def test_dry_run_keeps_placeholder_and_no_last_used(monkeypatch):
    calls = []
    out = {}

    async def go():
        inner = _runtime_case(monkeypatch, dry_run=True, handler=lambda r: calls.append(r) or httpx.Response(200))

        async def body(db, tid, ids):
            out["r"] = await inner(db, tid, ids)
        await _with_tenant(body)

    asyncio.run(go())
    st, rows, last = out["r"]
    assert st.status == "succeeded" and not calls
    assert "⟦secret:TOK#" in st.input["headers"]["Authorization"]
    assert last is None
