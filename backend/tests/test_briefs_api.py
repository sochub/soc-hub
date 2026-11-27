"""Security briefs API: feature gate, list filters and paging, per-user state, tenant isolation, sources, settings."""
import json
from datetime import timedelta

from sqlalchemy import delete, select

from app.ai import llm
from app.ai.errors import AIUnavailable
from app.db.session import AsyncSessionLocal
from app.models.artifact import Artifact
from app.models.artifact_type_definition import ArtifactTypeDefinition
from app.models.audit_log import AuditLog
from app.models.case_artifact import CaseArtifact
from app.models.ioc import IOC
from app.models.brief import BriefAI, BriefSource
from app.models.tenant import Tenant
from app.services import briefs
from tests.briefs_fixtures import cleanup_sources, make_item, make_org, make_source
from tests.guest_fixtures import client, make_case, run

API = "/api/v1/briefs"
ENDPOINTS = [  # every route, with a body that would be valid if the feature were on
    ("GET", "/", None), ("GET", "/unread-count", None), ("POST", "/read-all", None),
    ("POST", "/{item}/state", {"read": True}), ("POST", "/{item}/brief", None),
    ("GET", "/sources", None), ("POST", "/sources", {"name": "n", "url": "https://x.example.test/rss", "kind": "news"}),
    ("PUT", "/sources/{source}", {"enabled": False}), ("DELETE", "/sources/{source}", None),
]


async def _audit(db, tenant_id, action):
    return (await db.execute(select(AuditLog).where(AuditLog.tenant_id == tenant_id, AuditLog.action == action)
                             .order_by(AuditLog.id))).scalars().all()


def test_every_endpoint_is_404_while_the_feature_is_off():
    async def body(ids):
        try:
            async with AsyncSessionLocal() as db:
                t, h, _ = await make_org(db, ids, enabled=False, roles=("admin",))
                src = await make_source(db, ids, t)
                item = await make_item(db, src, "a")
            async with client() as cl:
                for method, path, payload in ENDPOINTS:
                    url = API + path.format(item=item.id, source=src.id)
                    assert (await cl.request(method, url, json=payload)).status_code == 401, (method, path)
                    r = await cl.request(method, url, headers=h["admin"], json=payload)
                    assert r.status_code == 404, (method, path, r.text)
                me = await cl.get("/api/v1/users/me", headers=h["admin"])
                assert me.json()["briefs_enabled"] is False

                # the settings toggle: admin only, audited, and it opens the API
                sec = "/api/v1/tenants/current/security"
                assert (await cl.get(sec, headers=h["admin"])).json()["briefs_enabled"] is False
                r = await cl.put(sec, headers=h["admin"], json={"briefs_enabled": True})
                assert r.status_code == 200 and r.json()["briefs_enabled"] is True and r.json()["mcp_enabled"] is False
                await cl.put(sec, headers=h["admin"], json={"briefs_enabled": True})  # no change, no second audit row
                assert (await cl.get("/api/v1/users/me", headers=h["admin"])).json()["briefs_enabled"] is True
                assert (await cl.get(API + "/", headers=h["admin"])).status_code == 200
            async with AsyncSessionLocal() as db:
                rows = await _audit(db, t.id, "tenant.briefs_toggled")
                assert [r.changes for r in rows] == [{"briefs_enabled": True}]
        finally:
            await cleanup_sources(ids)
    run(body)


def test_settings_toggle_is_admin_only():
    async def body(ids):
        async with AsyncSessionLocal() as db:
            t, h, _ = await make_org(db, ids, enabled=False)
        async with client() as cl:
            for role in ("analyst", "viewer"):
                r = await cl.put("/api/v1/tenants/current/security", headers=h[role], json={"briefs_enabled": True})
                assert r.status_code == 403
                # feature off: role-gated routes are a 404 too, never a 403 that confirms they exist
                assert (await cl.get(API + "/sources", headers=h[role])).status_code == 404
                assert (await cl.post(API + "/1/brief", headers=h[role])).status_code == 404
        async with AsyncSessionLocal() as db:
            assert (await db.get(Tenant, t.id)).briefs_enabled is False
            assert await _audit(db, t.id, "tenant.briefs_toggled") == []
    run(body)


