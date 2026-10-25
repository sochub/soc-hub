import asyncio

import httpx
import pytest
from sqlalchemy import delete

from app.db.session import AsyncSessionLocal, engine
from app.main import app
from app.models.oauth_client import OAuthClient
from app.oauth.routes import redirect_uri_allowed

BODY = {"client_name": "Test App", "redirect_uris": ["http://127.0.0.1:33418/cb"]}


def _run(body):
    async def go():
        await engine.dispose()
        ids: list[str] = []
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
                await body(c, ids)
        finally:
            if ids:
                async with AsyncSessionLocal() as db:
                    await db.execute(delete(OAuthClient).where(OAuthClient.id.in_(ids)))
                    await db.commit()
            await engine.dispose()
    asyncio.run(go())


def test_metadata_shape():
    async def body(c, ids):
        r = await c.get("/.well-known/oauth-authorization-server")
        assert r.status_code == 200
        m = r.json()
        assert m["authorization_endpoint"] == m["issuer"] + "/api/v1/oauth/authorize"
        assert m["registration_endpoint"].endswith("/api/v1/oauth/register")
        assert m["code_challenge_methods_supported"] == ["S256"]
        assert m["token_endpoint_auth_methods_supported"] == ["none"]
        assert m["scopes_supported"] == ["mcp"]
        assert r.headers["x-content-type-options"] == "nosniff"
    _run(body)


@pytest.mark.parametrize("uri,ok", [
    ("https://app.example.com/cb", True),
    ("http://localhost:33418/cb", True),
    ("http://[::1]:8080/cb", True),
    ("http://example.com/cb", False),
    ("javascript:alert(1)", False),
    ("com.example.app:/cb", True),
    ("https://x/cb#frag", False),
    ("https:///cb", False),
    ("file:///etc/passwd", False),
])
def test_redirect_uri_allowed(uri, ok):
    assert redirect_uri_allowed(uri) is ok


def test_register_ok():
    async def body(c, ids):
        r = await c.post("/api/v1/oauth/register", json=BODY)
        assert r.status_code == 201
        j = r.json()
        ids.append(j["client_id"])
        assert j["token_endpoint_auth_method"] == "none" and j["redirect_uris"] == BODY["redirect_uris"]
        assert j["client_id_issued_at"] > 0
    _run(body)


def test_register_rejects_confidential_and_bad_uri():
    async def body(c, ids):
        r = await c.post("/api/v1/oauth/register", json={**BODY, "token_endpoint_auth_method": "client_secret_basic"})
        assert r.status_code == 400 and r.json()["error"] == "invalid_client_metadata"
        r = await c.post("/api/v1/oauth/register", json={**BODY, "redirect_uris": ["http://evil.com/cb"]})
        assert r.status_code == 400 and r.json()["error"] == "invalid_redirect_uri"
        r = await c.post("/api/v1/oauth/register", json={"redirect_uris": BODY["redirect_uris"]})
        assert r.status_code == 400 and r.json()["error"] == "invalid_client_metadata"
    _run(body)


def test_register_rate_limited():
    async def body(c, ids):
        for _ in range(20):
            r = await c.post("/api/v1/oauth/register", json=BODY)
            assert r.status_code == 201
            ids.append(r.json()["client_id"])
        r = await c.post("/api/v1/oauth/register", json=BODY)
        assert r.status_code == 429
    _run(body)
