"""MCP suppression tools: list/preview for viewers, create for analysts (always saved disabled), tenant-scoped."""
import pytest

from app.mcp import tools
from app.mcp.tools import ToolError
from tests.guest_fixtures import make_tenant
from tests.test_mcp_tools import _alert, _member, _p, _run

CONDS = [{"field": "title", "op": "contains", "value": "travel"}]


def test_suppression_tools():
    async def body(db, ids):
        t, t2 = await make_tenant(db, ids), await make_tenant(db, ids)
        user, other = await _member(db, ids, t), await _member(db, ids, t2)
        p, p2 = _p(user, t), _p(other, t2)
        aid = (await _alert(db, ids, t, title="Impossible travel")).id
        prev = await tools.preview_suppression(db, p, conditions=CONDS)
        assert prev["matched"] >= 1 and aid in [s["id"] for s in prev["sample"]]
        with pytest.raises(ToolError):
            await tools.preview_suppression(db, p, conditions=[{"field": "owner_id", "op": "equals", "value": "1"}])
        r = await tools.create_suppression(db, p, name="VPN travel", conditions=CONDS, alert_id=aid)
        assert r["enabled"] is False and r["expires_at"]  # drafts only; 30-day default expiry
        with pytest.raises(ToolError):
            await tools.create_suppression(db, p, name="x", conditions=CONDS, expires_in_days=0)
        with pytest.raises(ToolError):  # another tenant's alert can't seed a rule
            await tools.create_suppression(db, p2, name="x", conditions=CONDS, alert_id=aid)
        assert [s["id"] for s in (await tools.list_suppressions(db, p))["suppressions"]] == [r["id"]]
        assert (await tools.list_suppressions(db, p2))["suppressions"] == []
        assert tools.MIN_ROLE["create_suppression"] == "analyst" and tools.MIN_ROLE["list_suppressions"] == "viewer"
    _run(body)