def test_list_filters_paging_and_state():
    async def body(ids):
        try:
            async with AsyncSessionLocal() as db:
                t, h, _ = await make_org(db, ids)
                other, oh, _ = await make_org(db, ids, roles=("admin",))
                shared = await make_source(db, ids, None, name="Shared vulns", kind="vulnerability")
                own = await make_source(db, ids, t, name="Own news", kind="news")
                private = await make_source(db, ids, other, name="Their private feed", kind="incident")
                a = await make_item(db, shared, "a", title="Acme 100%_done gateway bypass", age=timedelta(hours=1),
                                    cves=["CVE-2026-1111", "CVE-2026-2222"], summary="Exploited in the wild")
                b = await make_item(db, own, "b", title="Phishing wave", age=timedelta(hours=2),
                                    summary="Targets FINANCE teams")
                c = await make_item(db, own, "c", title="Quiet week", age=timedelta(hours=3), cves=["CVE-2026-11112"])
                secret = await make_item(db, private, "p", title="Their private item", age=timedelta(minutes=5))
                db.add(BriefAI(item_id=a.id, tenant_id=t.id, status="ready", what_happened="W", why_it_matters="Y",
                               lessons=["L1", "L2"], model="ollama/llama3"))
                # the other organization's brief of the shared item must never show up here
                db.add(BriefAI(item_id=a.id, tenant_id=other.id, status="ready", what_happened="THEIRS",
                               why_it_matters="THEIRS", lessons=["THEIRS"], model="x/y"))
                await db.commit()

            async with client() as cl:
                async def titles(headers=h["viewer"], **params):
                    r = await cl.get(API + "/", headers=headers, params=params)
                    assert r.status_code == 200, r.text
                    return [i["title"] for i in r.json()], int(r.headers["X-Total-Count"])

                # any member reads; newest first; another organization's private items are invisible
                r = await cl.get(API + "/", headers=h["viewer"])
                assert [i["id"] for i in r.json()] == [a.id, b.id, c.id] and r.headers["X-Total-Count"] == "3"
                first = r.json()[0]
                assert first == {
                    "id": a.id, "title": "Acme 100%_done gateway bypass", "summary": "Exploited in the wild",
                    "url": "https://articles.example.test/a", "published_at": first["published_at"],
                    "kind": "vulnerability", "cves": ["CVE-2026-1111", "CVE-2026-2222"],
                    "indicators": [], "matches": [],
                    "source": {"id": shared.id, "name": "Shared vulns"},
                    "brief": {"status": "ready", "what_happened": "W", "why_it_matters": "Y", "lessons": ["L1", "L2"],
                              "model": "ollama/llama3", "created_at": first["brief"]["created_at"]},
                    "read": False, "saved": False}
                assert r.json()[1]["brief"] is None
                assert (await titles(headers=oh["admin"]))[0] == ["Their private item", "Acme 100%_done gateway bypass"]

                # paging
                assert await titles(limit=2) == (["Acme 100%_done gateway bypass", "Phishing wave"], 3)
                assert await titles(skip=2, limit=2) == (["Quiet week"], 3)
                assert (await cl.get(API + "/", headers=h["viewer"], params={"limit": 101})).status_code == 422
                # filters
                assert await titles(kind="news") == (["Phishing wave", "Quiet week"], 2)
                assert (await cl.get(API + "/", headers=h["viewer"], params={"kind": "gossip"})).status_code == 422
                assert await titles(source_id=shared.id) == (["Acme 100%_done gateway bypass"], 1)
                assert await titles(source_id=private.id) == ([], 0)
                assert await titles(q="finance") == (["Phishing wave"], 1)       # summary, case-insensitive
                assert await titles(q="GATEWAY") == (["Acme 100%_done gateway bypass"], 1)
                assert await titles(q="100%_d") == (["Acme 100%_done gateway bypass"], 1)
                assert await titles(q="%") == (["Acme 100%_done gateway bypass"], 1)  # LIKE wildcards are literal
                assert await titles(q="_") == (["Acme 100%_done gateway bypass"], 1)
                assert await titles(cve="cve-2026-1111") == (["Acme 100%_done gateway bypass"], 1)
                assert await titles(cve="CVE-2026-111") == ([], 0)                # exact id, not a prefix
                assert await titles(kind="news", q="week", cve="CVE-2026-11112") == (["Quiet week"], 1)

                # read / saved belong to the caller; a second user (and a second organization) keep their own
                assert (await cl.get(API + "/unread-count", headers=h["viewer"])).json() == {"count": 3, "capped": False, "matched_unread": 0}
                r = await cl.post(f"{API}/{a.id}/state", headers=h["viewer"], json={"read": True})
                assert r.status_code == 200 and r.json() == {"read": True, "saved": False}
                r = await cl.post(f"{API}/{b.id}/state", headers=h["viewer"], json={"saved": True})
                assert r.json() == {"read": False, "saved": True}
                r = await cl.post(f"{API}/{a.id}/state", headers=h["viewer"], json={"saved": True})
                assert r.json() == {"read": True, "saved": True}   # an omitted field is left alone
                assert (await cl.post(f"{API}/{a.id}/state", headers=h["viewer"], json={})).json() == {
                    "read": True, "saved": True}
                assert await titles(unread="true") == (["Phishing wave", "Quiet week"], 2)
                assert await titles(unread="false") == (["Acme 100%_done gateway bypass"], 1)
                assert await titles(saved="true") == (["Acme 100%_done gateway bypass", "Phishing wave"], 2)
                assert await titles(saved="true", unread="true") == (["Phishing wave"], 1)
                assert (await cl.get(API + "/unread-count", headers=h["viewer"])).json()["count"] == 2
                assert await titles(headers=h["analyst"], unread="false") == ([], 0)
                assert await titles(headers=h["analyst"], saved="true") == ([], 0)
                assert (await cl.get(API + "/unread-count", headers=h["analyst"])).json()["count"] == 3
                assert (await cl.get(API + "/unread-count", headers=oh["admin"])).json()["count"] == 2
                r = await cl.post(f"{API}/{a.id}/state", headers=h["viewer"], json={"read": False, "saved": False})
                assert r.json() == {"read": False, "saved": False}
                await cl.post(f"{API}/{a.id}/state", headers=h["viewer"], json={"read": True})

                # tenant isolation: another organization's private item is a 404 everywhere
                for path, payload in ((f"/{secret.id}/state", {"read": True}), (f"/{secret.id}/brief", None),
                                      ("/99999999/state", {"read": True})):
                    assert (await cl.post(API + path, headers=h["admin"], json=payload)).status_code == 404, path

                # read-all with a filter marks only what matches; without one, everything visible
                r = await cl.post(API + "/read-all", headers=h["viewer"], params={"q": "phishing"})
                assert r.status_code == 200 and r.json() == {"marked": 1}
                assert await titles(unread="true") == (["Quiet week"], 1)
                assert await titles(saved="true") == (["Phishing wave"], 1)  # marking read keeps the saved mark
                assert (await cl.post(API + "/read-all", headers=h["viewer"])).json() == {"marked": 1}
                assert (await cl.post(API + "/read-all", headers=h["viewer"])).json() == {"marked": 0}
                assert (await cl.get(API + "/unread-count", headers=h["viewer"])).json()["count"] == 0
                assert (await cl.get(API + "/unread-count", headers=h["analyst"])).json()["count"] == 3
                # ...and never another organization's private item
                assert (await cl.get(API + "/unread-count", headers=oh["admin"])).json()["count"] == 2
        finally:
            await cleanup_sources(ids)
    run(body)


