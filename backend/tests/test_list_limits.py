"""List endpoints must bound `limit` (1..500) and reject negative `skip`.

Validation happens before the handler runs, so auth deps are stubbed and no DB
rows are touched.
"""
import asyncio
from types import SimpleNamespace

import httpx
import pytest

from app.api import deps
from app.main import app

ENDPOINTS = ["/api/v1/cases/", "/api/v1/alerts/", "/api/v1/artifacts/", "/api/v1/iocs/",
             "/api/v1/users/", "/api/v1/tenants/"]


async def _get(url):
    user = SimpleNamespace(id=0, is_active=True, is_super_admin=True)
    for dep in (deps.get_current_active_user, deps.require_admin, deps.require_super_admin,
                deps.require_analyst_or_above):
        app.dependency_overrides[dep] = lambda: user
    app.dependency_overrides[deps.get_effective_tenant_id] = lambda: 0
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            return await c.get(url)
    finally:
        app.dependency_overrides.clear()


@pytest.mark.parametrize("path", ENDPOINTS)
@pytest.mark.parametrize("query", ["limit=100000", "limit=0", "limit=-1", "skip=-1"])
def test_list_endpoint_rejects_out_of_range_paging(path, query):
    r = asyncio.run(_get(f"{path}?{query}"))
    assert r.status_code == 422, (path, query, r.status_code, r.text[:200])
