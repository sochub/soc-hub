from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.api import deps
from app.db.session import AsyncSessionLocal
from app.main import app
from app.models.audit_log import AuditLog
from app.models.case import TimelineEvent
from app.models.case_guest import CaseGuest
from app.models.tenant import Tenant
from app.services import email_service
from tests.guest_fixtures import client, make_case, make_tenant, make_user, run


def _as(user, tenant_id, role="analyst"):
    app.dependency_overrides[deps.get_current_active_user] = lambda: user
    app.dependency_overrides[deps.get_effective_tenant_id] = lambda: tenant_id
    ok = lambda: user  # noqa: E731
    app.dependency_overrides[deps.require_analyst_or_above] = ok
    if role == "admin":
        app.dependency_overrides[deps.require_admin] = ok


def test_invite_list_patch_revoke_resend(monkeypatch):
    sent = []
    monkeypatch.setattr(email_service, "_smtp_configured", lambda: True)
    monkeypatch.setattr(email_service, "_send_email", lambda to, subj, body, log_recipient=True: sent.append((to, body)) or True)

    async def body(ids):
        async with AsyncSessionLocal() as db:
            t = await make_tenant(db, ids)
            other = await make_tenant(db, ids)
            c = await make_case(db, ids, t)
            c_other = await make_case(db, ids, other)
            u = await make_user(db, ids)
        _as(u, t.id)
        base = f"/api/v1/cases/{c.id}/guests"
        async with client() as cl:
            mixed = ids.email("jane").replace("g-", "G-").replace("dept.example", "Dept.Example")
            r = await cl.post(base, json={"email": mixed, "permission": "contributor"})
            assert r.status_code == 201, r.text
            g = r.json()
            assert g["email"] == mixed.lower() and g["permission"] == "contributor" and g["active"] is True
            exp = datetime.fromisoformat(g["expires_at"])
            assert timedelta(days=29) < exp - datetime.now(timezone.utc) <= timedelta(days=30)
            assert len(sent) == 1 and sent[0][0] == mixed.lower() and "/guest/link/" in sent[0][1]

            r = await cl.post(base, json={"email": "x@evil.example", "permission": "viewer"})
            assert r.status_code == 422
            r = await cl.post(base, json={"email": ids.email("p"), "permission": "admin"})
            assert r.status_code == 422
            past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
            r = await cl.post(base, json={"email": ids.email("p"), "permission": "viewer", "expires_at": past})
            assert r.status_code == 422
            far = (datetime.now(timezone.utc) + timedelta(days=400)).isoformat()
            r = await cl.post(base, json={"email": ids.email("p"), "permission": "viewer", "expires_at": far})
            assert r.status_code == 422
            assert (await cl.post(f"/api/v1/cases/{c_other.id}/guests",
                                  json={"email": ids.email("q"), "permission": "viewer"})).status_code == 404

            r = await cl.get(base)
            assert [x["id"] for x in r.json()] == [g["id"]]

            r = await cl.patch(f"{base}/{g['id']}", json={"permission": "viewer"})
            assert r.status_code == 200 and r.json()["permission"] == "viewer"
            r = await cl.patch(f"{base}/{g['id']}", json={"expires_at": past})
            assert r.status_code == 422

            assert (await cl.delete(f"{base}/{g['id']}")).status_code == 204
            assert (await cl.get(base)).json()[0]["active"] is False
            assert (await cl.post(f"{base}/{g['id']}/resend")).status_code == 409   # revoked: re-invite instead

            r = await cl.post(base, json={"email": mixed, "permission": "viewer"})  # re-invite reactivates
            assert r.status_code == 201 and r.json()["id"] == g["id"] and r.json()["active"] is True
            assert (await cl.post(f"{base}/{g['id']}/resend")).status_code == 204
            assert len(sent) == 3

            async with AsyncSessionLocal() as db:
                inv = (await db.execute(select(TimelineEvent.id).where(
                    TimelineEvent.case_id == c.id, TimelineEvent.event_type == "guest_invited"))).scalars().first()
            r = await cl.put(f"/api/v1/cases/{c.id}/timeline/{inv}/share", json={"shared": True})
            assert r.status_code == 422
            assert (await cl.put(f"/api/v1/cases/{c.id}/timeline/{inv}/share", json={"shared": False})).status_code == 200

        async with AsyncSessionLocal() as db:
            actions = (await db.execute(select(AuditLog.action).where(AuditLog.tenant_id == t.id))).scalars().all()
            assert {"guest_invited", "guest_updated", "guest_revoked"} <= set(actions)
            evs = (await db.execute(select(TimelineEvent).where(TimelineEvent.case_id == c.id))).scalars().all()
            assert any(e.event_type == "guest_invited" and e.shared_with_guests is False for e in evs)
    run(body)