def test_unread_count_is_capped(monkeypatch):
    from app.api.v1 import briefs as api
    monkeypatch.setattr(api, "UNREAD_BADGE_CAP", 2)

    async def body(ids):
        async with AsyncSessionLocal() as db:
            t, h, _ = await make_org(db, ids, roles=("viewer",))
            src = await make_source(db, ids, t)
            for g in "abc":
                await make_item(db, src, g)
        async with client() as cl:
            assert (await cl.get(API + "/unread-count", headers=h["viewer"])).json() == {"count": 2, "capped": True, "matched_unread": 0}
    run(body)


def test_brief_on_demand(monkeypatch):
    replies = []

    async def complete(messages, *, tenant_id, temperature=None, json_mode=False):
        reply = replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply
    monkeypatch.setattr(llm, "complete", complete)
    monkeypatch.setattr(briefs, "BRIEFS_DAILY_CAP", 1)
    good = json.dumps({"what_happened": "It happened.", "why_it_matters": "It matters.", "lessons": ["Do this"]})

    async def body(ids):
        async with AsyncSessionLocal() as db:
            t, h, _ = await make_org(db, ids)
            src = await make_source(db, ids, t)
            a, b = await make_item(db, src, "a"), await make_item(db, src, "b")
        async with client() as cl:
            assert (await cl.post(f"{API}/{a.id}/brief", headers=h["viewer"])).status_code == 403
            # provider down: 503 without the provider's own words, nothing stored
            replies.append(AIUnavailable("AuthenticationError: key sk-secret at http://10.0.0.9"))
            r = await cl.post(f"{API}/{a.id}/brief", headers=h["analyst"])
            assert r.status_code == 503 and "sk-secret" not in r.text and "10.0.0.9" not in r.text
            assert (await cl.get(API + "/", headers=h["analyst"])).json()[0]["brief"] is None
            # unusable reply: stored as failed, shown as such
            replies.append("no json here")
            r = await cl.post(f"{API}/{a.id}/brief", headers=h["analyst"])
            assert r.status_code == 200 and r.json()["status"] == "failed" and r.json()["lessons"] == []
            # asking again regenerates a failed brief
            replies.append(good)
            r = await cl.post(f"{API}/{a.id}/brief", headers=h["analyst"])
            assert r.status_code == 200, r.text
            assert {k: r.json()[k] for k in ("status", "what_happened", "why_it_matters", "lessons")} == {
                "status": "ready", "what_happened": "It happened.", "why_it_matters": "It matters.", "lessons": ["Do this"]}
            assert "/" in r.json()["model"]
            # a ready brief is returned as is: no second model call (replies is empty, a call would fail)
            assert (await cl.post(f"{API}/{a.id}/brief", headers=h["admin"])).json()["what_happened"] == "It happened."
            # on demand is allowed past the daily cap (1 here), and counts toward it
            replies.append(good)
            assert (await cl.post(f"{API}/{b.id}/brief", headers=h["admin"])).json()["status"] == "ready"
            items = (await cl.get(API + "/", headers=h["viewer"])).json()
            assert [i["brief"]["status"] for i in items] == ["ready", "ready"]
        async with AsyncSessionLocal() as db:
            from datetime import datetime, timezone
            assert await briefs.briefs_today(db, t.id, datetime.now(timezone.utc)) == 2
            assert len((await db.execute(select(BriefAI).where(BriefAI.tenant_id == t.id))).scalars().all()) == 2
    run(body)


