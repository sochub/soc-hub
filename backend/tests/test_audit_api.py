import csv
import io

from app.api.v1 import audit_logs as al
from tests.audit_fixtures import hdr, rows, run


async def _seed(c, w, n=3):
    for i in range(n):
        await c.put(f"/api/v1/cases/{w.case}", json={"title": f"t{i}"}, headers=hdr(w, "admin"))


def test_admin_sees_own_tenant_only_and_analyst_is_denied():
    async def body(c, w):
        await _seed(c, w)
        r = await c.get("/api/v1/audit-logs", headers=hdr(w, "admin"))
        assert r.status_code == 200
        items = r.json()["items"]
        assert items and all(i["route"] != "/api/v1/audit-logs" or i["category"] == "read" for i in items)
        rb = await c.get("/api/v1/audit-logs", headers=hdr(w, "admin_b"))
        assert all(i["entity_id"] != w.case for i in rb.json()["items"])
        for path in ("/api/v1/audit-logs", "/api/v1/audit-logs/facets", "/api/v1/audit-logs/export"):
            assert (await c.get(path, headers=hdr(w, "analyst"))).status_code == 403
        assert (await c.get("/api/v1/audit-logs?scope=platform", headers=hdr(w, "admin"))).status_code == 403
        assert (await c.get("/api/v1/audit-logs?scope=platform", headers=hdr(w, "root"))).status_code == 200
    run(body)


def test_filters_and_keyset_paging():
    async def body(c, w):
        await _seed(c, w, n=5)
        seen, before = [], None
        for _ in range(3):
            q = "/api/v1/audit-logs?category=write&limit=2" + (f"&before={before}" if before else "")
            page = (await c.get(q, headers=hdr(w, "admin"))).json()
            seen += [i["id"] for i in page["items"]]
            before = page["next_before"]
            if not before:
                break
        assert len(seen) == len(set(seen)) and seen == sorted(seen, reverse=True) and len(seen) == 5
        f = (await c.get(f"/api/v1/audit-logs?entity_type=case&entity_id={w.case}&action=PUT",
                         headers=hdr(w, "admin"))).json()["items"]
        assert f and all(i["entity_id"] == w.case and i["action"].startswith("PUT") for i in f)
        rid = f[0]["request_id"]
        linked = (await c.get(f"/api/v1/audit-logs?request_id={rid}", headers=hdr(w, "admin"))).json()["items"]
        assert {i["category"] for i in linked} >= {"write", "change"}
    run(body)


def test_viewing_the_log_is_itself_recorded():
    async def body(c, w):
        await c.get("/api/v1/audit-logs", headers=hdr(w, "admin"))
        assert len(await rows(w, action="audit.view")) == 1
    run(body)


def test_export_csv_respects_filters_and_cap(monkeypatch):
    monkeypatch.setattr(al, "EXPORT_CAP", 2)

    async def body(c, w):
        await _seed(c, w, n=4)
        r = await c.get("/api/v1/audit-logs/export?category=write&format=csv", headers=hdr(w, "admin"))
        assert r.status_code == 200 and "attachment;" in r.headers["content-disposition"]
        data = list(csv.DictReader(io.StringIO(r.text)))
        assert len(data) == 2 and all(d["category"] == "write" for d in data)
        assert len(await rows(w, action="audit.export")) == 1
    run(body)


def test_per_entity_endpoint_returns_only_change_rows():
    async def body(c, w):
        await _seed(c, w, n=1)
        await c.get(f"/api/v1/cases/{w.case}", headers=hdr(w, "analyst"))
        r = await c.get(f"/api/v1/audit-logs/case/{w.case}", headers=hdr(w, "analyst"))
        assert r.status_code == 200
        assert r.json() and all(i.get("category", "change") == "change" for i in r.json())
    run(body)


def test_facets():
    async def body(c, w):
        await _seed(c, w, n=1)
        f = (await c.get("/api/v1/audit-logs/facets", headers=hdr(w, "admin"))).json()
        assert "write" in f["categories"] and "case" in f["entity_types"]
        assert any(u["id"] == w.users["admin"] for u in f["users"])
    run(body)


def test_csv_neutralises_formulas_but_json_stays_raw():
    async def body(c, w):
        h = {**hdr(w, "admin"), "User-Agent": "=1+1"}
        await c.put(f"/api/v1/cases/{w.case}", json={"title": "fx"}, headers=h)
        r = await c.get("/api/v1/audit-logs/export?category=write&format=csv", headers=hdr(w, "admin"))
        assert "'=1+1" in [d["user_agent"] for d in csv.DictReader(io.StringIO(r.text))]
        j = (await c.get("/api/v1/audit-logs/export?category=write&format=json", headers=hdr(w, "admin"))).json()
        assert "=1+1" in [d["user_agent"] for d in j]
    run(body)


def test_platform_view_is_a_platform_row_and_unknown_tenant_is_404():
    async def body(c, w):
        assert (await c.get("/api/v1/audit-logs?scope=platform", headers=hdr(w, "root"))).status_code == 200
        got = await rows(w, action="audit.view", tenant_id=None)
        assert len(got) == 1 and got[0].user_id == w.users["root"]
        assert (await c.get("/api/v1/audit-logs?tenant_id=999999999", headers=hdr(w, "root"))).status_code == 404
    run(body)


def test_other_tenant_cannot_see_facets_or_export_rows():
    async def body(c, w):
        await _seed(c, w, n=2)
        f = (await c.get("/api/v1/audit-logs/facets", headers=hdr(w, "admin_b"))).json()
        assert w.users["admin"] not in {u["id"] for u in f["users"]}
        r = await c.get("/api/v1/audit-logs/export?format=csv", headers=hdr(w, "admin_b"))
        data = list(csv.DictReader(io.StringIO(r.text)))
        assert all(d["entity_id"] != str(w.case) and d["user_email"] != w.emails["admin"] for d in data)
        items = (await c.get("/api/v1/audit-logs?limit=200", headers=hdr(w, "admin_b"))).json()["items"]
        assert all(i["user_email"] != w.emails["admin"] and i["entity_id"] != w.case for i in items)
    run(body)
