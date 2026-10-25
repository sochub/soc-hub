import asyncio
import socket

import httpx
import pytest

from app.services import oidc_service as svc
from app.services.oidc_service import OIDCError
from tests.oidc_fixtures import CLIENT_ID, ISSUER, discovery_doc, jwks_for, make_key, sign_token

KEY = make_key()
KID = "k1"


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("test reached the network")

    monkeypatch.setattr(httpx.AsyncClient, "get", boom)
    monkeypatch.setattr(httpx.AsyncClient, "post", boom)
    monkeypatch.setattr(svc, "_resolve", boom)  # tests needing DNS override this


@pytest.fixture(autouse=True)
def _clean():
    svc._clear_caches()
    yield
    svc._clear_caches()


def run(c):
    return asyncio.run(c)


def fake_get(monkeypatch, jwks=None, calls=None):
    docs = {f"{ISSUER}/.well-known/openid-configuration": discovery_doc(),
            f"{ISSUER}/jwks": jwks or jwks_for((KEY, KID))}

    async def g(url, *a, **k):
        if calls is not None:
            calls.append(url)
        return docs[url]

    monkeypatch.setattr(svc, "_get_json", g)
    return docs


def verify(tok, **kw):
    meta = discovery_doc()
    return run(svc.verify_id_token(tok, meta, client_id=kw.get("client_id", CLIENT_ID), nonce=kw.get("nonce", "n1")))


def code_of(fn):
    with pytest.raises(OIDCError) as e:
        fn()
    return e.value.code


def test_valid_token(monkeypatch):
    fake_get(monkeypatch)
    assert verify(sign_token(KEY, KID))["email"] == "alice@corp.com"


def test_wrong_nonce(monkeypatch):
    fake_get(monkeypatch)
    assert code_of(lambda: verify(sign_token(KEY, KID, nonce="other"))) == "id_token"


def test_wrong_audience(monkeypatch):
    fake_get(monkeypatch)
    assert code_of(lambda: verify(sign_token(KEY, KID, aud="someone-else"))) == "id_token"


def test_wrong_issuer(monkeypatch):
    fake_get(monkeypatch)
    assert code_of(lambda: verify(sign_token(KEY, KID, iss="https://evil.example.com"))) == "id_token"


def test_bad_signature(monkeypatch):
    fake_get(monkeypatch)
    assert code_of(lambda: verify(sign_token(make_key(), KID))) == "id_token"


def test_unknown_kid_refetches_once(monkeypatch):
    calls = []
    docs = fake_get(monkeypatch, calls=calls)
    verify(sign_token(KEY, KID))
    new = make_key()
    docs[f"{ISSUER}/jwks"] = jwks_for((new, "k2"))
    assert verify(sign_token(new, "k2"))["sub"] == "u1"
    assert calls.count(f"{ISSUER}/jwks") == 2
    # a kid that is still unknown after the refetch is refused, with exactly one more fetch
    assert code_of(lambda: verify(sign_token(new, "nope"))) == "id_token"
    assert calls.count(f"{ISSUER}/jwks") == 3


def test_discovery_issuer_mismatch(monkeypatch):
    async def g(url, *a, **k):
        return discovery_doc("https://other.example.com")

    monkeypatch.setattr(svc, "_get_json", g)
    assert code_of(lambda: run(svc.discover(ISSUER))) == "discovery"


def test_discovery_ok_and_cached(monkeypatch):
    calls = []
    fake_get(monkeypatch, calls=calls)
    assert run(svc.discover(ISSUER))["jwks_uri"] == f"{ISSUER}/jwks"
    run(svc.discover(ISSUER))
    assert len(calls) == 1


def test_email_unverified_false_refused():
    assert code_of(lambda: svc.claims_email({"email": "a@b.co", "email_verified": False})) == "unverified"


def test_email_verified_absent_ok():
    assert svc.claims_email({"email": "a@b.co"})[0] == "a@b.co"


def test_email_missing():
    assert code_of(lambda: svc.claims_email({})) == "email"


def test_email_normalized():
    assert svc.claims_email({"email": " Alice@Corp.COM ", "name": "Alice A"}) == ("alice@corp.com", "Alice A")


def test_network_error_is_oidcerror(monkeypatch):
    async def g(url, *a, **k):
        raise httpx.ConnectError("boom")

    monkeypatch.setattr(svc, "_get_json", g)
    assert code_of(lambda: run(svc.discover(ISSUER))) == "discovery"


def _resolver(monkeypatch, mapping):
    def fake(host, port, proto=0, **k):
        ip = mapping[host]
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port))]

    monkeypatch.setattr(svc, "_resolve", fake)


def test_private_issuer_refused(monkeypatch):
    _resolver(monkeypatch, {"idp.example.com": "10.0.0.5"})
    with pytest.raises(OIDCError) as e:
        run(svc.discover(ISSUER))
    assert e.value.code == "discovery"
    assert "non-public address (10.0.0.5)" in str(e.value)


def test_private_jwks_uri_refused(monkeypatch):
    _resolver(monkeypatch, {"idp.example.com": "93.184.216.34", "keys.example.com": "169.254.169.254"})
    meta = discovery_doc()
    meta["jwks_uri"] = "https://keys.example.com/keys"
    with pytest.raises(OIDCError) as e:
        run(svc.verify_id_token(sign_token(KEY, KID), meta, client_id=CLIENT_ID, nonce="n1"))
    assert e.value.code == "discovery"
    assert "non-public address (169.254.169.254)" in str(e.value)


def test_private_token_endpoint_refused(monkeypatch):
    _resolver(monkeypatch, {"tok.example.com": "169.254.169.254"})
    meta = discovery_doc()
    meta["token_endpoint"] = "https://tok.example.com/token"
    with pytest.raises(OIDCError) as e:
        run(svc.exchange_code(meta, client_id="c", client_secret="s", code="x", redirect_uri="https://a/cb", code_verifier="v"))
    assert e.value.code == "token"


def test_http_scheme_refused():
    with pytest.raises(OIDCError):
        run(svc._get_json("http://idp.example.com/x"))


def test_private_host_allowlist(monkeypatch):
    _resolver(monkeypatch, {"idp.internal": "10.0.0.5"})
    monkeypatch.setattr(svc.settings, "OIDC_PRIVATE_HOSTS", ["idp.internal"])
    svc._guard("https://idp.internal/x", "discovery")


def test_exchange_code_posts_secret_and_verifier(monkeypatch):
    seen = {}

    async def p(url, data, *a, **k):
        seen.update(url=url, data=data)
        return {"id_token": "t"}

    monkeypatch.setattr(svc, "_post_form", p)
    tok = run(svc.exchange_code(discovery_doc(), client_id="c", client_secret="s", code="x",
                                redirect_uri="https://a/cb", code_verifier="v"))
    assert tok["id_token"] == "t"
    assert seen["data"]["client_secret"] == "s" and seen["data"]["code_verifier"] == "v"


def test_authorization_url_and_pkce():
    v, c = svc.pkce_pair()
    u = svc.authorization_url(discovery_doc(), client_id="c", redirect_uri="https://a/cb",
                              scopes="openid email", state="s", nonce="n", code_challenge=c)
    assert "code_challenge_method=S256" in u and f"code_challenge={c}" in u and "state=s" in u
    import base64, hashlib
    assert c == base64.urlsafe_b64encode(hashlib.sha256(v.encode()).digest()).rstrip(b"=").decode()