def test_sources_admin(monkeypatch):
    monkeypatch.setattr(briefs, "MAX_TENANT_SOURCES", 3)

    async def body(ids):
        try:
            async with AsyncSessionLocal() as db:
                t, h, users = await make_org(db, ids)
                other, oh, _ = await make_org(db, ids, roles=("admin", "viewer"))
                builtin = await make_source(db, ids, None, name="Built-in feed", kind="vulnerability",
                                            last_error="HTTP 404")
                retired = await make_source(db, ids, None, name="Built-in the deployment turned off", enabled=False)
                theirs = await make_source(db, ids, other, name="Other organization's")
                await make_item(db, builtin, "k1", title="from the built-in")
                await make_item(db, builtin, "k2", title="from the built-in too")
            new = {"name": "  Vendor blog ", "url": "https://blog.example.test/feed.xml", "kind": "news"}
            async with client() as cl:
                # admin only
                for role in ("analyst", "viewer"):
                    assert (await cl.get(API + "/sources", headers=h[role])).status_code == 403
                    assert (await cl.post(API + "/sources", headers=h[role], json=new)).status_code == 403
                    assert (await cl.put(f"{API}/sources/{builtin.id}", headers=h[role], json={"enabled": False})).status_code == 403
                    assert (await cl.delete(f"{API}/sources/{builtin.id}", headers=h[role])).status_code == 403

                # https only, no credentials in the URL, a known kind
                for bad in ({**new, "url": "http://blog.example.test/feed.xml"}, {**new, "url": "ftp://x.example.test/f"},
                            {**new, "url": "https://user:pw@blog.example.test/feed.xml"}, {**new, "url": "https://"},
                            {**new, "url": "blog.example.test/feed"}, {**new, "kind": "gossip"}, {**new, "name": "   "}):
                    assert (await cl.post(API + "/sources", headers=h["admin"], json=bad)).status_code == 422, bad

                r = await cl.post(API + "/sources", headers=h["admin"], json=new)
                assert r.status_code == 201, r.text
                mine = r.json()
                assert mine == {"id": mine["id"], "name": "Vendor blog", "url": "https://blog.example.test/feed.xml",
                                "kind": "news", "format": "rss", "builtin": False, "enabled": True,
                                "last_fetched_at": None, "last_ok_at": None, "last_error": None, "item_count": 0}
                assert (await cl.post(API + "/sources", headers=h["admin"], json=new)).status_code == 409
                # the same URL is fine for another organization
                assert (await cl.post(API + "/sources", headers=oh["admin"], json=new)).status_code == 201

                # the list: built-ins the deployment has on, then own; never another organization's
                rows = (await cl.get(API + "/sources", headers=h["admin"])).json()
                listed = {r["id"]: r for r in rows}
                assert builtin.id in listed and mine["id"] in listed
                assert retired.id not in listed and theirs.id not in listed
                assert listed[builtin.id] == {**listed[builtin.id], "builtin": True, "enabled": True, "item_count": 2,
                                              "last_error": "HTTP 404", "name": "Built-in feed"}
                assert [r["builtin"] for r in rows] == sorted((r["builtin"] for r in rows), reverse=True)

                # max per organization (3 here)
                for n in range(2):
                    r = await cl.post(API + "/sources", headers=h["admin"], json={**new, "url": f"https://f{n}.example.test/r"})
                    assert r.status_code == 201
                r = await cl.post(API + "/sources", headers=h["admin"], json={**new, "url": "https://f9.example.test/r"})
                assert r.status_code == 400 and "At most 3" in r.json()["detail"]

                # rename / pause an own source
                r = await cl.put(f"{API}/sources/{mine['id']}", headers=h["admin"], json={"name": "Vendor PSIRT", "enabled": False})
                assert r.status_code == 200 and (r.json()["name"], r.json()["enabled"]) == ("Vendor PSIRT", False)
                # another organization's source, or one that does not exist: 404
                for sid in (theirs.id, retired.id, 99999999):
                    assert (await cl.put(f"{API}/sources/{sid}", headers=h["admin"], json={"enabled": False})).status_code == 404
                    assert (await cl.delete(f"{API}/sources/{sid}", headers=h["admin"])).status_code == 404

                # a built-in can only be switched off for this organization
                assert (await cl.put(f"{API}/sources/{builtin.id}", headers=h["admin"], json={"name": "mine now"})).status_code == 400
                assert (await cl.delete(f"{API}/sources/{builtin.id}", headers=h["admin"])).status_code == 400
                assert len((await cl.get(API + "/", headers=h["viewer"])).json()) == 2
                r = await cl.put(f"{API}/sources/{builtin.id}", headers=h["admin"], json={"enabled": False})
                assert r.status_code == 200 and r.json()["enabled"] is False and r.json()["item_count"] == 2
                await cl.put(f"{API}/sources/{builtin.id}", headers=h["admin"], json={"enabled": False})  # no-op
                # its items disappear for this organization only
                assert (await cl.get(API + "/", headers=h["viewer"])).json() == []
                assert (await cl.get(API + "/unread-count", headers=h["viewer"])).json()["count"] == 0
                assert len((await cl.get(API + "/", headers=oh["viewer"])).json()) == 2
                listed = {r["id"]: r for r in (await cl.get(API + "/sources", headers=h["admin"])).json()}
                assert listed[builtin.id]["enabled"] is False
                assert {r["id"]: r for r in (await cl.get(API + "/sources", headers=oh["admin"])).json()}[builtin.id]["enabled"] is True
                r = await cl.put(f"{API}/sources/{builtin.id}", headers=h["admin"], json={"enabled": True})
                assert r.json()["enabled"] is True and len((await cl.get(API + "/", headers=h["viewer"])).json()) == 2

                # a paused own source hides its items too; deleting it removes them
                async with AsyncSessionLocal() as db:
                    own_src = await db.get(BriefSource, mine["id"])
                    await make_item(db, own_src, "o1", title="own item")
                assert len((await cl.get(API + "/", headers=h["viewer"])).json()) == 2
                await cl.put(f"{API}/sources/{mine['id']}", headers=h["admin"], json={"enabled": True})
                assert len((await cl.get(API + "/", headers=h["viewer"])).json()) == 3
                assert (await cl.delete(f"{API}/sources/{mine['id']}", headers=h["admin"])).status_code == 204
                assert len((await cl.get(API + "/", headers=h["viewer"])).json()) == 2
                assert (await cl.delete(f"{API}/sources/{mine['id']}", headers=h["admin"])).status_code == 404

            async with AsyncSessionLocal() as db:
                assert (await db.get(BriefSource, builtin.id)) is not None  # the shared row itself is untouched
                assert (await db.get(Tenant, t.id)).briefs_disabled_sources == []
                created = await _audit(db, t.id, "brief_source.created")
                assert len(created) == 3 and created[0].user_id == users["admin"].id
                assert created[0].entity_type == "brief_source" and created[0].entity_id == mine["id"]
                assert created[0].changes == {"name": "Vendor blog", "url": "https://blog.example.test/feed.xml", "kind": "news"}
                updated = [r.changes for r in await _audit(db, t.id, "brief_source.updated")]
                assert updated == [
                    {"source": "Vendor PSIRT", "name": "Vendor PSIRT", "enabled": False},
                    {"source": "Built-in feed", "enabled": False, "builtin": True},
                    {"source": "Built-in feed", "enabled": True, "builtin": True},
                    {"source": "Vendor PSIRT", "enabled": True}]
                deleted = await _audit(db, t.id, "brief_source.deleted")
                assert [r.entity_id for r in deleted] == [mine["id"]]
        finally:
            await cleanup_sources(ids)
    run(body)


