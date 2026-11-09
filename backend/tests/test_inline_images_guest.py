from sqlalchemy import select

from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.models.case import TimelineEvent
from app.services.attachments import store_upload
from tests.guest_fixtures import client, make_case, make_grant, make_tenant, run, session_headers
from tests.test_inline_images import PNG


async def _chunks(b: bytes):
    yield b


async def _inline(db, case, *, inline=True):
    return await store_upload(db, case=case, user_id=None, filename="p.png", content_type="image/png",
                              chunks=_chunks(PNG), is_malicious=False, description=None, is_inline=inline)


def _note(case_id, att_id, **kw):
    return TimelineEvent(case_id=case_id, event_type="comment", content=f"look ![x](/attachments/{att_id}/raw)", **kw)


def test_guest_raw_allowed_when_referenced_by_shared_note(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "STORAGE_LOCAL_PATH", str(tmp_path))

    async def body(ids):
        async with AsyncSessionLocal() as db:
            t = await make_tenant(db, ids)
            c = await make_case(db, ids, t)
            email = ids.email("viewer")
            await make_grant(db, c, email, "viewer")
            att = await _inline(db, c)
            db.add(_note(c.id, att.id, shared_with_guests=True, guest_id=None))
            await db.commit()
            cid, aid = c.id, att.id
        async with client() as cl:
            r = await cl.get(f"/api/v1/guest/cases/{cid}/attachments/{aid}/raw",
                             headers=session_headers(email, link_tenant_id=t.id))
        assert r.status_code == 200, r.text
        assert r.content == PNG
        assert r.headers["content-type"] == "image/png"
        assert r.headers["content-disposition"] == "inline"
        assert r.headers["x-content-type-options"] == "nosniff"
        assert r.headers["content-security-policy"] == "sandbox"
        assert r.headers["cache-control"] == "private, max-age=300"

    run(body)


def test_guest_raw_denied_unshared(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "STORAGE_LOCAL_PATH", str(tmp_path))

    async def body(ids):
        async with AsyncSessionLocal() as db:
            t = await make_tenant(db, ids)
            c = await make_case(db, ids, t)
            c2 = await make_case(db, ids, t, title="other")
            email = ids.email("viewer")
            await make_grant(db, c, email, "viewer")
            att = await _inline(db, c)
            plain = await _inline(db, c, inline=False)
            other = await _inline(db, c2)
            db.add_all([
                _note(c.id, att.id, shared_with_guests=False),                      # not shared
                _note(c.id, plain.id, shared_with_guests=True),                     # shared but not inline
                TimelineEvent(case_id=c.id, event_type="comment", content="no ref", shared_with_guests=True),
                _note(c.id, other.id, shared_with_guests=True),                     # other case's attachment
            ])
            await db.commit()
            cid, aid, pid, oid = c.id, att.id, plain.id, other.id
        h = session_headers(email, link_tenant_id=t.id)
        async with client() as cl:
            for a in (aid, pid, oid):
                r = await cl.get(f"/api/v1/guest/cases/{cid}/attachments/{a}/raw", headers=h)
                assert r.status_code == 404, (a, r.text)
            # sharing opens access, unsharing closes it
            async with AsyncSessionLocal() as db:
                ev = (await db.execute(select(TimelineEvent).where(
                    TimelineEvent.case_id == cid, TimelineEvent.content.contains(f"/attachments/{aid}/raw")))
                ).scalars().one()
                evid = ev.id
                ev.shared_with_guests = True
                await db.commit()
            r = await cl.get(f"/api/v1/guest/cases/{cid}/attachments/{aid}/raw", headers=h)
            assert r.status_code == 200
            async with AsyncSessionLocal() as db:
                ev = (await db.execute(select(TimelineEvent).where(TimelineEvent.id == evid))).scalars().one()
                ev.shared_with_guests = False
                await db.commit()
            r = await cl.get(f"/api/v1/guest/cases/{cid}/attachments/{aid}/raw", headers=h)
            assert r.status_code == 404  # unsharing closes access

    run(body)
