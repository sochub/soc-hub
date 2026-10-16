from sqlalchemy import func, select

from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.models.case import TimelineEvent
from app.models.case_attachment import CaseAttachment
from app.models.membership import TenantMembership
from app.models.notification import CaseFollower, Notification
from tests.guest_fixtures import client, make_case, make_grant, make_tenant, make_user, run, session_headers


def test_portal_visibility_and_contributions(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "STORAGE_LOCAL_PATH", str(tmp_path))
    monkeypatch.setattr(settings, "MAX_UPLOAD_MB", 1)

    async def body(ids):
        async with AsyncSessionLocal() as db:
            t = await make_tenant(db, ids)
            t_sso = await make_tenant(db, ids, require_sso=True, sso=True)
            u = await make_user(db, ids)
            db.add(TenantMembership(user_id=u.id, tenant_id=t.id, role="analyst"))
            c = await make_case(db, ids, t, title="Phish", guest_summary="Shared summary")
            c_sso = await make_case(db, ids, t_sso)
            db.add_all([
                TimelineEvent(case_id=c.id, user_id=u.id, event_type="comment", content="SECRET internal"),
                TimelineEvent(case_id=c.id, user_id=u.id, event_type="comment", content="shared note",
                              shared_with_guests=True),
                CaseFollower(case_id=c.id, user_id=u.id, tenant_id=t.id),
            ])
            await db.commit()
            ve, ce = ids.email("viewer"), ids.email("contrib")
            await make_grant(db, c, ve, "viewer")
            gc = await make_grant(db, c, ce, "contributor")
            await make_grant(db, c_sso, ce, "contributor")
            db.add(CaseAttachment(tenant_id=t.id, case_id=c.id, filename="analyst.bin", size_bytes=1,
                                  sha256="0" * 64, storage_key=f"k-{ids.h}", uploaded_by=u.id))
            await db.commit()
        V, C = session_headers(ve), session_headers(ce)
        async with client() as cl:
            # Review Focus 3: SSO-required tenant's case hidden from a link session
            r = await cl.get("/api/v1/guest/cases", headers=C)
            assert [x["id"] for x in r.json()] == [c.id], r.text
            assert r.json()[0]["permission"] == "contributor"

            r = await cl.get(f"/api/v1/guest/cases/{c.id}", headers=V)
            d = r.json()
            assert d["title"] == "Phish" and d["guest_summary"] == "Shared summary" and "description" not in d

            r = await cl.get(f"/api/v1/guest/cases/{c.id}/timeline", headers=V)
            assert [e["content"] for e in r.json()] == ["shared note"]
            assert "user_id" not in r.json()[0] and r.json()[0]["author"]

            assert (await cl.post(f"/api/v1/guest/cases/{c.id}/comments", json={"content": "x"},
                                  headers=V)).status_code == 403
            assert (await cl.post(f"/api/v1/guest/cases/{c.id}/attachments",
                                  files={"file": ("a.txt", b"x", "text/plain")}, headers=V)).status_code == 403

            r = await cl.post(f"/api/v1/guest/cases/{c.id}/comments", json={"content": "Here is context"},
                              headers=C)
            assert r.status_code == 201 and r.json()["author"] == ce
            assert (await cl.post(f"/api/v1/guest/cases/{c.id}/comments", json={"content": "  "},
                                  headers=C)).status_code == 422

            r = await cl.post(f"/api/v1/guest/cases/{c.id}/attachments",
                              files={"file": ("evidence.eml", b"From: x", "message/rfc822")}, headers=C)
            assert r.status_code == 201, r.text
            # Review Focus 4: oversized / empty -> 413 / 422, no orphan rows
            async with AsyncSessionLocal() as db:
                n0 = (await db.execute(select(func.count()).select_from(CaseAttachment)
                                       .where(CaseAttachment.case_id == c.id))).scalar()
            big = b"x" * (1024 * 1024 + 1)
            assert (await cl.post(f"/api/v1/guest/cases/{c.id}/attachments",
                                  files={"file": ("big.bin", big, "application/octet-stream")},
                                  headers=C)).status_code == 413
            assert (await cl.post(f"/api/v1/guest/cases/{c.id}/attachments",
                                  files={"file": ("e.txt", b"", "text/plain")}, headers=C)).status_code == 422
            async with AsyncSessionLocal() as db:
                n1 = (await db.execute(select(func.count()).select_from(CaseAttachment)
                                       .where(CaseAttachment.case_id == c.id))).scalar()
            assert n1 == n0

            r = await cl.get(f"/api/v1/guest/cases/{c.id}/attachments", headers=C)
            assert [a["filename"] for a in r.json()] == ["evidence.eml"]
            assert (await cl.get(f"/api/v1/guest/cases/{c.id}/attachments", headers=V)).json() == []

            r = await cl.get(f"/api/v1/guest/cases/{c.id}/timeline", headers=V)
            contents = [e["content"] for e in r.json()]
            assert "Here is context" in contents and any("evidence.eml" in x for x in contents)

            me = (await cl.get("/api/v1/guest/me", headers=C)).json()
            assert me == {"email": ce, "has_account": False, "mfa_enabled": False, "amr": "link"}

        async with AsyncSessionLocal() as db:
            att = (await db.execute(select(CaseAttachment).where(CaseAttachment.case_id == c.id,
                                                                 CaseAttachment.filename == "evidence.eml"))
                   ).scalars().one()
            assert att.uploaded_by_guest_id == gc.id and att.uploaded_by is None
            n = (await db.execute(select(Notification).where(Notification.case_id == c.id,
                                                             Notification.user_id == u.id))).scalars().all()
            assert any(ce in x.summary for x in n), [x.summary for x in n]
    run(body)
