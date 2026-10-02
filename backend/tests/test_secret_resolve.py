import asyncio
import secrets as pysecrets

import pytest
from sqlalchemy import delete, select, update

import app.db.base  # noqa: F401  (registers all mappers)
from app.db.session import AsyncSessionLocal, engine
from app.models.tenant import Tenant
from app.models.tenant_secret import TenantSecret
from app.secrets.refs import placeholder
from app.secrets.resolve import redact, redact_text, resolve_for_request
from app.utils.crypto import encrypt
from app.workflows.nodes import NodeError

TOK_V = "s3cr3t value&x"
KEY_V = "k3y\r\nX-Evil: 1"
N = "0123456789abcdef"
OTHER = "fedcba9876543210"
P_TOK, P_KEY = placeholder("TOK", N), placeholder("KEY", N)


async def _scenario(body):
    await engine.dispose()
    h = pysecrets.token_hex(3)
    ids = {"t": [], "s": []}
    try:
        async with AsyncSessionLocal() as db:
            t = Tenant(name="wf-test", slug=f"wf-test-{h}-sr")
            db.add(t)
            await db.flush()
            ids["t"].append(t.id)
            rows = {}
            for name, val, hosts in [("TOK", TOK_V, ["api.example.com"]), ("KEY", KEY_V, ["api.example.com"])]:
                r = TenantSecret(tenant_id=t.id, name=name, value_enc=encrypt(val), allowed_hosts=hosts)
                db.add(r)
                await db.flush()
                ids["s"].append(r.id)
                rows[name] = r.id
            await db.commit()
            await body(db, t.id, rows)
    finally:
        async with AsyncSessionLocal() as db:
            if ids["s"]:
                await db.execute(delete(TenantSecret).where(TenantSecret.id.in_(ids["s"])))
            if ids["t"]:
                await db.execute(delete(Tenant).where(Tenant.id.in_(ids["t"])))
            await db.commit()
        await engine.dispose()


def run(body):
    asyncio.run(_scenario(body))


H = "api.example.com"


def test_header_query_path_body_and_last_used():
    async def body(db, tid, rows):
        url, hd, bd, used = await resolve_for_request(
            db, tid, f"https://api.example.com/v/{P_TOK}/x?k={P_TOK}#frag{P_TOK}",
            {"Authorization": f"Bearer {P_TOK}", "X": "plain"},
            {"a": [f"t={P_TOK}", 1], "b": {"c": P_TOK}}, H, N)
        assert url == "https://api.example.com/v/s3cr3t%20value%26x/x?k=s3cr3t+value%26x"
        assert hd == {"Authorization": f"Bearer {TOK_V}", "X": "plain"}
        assert bd == {"a": [f"t={TOK_V}", 1], "b": {"c": TOK_V}}
        assert used == {"TOK": TOK_V}
        await db.commit()
        r = (await db.execute(select(TenantSecret).where(TenantSecret.id == rows["TOK"]))).scalar_one()
        assert r.last_used_at is not None
        k = (await db.execute(select(TenantSecret).where(TenantSecret.id == rows["KEY"]))).scalar_one()
        assert k.last_used_at is None
    run(body)


def test_string_body_and_no_nonce_noop():
    async def body(db, tid, rows):
        _, _, bd, _ = await resolve_for_request(db, tid, "https://api.example.com/", {}, f"x{P_TOK}", H, N)
        assert bd == f"x{TOK_V}"
        args = ("https://api.example.com/", {"A": P_TOK}, {"b": P_TOK})
        url, hd, b, used = await resolve_for_request(db, tid, *args[:1], *args[1:], H, None)
        assert (url, hd, b, used) == (args[0], args[1], args[2], {})
    run(body)


def test_forged_placeholders_inert():
    async def body(db, tid, rows):
        forged = ["⟦secret:TOK⟧", placeholder("TOK", OTHER)]
        for f in forged:
            url, hd, bd, used = await resolve_for_request(
                db, tid, f"https://api.example.com/?q={f}", {"A": f}, {"b": f}, "evil.example.org", N)
            assert hd == {"A": f} and bd == {"b": f} and used == {}
            assert f in url or f.replace("⟦", "%E2%9F%A6") in url or "secret" in url
        r = (await db.execute(select(TenantSecret).where(TenantSecret.id == rows["TOK"]))).scalar_one()
        assert r.last_used_at is None
    run(body)


def test_marker_forgery_inert():
    async def body(db, tid, rows):
        url, _, _, used = await resolve_for_request(
            db, tid, "https://api.example.com/\ue0000\ue001?q=\ue0000\ue001", {}, None, H, N)
        assert used == {} and "\ue000" not in url
    run(body)


def _err(msg_part, **kw):
    async def body(db, tid, rows):
        if kw.get("corrupt"):
            await db.execute(update(TenantSecret).where(TenantSecret.id == rows["TOK"]).values(value_enc="garbage"))
            await db.commit()
        with pytest.raises(NodeError) as e:
            await resolve_for_request(db, tid, kw.get("url", "https://api.example.com/"),
                                      kw.get("headers", {}), kw.get("body"), kw.get("host", H), N)
        assert str(e.value) == msg_part
    run(body)


def test_netloc_rejected():
    _err("secrets are not allowed in the URL host or credentials", url=f"https://u:{P_TOK}@api.example.com/")
    _err("secrets are not allowed in the URL host or credentials", url=f"https://{P_TOK}.example.com/")


def test_unknown():
    _err("Unknown secret NOPE", headers={"A": placeholder("NOPE", N)})


def test_wrong_host():
    _err("Secret TOK is not allowed for host evil.example.org", headers={"A": P_TOK}, host="evil.example.org")


def test_linebreak_header():
    _err("secret KEY contains a line break and cannot be used in a header", headers={"A": P_KEY})


def test_decrypt_failure():
    _err("Secret TOK could not be decrypted — re-enter it in Integrations", headers={"A": P_TOK}, corrupt=True)


def test_redact():
    used = {"TOK": TOK_V}
    out = redact({"a": f"x {TOK_V} y", "b": ["s3cr3t+value%26x", "s3cr3t%20value%26x"], "c": 1, "d": None}, used)
    assert out == {"a": "x •••• y", "b": ["••••", "••••"], "c": 1, "d": None}
    assert redact_text(f"a {TOK_V} b", used) == "a •••• b"


def test_redact_overlap_and_empty():
    used = {"A": "abc", "B": "abcdef", "E": ""}
    assert redact_text("xx abcdef yy abc", used) == "xx •••• yy ••••"
    assert redact_text("hello", {"E": ""}) == "hello"
