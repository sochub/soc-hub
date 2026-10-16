from datetime import datetime, timedelta, timezone

from app.db.session import AsyncSessionLocal
from app.guests import tokens
from app.models.case_guest import CaseGuest
from tests.guest_fixtures import client, make_case, make_grant, make_tenant, run, session_headers

EXISTING = ["/api/v1/cases/", "/api/v1/alerts/", "/api/v1/iocs/", "/api/v1/users/me",
            "/api/v1/copilot/sessions/general", "/api/v1/stats/", "/api/v1/notifications/"]


def test_guest_token_rejected_by_every_existing_route():
    async def body(ids):
        h = session_headers(ids.email("iso"))
        async with client() as c:
            for url in EXISTING:
                r = await c.get(url, headers=h)
                assert r.status_code == 401, f"{url} -> {r.status_code}"
            for purpose_token in (tokens.issue_link(1),):
                r = await c.get("/api/v1/cases/", headers={"Authorization": f"Bearer {purpose_token}"})
                assert r.status_code == 401
    run(body)


def test_grant_checks():
    async def body(ids):
        async with AsyncSessionLocal() as db:
            t = await make_tenant(db, ids)
            t_sso = await make_tenant(db, ids, require_sso=True, sso=True)
            t_empty = await make_tenant(db, ids, allow=())
            c, c2 = await make_case(db, ids, t), await make_case(db, ids, t)
            c_sso, c_empty = await make_case(db, ids, t_sso), await make_case(db, ids, t_empty)
            e = ids.email("ok")
            g = await make_grant(db, c, e)
            await make_grant(db, c_sso, e)
            await make_grant(db, c_empty, e)
            revoked = await make_grant(db, c2, ids.email("rev"), revoked=True)
            expired = await make_grant(db, c2, ids.email("exp"), expires_in=timedelta(seconds=-1))
        async with client() as cl:
            url = lambda cid: f"/api/v1/guest/cases/{cid}"  # noqa: E731
            assert (await cl.get(url(c.id), headers=session_headers(e))).status_code == 200
            assert (await cl.get(url(c2.id), headers=session_headers(e))).status_code == 404       # wrong case
            assert (await cl.get(url(c2.id), headers=session_headers(revoked.email))).status_code == 404
            assert (await cl.get(url(c2.id), headers=session_headers(expired.email))).status_code == 404
            assert (await cl.get(url(c_empty.id), headers=session_headers(e))).status_code == 404  # no allowlist
            assert (await cl.get(url(c_sso.id), headers=session_headers(e))).status_code == 404    # link session
            assert (await cl.get(url(c_sso.id), headers=session_headers(e, "sso", t.id))).status_code == 404
            assert (await cl.get(url(c_sso.id), headers=session_headers(e, "sso", t_sso.id))).status_code == 200
            # Review Focus 2: grant expires mid-session -> 404 with the same still-valid session
            async with AsyncSessionLocal() as db:
                row = await db.get(CaseGuest, g.id)
                row.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
                await db.commit()
            assert (await cl.get(url(c.id), headers=session_headers(e))).status_code == 404
            assert (await cl.get(url(c.id))).status_code == 401
    run(body)
