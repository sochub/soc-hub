"""Alerts list: conditions on payload fields (the suppression-rule vocabulary), received-time range, field discovery."""
import json
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete

from app.db.session import AsyncSessionLocal
from app.models.case import Alert
from app.models.membership import TenantMembership
from tests.guest_fixtures import client, make_tenant, make_user, run
from tests.session_fixtures import make_web_token

UTC = timezone.utc


def _titles(r):
    assert r.status_code == 200, r.text
    return sorted(a["title"] for a in r.json())


def _where(*conds):
    return {"where": json.dumps([{"field": f, "op": op, "value": v} for f, op, v in conds])}


def test_alert_list_payload_conditions_time_range_and_fields():
    async def body(ids):
        alert_ids = []
        try:
            async with AsyncSessionLocal() as db:
                t, other = await make_tenant(db, ids), await make_tenant(db, ids)
                u = await make_user(db, ids)
                db.add(TenantMembership(user_id=u.id, tenant_id=t.id, role="analyst"))
                await db.commit()
                h = {"Authorization": f"Bearer {await make_web_token(db, u, t.id)}"}
                now = datetime.now(UTC)
                rows = [
                    (t, "wazuh", "ps", "high", {"agent_name": "test", "agent_id": "008", "rule": {"groups": ["windows", "powershell"]},
                                                "src_ip": "10.1.2.3"}, now - timedelta(hours=3)),
                    (t, "wazuh", "file", "medium", {"agent_name": "TEST ", "agent_id": "008", "src_ip": "192.168.1.9"}, now - timedelta(hours=2)),
                    (t, "wazuh", "other-agent", "high", {"agent_name": "web-01", "agent_id": "011", "note": "50%_off"}, now - timedelta(hours=1)),
                    (t, "okta", "no-agent", "low", {"user": "forrest"}, now),
                    (other, "wazuh", "elsewhere", "high", {"agent_name": "test"}, now),
                ]
                for i, (tenant, src, title, sev, payload, at) in enumerate(rows):
                    a = Alert(tenant_id=tenant.id, source=src, external_id=f"f-{tenant.id}-{i}", title=title, severity=sev,
                              payload=payload, status="pending", created_at=at)
                    db.add(a)
                    await db.flush()
                    alert_ids.append(a.id)
                await db.commit()

            async with client() as cl:
                get = lambda params: cl.get("/api/v1/alerts/", headers=h, params=params)  # noqa: E731
                # equals ignores case and surrounding spaces; other tenants never leak in
                r = await get(_where(("payload.agent_name", "equals", "test")))
                assert _titles(r) == ["file", "ps"] and r.headers["x-total-count"] == "2"
                # combined with the column filters and other conditions (AND)
                r = await get({**_where(("payload.agent_id", "equals", "008")), "severity": "high"})
                assert _titles(r) == ["ps"]
                # a missing field matches only not_equals
                assert _titles(await get(_where(("payload.agent_name", "not_equals", "test")))) == ["no-agent", "other-agent"]
                # nested paths; a list is matched as its JSON text, so contains finds an item
                assert _titles(await get(_where(("payload.rule.groups", "contains", "powershell")))) == ["ps"]
                # LIKE wildcards in the value are literal
                assert _titles(await get(_where(("payload.note", "contains", "50%_")))) == ["other-agent"]
                assert _titles(await get(_where(("payload.note", "contains", "%")))) == ["other-agent"]
                assert _titles(await get(_where(("payload.agent_name", "starts_with", "web")))) == ["other-agent"]
                # in_cidr parses IPs (in Python, after the SQL conditions)
                r = await get(_where(("payload.src_ip", "in_cidr", "10.0.0.0/8"), ("source", "equals", "wazuh")))
                assert _titles(r) == ["ps"] and r.headers["x-total-count"] == "1"
                # paging and the count apply to the filtered set
                r = await get({**_where(("source", "equals", "wazuh")), "limit": 1, "sort": "created_at"})
                assert _titles(r) == ["ps"] and r.headers["x-total-count"] == "3"

                # received-time window, oldest first for a chronological review
                window = {"received_from": (now - timedelta(minutes=150)).isoformat(),
                          "received_before": (now - timedelta(minutes=30)).isoformat(), "sort": "created_at"}
                r = await get(window)
                assert [a["title"] for a in r.json()] == ["file", "other-agent"]

                # bad input is a 422, never a 500
                for bad in ("not json", json.dumps({"field": "payload.x"}),
                            json.dumps([{"field": "payload.x;drop", "op": "equals", "value": "a"}]),
                            json.dumps([{"field": "payload.x", "op": "regex", "value": "a"}]),
                            json.dumps([{"field": "payload.x", "op": "equals", "value": "a"}] * 11)):
                    assert (await get({"where": bad})).status_code == 422, bad

                # field discovery: paths from recent payloads, most common first, with examples
                r = await cl.get("/api/v1/alerts/fields", headers=h, params={"source": "wazuh"})
                assert r.status_code == 200, r.text
                fields = {f["field"]: f for f in r.json()}
                assert fields["payload.agent_name"]["count"] == 3 and "test" in fields["payload.agent_name"]["examples"]
                assert "payload.rule.groups" in fields and "payload.user" not in fields

                # value suggestions: most common first, counted over every matching alert (not a page)
                vals = lambda params: cl.get("/api/v1/alerts/field-values", headers=h, params=params)  # noqa: E731
                r = await vals({"field": "payload.agent_id"})
                assert r.status_code == 200, r.text
                assert r.json() == [{"value": "008", "count": 2}, {"value": "011", "count": 1}]
                # typed text narrows them, ignoring case; other tenants never leak in
                assert (await vals({"field": "payload.agent_name", "q": "WEB"})).json() == [{"value": "web-01", "count": 1}]
                names = {v["value"]: v["count"] for v in (await vals({"field": "payload.agent_name"})).json()}
                assert names == {"test": 1, "TEST": 1, "web-01": 1}  # stored spellings, surrounding spaces trimmed
                # the list's other filters apply: source, severity, status and the received-time window
                assert (await vals({"field": "source"})).json()[0] == {"value": "wazuh", "count": 3}
                assert (await vals({"field": "payload.agent_id", "severity": "medium"})).json() == [{"value": "008", "count": 1}]
                r = await vals({"field": "payload.agent_id", "received_from": (now - timedelta(minutes=90)).isoformat()})
                assert r.json() == [{"value": "011", "count": 1}]
                assert (await vals({"field": "payload.agent_id", "status": "dismissed"})).json() == []
                # lists/objects are not offered as values; a field nobody sends is empty; bad fields are a 422
                assert (await vals({"field": "payload.rule.groups"})).json() == []
                assert (await vals({"field": "payload.rule"})).json() == []
                assert (await vals({"field": "payload.nope"})).json() == []
                assert (await vals({"field": "payload.x;drop"})).status_code == 422
                assert (await vals({"field": "payload.agent_id", "limit": 1})).json() == [{"value": "008", "count": 2}]
        finally:
            async with AsyncSessionLocal() as db:
                await db.execute(delete(Alert).where(Alert.id.in_(alert_ids or [-1])))
                await db.commit()
    run(body)