def test_tenant_guest_settings_and_empty_allowlist_blocks():
    async def body(ids):
        async with AsyncSessionLocal() as db:
            t = await make_tenant(db, ids, allow=())
            t_sso = await make_tenant(db, ids, sso=True)
            c = await make_case(db, ids, t)
            u = await make_user(db, ids)
        _as(u, t.id, role="admin")
        async with client() as cl:
            r = await cl.post(f"/api/v1/cases/{c.id}/guests", json={"email": ids.email("a"), "permission": "viewer"})
            assert r.status_code == 422
            r = await cl.put("/api/v1/tenants/current/guests",
                             json={"domain_allowlist": [" Dept.Example ", "dept.example"], "require_sso": False})
            assert r.status_code == 200 and r.json() == {"domain_allowlist": ["dept.example"], "require_sso": False,
                                                         "sso_ready": False}
            r = await cl.put("/api/v1/tenants/current/guests", json={"domain_allowlist": ["not a domain"],
                                                                     "require_sso": False})
            assert r.status_code == 422
            r = await cl.put("/api/v1/tenants/current/guests", json={"domain_allowlist": [], "require_sso": True})
            assert r.status_code == 422  # SSO not ready on this tenant
            r = await cl.post(f"/api/v1/cases/{c.id}/guests", json={"email": ids.email("a"), "permission": "viewer"})
            assert r.status_code == 201
        _as(u, t_sso.id, role="admin")
        async with client() as cl:
            r = await cl.put("/api/v1/tenants/current/guests", json={"domain_allowlist": [], "require_sso": True})
            assert r.status_code == 200 and r.json()["require_sso"] is True
        async with AsyncSessionLocal() as db:
            assert (await db.get(Tenant, t_sso.id)).guest_require_sso is True
    run(body)


def test_guest_summary_requires_analyst(monkeypatch):
    from app.api.v1 import cases as cases_api

    async def body(ids):
        async with AsyncSessionLocal() as db:
            t = await make_tenant(db, ids)
            c = await make_case(db, ids, t)
            u = await make_user(db, ids)
        _as(u, t.id)
        monkeypatch.setattr(cases_api, "resolve_active_role", lambda *a, **k: "viewer")
        async with client() as cl:
            r = await cl.put(f"/api/v1/cases/{c.id}", json={"guest_summary": "x"})
            assert r.status_code == 403
        async with AsyncSessionLocal() as db:
            from app.models.case import Case
            assert (await db.get(Case, c.id)).guest_summary is None
    run(body)


def test_share_toggle_and_guest_summary(monkeypatch):
    from app.api.v1 import cases as cases_api
    monkeypatch.setattr(cases_api, "resolve_active_role", lambda *a, **k: "analyst")

    async def body(ids):
        async with AsyncSessionLocal() as db:
            t = await make_tenant(db, ids)
            c = await make_case(db, ids, t)
            u = await make_user(db, ids)
            ev = TimelineEvent(case_id=c.id, user_id=u.id, event_type="comment", content="note")
            db.add(ev)
            await db.commit()
            ev_id = ev.id
        _as(u, t.id)
        async with client() as cl:
            r = await cl.put(f"/api/v1/cases/{c.id}/timeline/{ev_id}/share", json={"shared": True})
            assert r.status_code == 200 and r.json()["shared_with_guests"] is True
            r = await cl.put(f"/api/v1/cases/{c.id}/timeline/999999999/share", json={"shared": True})
            assert r.status_code == 404
            r = await cl.put(f"/api/v1/cases/{c.id}", json={"guest_summary": "We are investigating."})
            assert r.status_code == 200 and r.json()["guest_summary"] == "We are investigating."
    run(body)
