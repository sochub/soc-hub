import asyncio
from datetime import datetime

import httpx

from app.api import deps
from app.main import app
from app.services.war_room_naming import collision_name, render_channel_name, validate_template

DT = datetime(2026, 10, 26, 9, 30)


def test_default_matches_legacy():
    assert render_channel_name(None, case_id=7, title="Compromised Account!", created=DT) == "inc-0007-compromised-account"


def test_placeholders_and_sanitise():
    assert render_channel_name("IR_{date}_{id}-{title}", case_id=7, title="Compromised Account!", created=DT) == "ir_20261026_0007-compromised-account"


def test_cap_80_shortens_title_first():
    out = render_channel_name(None, case_id=7, title="x" * 200, created=DT)
    assert len(out) == 80 and "0007" in out and out.startswith("inc-0007-")


def test_validate_requires_id():
    assert "{id}" in validate_template("inc-{title}")


def test_validate_rejects_unknown():
    assert "severity" in validate_template("{id}-{severity}")


def test_validate_accepts_good():
    assert validate_template("IR_{date}_{id}-{title}") is None


def test_collision_suffix():
    assert collision_name("a" * 80, 2) == "a" * 77 + "-2"
    assert collision_name("inc-0007-x", 3) == "inc-0007-x-3"


def test_settings_put_rejects_bad_template():
    async def go():
        app.dependency_overrides[deps.get_effective_tenant_id] = lambda: 1
        app.dependency_overrides[deps.require_admin] = lambda: None
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
                return (await c.put("/api/v1/slack/config", json={"war_room_name_template": "x"})).status_code
        finally:
            app.dependency_overrides.clear()
    assert asyncio.run(go()) == 422