def test_correlation_with_observables():
    HASH = "cd" * 32

    async def body(ids):
        art_ids, ioc_ids = [], []

        async def artifact(db, tenant, value, cases=(), **kw):
            a = Artifact(tenant_id=tenant.id, value=value, **kw)
            db.add(a)
            await db.flush()
            art_ids.append(a.id)
            db.add_all([CaseArtifact(case_id=c.id, artifact_id=a.id) for c in cases])
            await db.commit()

        async def ioc(db, tenant, value, case=None, ioc_type="other"):
            i = IOC(tenant_id=tenant.id, value=value, ioc_type=ioc_type, case_id=case.id if case else None)
            db.add(i)
            await db.commit()
            ioc_ids.append(i.id)

        try:
            async with AsyncSessionLocal() as db:
                t, h, _ = await make_org(db, ids)
                other, oh, _ = await make_org(db, ids, roles=("admin",))
                shared = await make_source(db, ids, None, name="Shared")
                a = await make_item(db, shared, "a", title="Botnet report", age=timedelta(hours=1), cves=["CVE-2026-7777"],
                                    indicators=[{"type": "ip", "value": "203.0.113.7"},
                                                {"type": "domain", "value": "evil.example.net"},
                                                {"type": "domain", "value": "unseen.example.net"}])
                b = await make_item(db, shared, "b", title="Loader", age=timedelta(hours=2),
                                    indicators=[{"type": "file_hash", "value": HASH}])
                c = await make_item(db, shared, "c", title="Only theirs", age=timedelta(hours=3),
                                    indicators=[{"type": "domain", "value": "theirs.example.org"},
                                                {"type": "url", "value": "https://evil.example.net/x"}])
                d = await make_item(db, shared, "d", title="Not computed yet", age=timedelta(hours=4), indicators=None)

                cases = [await make_case(db, ids, t, title=f"case {n}") for n in range(6)]
                await artifact(db, t, "203.0.113.7", cases)                  # six cases: five are listed
                await artifact(db, t, "EVIL.Example.NET")                    # case-insensitive, on no case
                await ioc(db, t, HASH.upper(), cases[0], "file_hash")        # an IOC, on a case
                await ioc(db, t, "cve-2026-7777")                            # a CVE id held as an IOC
                private = ArtifactTypeDefinition(tenant_id=t.id, key="secret_ref", label="Secret", private=True)
                db.add(private)
                await db.flush()
                await artifact(db, t, "unseen.example.net", custom_type_id=private.id)  # private type: never matched
                # the other organization's observables (and its case title) must never reach t
                theirs = await make_case(db, ids, other, title="OTHER ORGANIZATION SECRET CASE")
                await artifact(db, other, "theirs.example.org", [theirs])
                await artifact(db, other, "unseen.example.net", [theirs])

            async with client() as cl:
                async def listed(headers=h["viewer"], **params):
                    r = await cl.get(API + "/", headers=headers, params=params)
                    assert r.status_code == 200, r.text
                    return {i["title"]: i for i in r.json()}, int(r.headers["X-Total-Count"])

                assert (await cl.get("/api/v1/users/me", headers=h["viewer"])).json()["briefs_correlate"] is True
                items, total = await listed()
                assert total == 4 and "SECRET" not in str(items)
                # indicators are part of the item, matched or not
                assert items["Botnet report"]["indicators"][2] == {"type": "domain", "value": "unseen.example.net"}
                assert items["Not computed yet"]["indicators"] == [] and items["Not computed yet"]["matches"] == []
                m = items["Botnet report"]["matches"]
                assert [(x["value"], x["type"], x["ioc"]) for x in m] == [
                    ("CVE-2026-7777", "cve", True), ("203.0.113.7", "ip", False), ("evil.example.net", "domain", False)]
                assert m[0]["cases"] == [] and m[2]["cases"] == []
                assert m[1]["cases"] == [{"id": x.id, "title": x.title} for x in reversed(cases[1:])]  # 5 of 6
                assert items["Loader"]["matches"] == [
                    {"value": HASH, "type": "file_hash", "ioc": True, "cases": [{"id": cases[0].id, "title": "case 0"}]}]
                # exact values only: a URL on a matched domain is not a match; nor is another organization's observable
                assert items["Only theirs"]["matches"] == []

                # the filter, its total, and combined with the others
                assert [list(x) for x in [(await listed(matched="true"))[0]]] == [["Botnet report", "Loader"]]
                assert (await listed(matched="true"))[1] == 2
                assert list((await listed(matched="true", q="loader"))[0]) == ["Loader"]
                assert (await listed(matched="false"))[1] == 4
                assert (await cl.get(API + "/unread-count", headers=h["viewer"])).json() == {
                    "count": 4, "capped": False, "matched_unread": 2}

                # the other organization sees its own matches, with its own case
                theirs_items, n = await listed(headers=oh["admin"], matched="true")
                assert n == 2 and list(theirs_items) == ["Botnet report", "Only theirs"]
                assert theirs_items["Only theirs"]["matches"] == [{
                    "value": "theirs.example.org", "type": "domain", "ioc": False,
                    "cases": [{"id": theirs.id, "title": "OTHER ORGANIZATION SECRET CASE"}]}]
                assert [x["value"] for x in theirs_items["Botnet report"]["matches"]] == ["unseen.example.net"]

                # an observable added later flags an older brief (nothing is stored per organization)
                async with AsyncSessionLocal() as db:
                    await ioc(db, t, "https://EVIL.example.net/x", ioc_type="url")
                assert (await listed(matched="true"))[1] == 3
                async with AsyncSessionLocal() as db:
                    await db.execute(delete(IOC).where(IOC.id == ioc_ids.pop()))
                    await db.commit()

                # read-all honours the filter
                assert (await cl.post(API + "/read-all", headers=h["viewer"], params={"matched": "true"})).json() == {"marked": 2}
                assert (await cl.get(API + "/unread-count", headers=h["viewer"])).json() == {
                    "count": 2, "capped": False, "matched_unread": 0}
                assert (await cl.get(API + "/unread-count", headers=h["analyst"])).json()["matched_unread"] == 2

                # the option: admin only, audited; off = no matches, filter ignored, indicators still shown
                sec = "/api/v1/tenants/current/security"
                assert (await cl.get(sec, headers=h["admin"])).json()["briefs_correlate"] is True
                for role in ("analyst", "viewer"):
                    assert (await cl.put(sec, headers=h[role], json={"briefs_correlate": False})).status_code == 403
                r = await cl.put(sec, headers=h["admin"], json={"briefs_correlate": False})
                assert r.status_code == 200 and r.json()["briefs_correlate"] is False and r.json()["briefs_enabled"] is True
                await cl.put(sec, headers=h["admin"], json={"briefs_correlate": False})  # no change, no audit row
                assert (await cl.get("/api/v1/users/me", headers=h["viewer"])).json()["briefs_correlate"] is False
                items, total = await listed(matched="true")
                assert total == 4 and all(i["matches"] == [] for i in items.values())
                assert len(items["Botnet report"]["indicators"]) == 3
                assert (await cl.get(API + "/unread-count", headers=h["analyst"])).json() == {
                    "count": 4, "capped": False, "matched_unread": 0}
                assert (await cl.post(API + "/read-all", headers=h["analyst"], params={"matched": "true"})).json() == {"marked": 4}
            async with AsyncSessionLocal() as db:
                rows = await _audit(db, t.id, "tenant.briefs_correlate_toggled")
                assert [r.changes for r in rows] == [{"briefs_correlate": False}]
            assert b.id and c.id and d.id and a.id
        finally:
            async with AsyncSessionLocal() as db:  # only the rows made here; cases and tenants go with run()'s cleanup
                await db.execute(delete(CaseArtifact).where(CaseArtifact.artifact_id.in_(art_ids or [-1])))
                await db.execute(delete(Artifact).where(Artifact.id.in_(art_ids or [-1])))
                await db.execute(delete(IOC).where(IOC.id.in_(ioc_ids or [-1])))
                await db.commit()
            await cleanup_sources(ids)
    run(body)
