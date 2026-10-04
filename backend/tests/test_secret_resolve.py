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
ALL = {"TOK", "KEY", "NOPE"}


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
            {"a": [f"t={P_TOK}", 1], "b": {"c": P_TOK}}, H, N, ALL)
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
        _, _, bd, _ = await resolve_for_request(db, tid, "https://api.example.com/", {}, f"x{P_TOK}", H, N, ALL)
        assert bd == f"x{TOK_V}"
        args = ("https://api.example.com/", {"A": P_TOK}, {"b": P_TOK})
        assert await resolve_for_request(db, tid, *args, H, None, ALL) == (*args, {})
    run(body)


def test_forged_placeholders_inert():
    async def body(db, tid, rows):
        forged = ["⟦secret:TOK⟧", placeholder("TOK", OTHER)]
        for f in forged:
            url, hd, bd, used = await resolve_for_request(
                db, tid, f"https://api.example.com/?q={f}", {"A": f}, {"b": f}, "evil.example.org", N, ALL)
            assert hd == {"A": f} and bd == {"b": f} and used == {}
            assert TOK_V not in url and "s3cr3t" not in url
            if "#" not in f:
                assert f in url  # inert text stays verbatim (a '#' nonce form lands in the stripped fragment)
        r = (await db.execute(select(TenantSecret).where(TenantSecret.id == rows["TOK"]))).scalar_one()
        assert r.last_used_at is None
    run(body)


def test_marker_forgery_inert():
    async def body(db, tid, rows):
        url, _, _, used = await resolve_for_request(
            db, tid, "https://api.example.com/\ue0000\ue001?q=\ue0000\ue001", {}, None, H, N, ALL)
        assert used == {} and "\ue000" not in url
    run(body)


def _err(msg_part, **kw):
    async def body(db, tid, rows):
        if kw.get("corrupt"):
            await db.execute(update(TenantSecret).where(TenantSecret.id == rows["TOK"]).values(value_enc="garbage"))
            await db.commit()
        with pytest.raises(NodeError) as e:
            await resolve_for_request(db, tid, kw.get("url", "https://api.example.com/"),
                                      kw.get("headers", {}), kw.get("body"), kw.get("host", H), N, ALL, kw.get("allow", ()))
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


def test_port_ipv6_scheme_placeholders_rejected():
    msg = "secrets are not allowed in the URL host or credentials"
    _err(msg, url=f"https://api.example.com:{P_TOK}/")
    _err(msg, url=f"https://[{P_TOK}]/")
    _err(msg, url=f"{P_TOK}://api.example.com/", headers={"A": P_TOK})


def test_nul_in_header_secret():
    async def body(db, tid, rows):
        await db.execute(update(TenantSecret).where(TenantSecret.id == rows["TOK"]).values(value_enc=encrypt("x\x00y")))
        await db.commit()
        with pytest.raises(NodeError) as e:
            await resolve_for_request(db, tid, "https://api.example.com/", {"A": P_TOK}, None, H, N, ALL)
        assert str(e.value) == "secret TOK contains a control character and cannot be used in a header"
    run(body)


def test_body_key_placeholder_rejected():
    _err("secrets are not allowed in body keys", body={P_TOK: 1})


def test_final_host_mismatch_defense():
    _err("secret host check mismatch", url=f"https://evil.example.org/?k={P_TOK}", headers={"A": P_TOK})


def test_redact_keys_variants_and_numbers():
    used = {"TOK": TOK_V, "N": "12345", "J": 'a"b/c'}
    out = redact({TOK_V: 1, "k": "a\\\"b/c", "p": "a%22b/c", "n": 12345, "f": 1.5, "t": ("12345",)}, used)
    assert out == {"••••": 1, "k": "••••", "p": "••••", "n": "••••", "f": 1.5, "t": ("••••",)}


def test_only_issued_names_resolve():
    # R10: a current-nonce placeholder for a name the render never handed out stays inert text
    async def body(db, tid, rows):
        url, hd, bd, used = await resolve_for_request(
            db, tid, "https://api.example.com/", {"A": P_TOK}, {"k": P_KEY}, H, N, {"TOK"})
        assert hd == {"A": TOK_V} and bd == {"k": P_KEY} and used == {"TOK": TOK_V}
        _, hd, _, used = await resolve_for_request(db, tid, "https://api.example.com/", {"A": P_TOK}, None, H, N, set())
        assert hd == {"A": P_TOK} and used == {}
    run(body)


def test_plain_http_needs_allowlisted_host():
    # R11
    msg = "Secret TOK can only be sent over HTTPS unless the host is on the tenant HTTP allowlist"
    _err(msg, url="http://api.example.com/", headers={"A": P_TOK})
    _err(msg, url="HTTP://api.example.com/x?k=" + P_TOK, allow=["other.example.com"])

    async def body(db, tid, rows):
        url, hd, _, used = await resolve_for_request(
            db, tid, "http://api.example.com/", {"A": P_TOK}, None, H, N, ALL, ["API.example.com."])
        assert hd == {"A": TOK_V} and used == {"TOK": TOK_V}
    run(body)


def test_use_sets_last_used_without_bumping_updated_at():
    async def body(db, tid, rows):
        before = (await db.execute(select(TenantSecret.updated_at).where(TenantSecret.id == rows["TOK"]))).scalar_one()
        await asyncio.sleep(0.01)
        await resolve_for_request(db, tid, "https://api.example.com/", {"A": P_TOK}, None, H, N, ALL)
        await db.commit()
        r = (await db.execute(select(TenantSecret.updated_at, TenantSecret.last_used_at)
                              .where(TenantSecret.id == rows["TOK"]))).one()
        assert r.last_used_at is not None and r.updated_at == before
    run(body)
