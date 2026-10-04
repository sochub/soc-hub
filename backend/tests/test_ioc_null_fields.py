import httpx
import pytest

from app.api import deps
from app.main import app


@pytest.mark.asyncio
@pytest.mark.parametrize("body,bad", [
    ({"tlp": None}, "tlp"),
    ({"threat_level": None, "tags": None}, "tags, threat_level"),
    ({"status": None, "description": "x"}, "status"),
])
async def test_update_rejects_null_on_required_fields(body, bad):
    app.dependency_overrides[deps.get_current_active_user] = lambda: object()
    app.dependency_overrides[deps.get_effective_tenant_id] = lambda: 1
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            r = await c.put("/api/v1/iocs/999999999", json=body)
        assert r.status_code == 422
        assert r.json()["detail"] == f"{bad} cannot be null"
    finally:
        app.dependency_overrides.clear()
