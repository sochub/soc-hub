import asyncio
import secrets
from io import BytesIO

import httpx
from PIL import Image
from sqlalchemy import delete, select

from app.core import security
from app.core.security import get_password_hash
from app.db.session import AsyncSessionLocal, engine
from app.main import app
from app.models.audit_log import AuditLog
from app.models.membership import TenantMembership
from app.models.tenant import Tenant
from app.models.user import User
from app.storage import get_storage

URL = "/api/v1/users/me/avatar"


def _png(size=(600, 400), mode="RGB", fmt="PNG", **kw):
    b = BytesIO()
    Image.new(mode, size, "red" if mode == "RGB" else 0).save(b, fmt, **kw)
    return b.getvalue()


def _exif_jpeg():
    im = Image.new("RGB", (600, 400), "blue")
    ex = Image.Exif()
    ex[0x010E] = "secret description"
    ex[0x0112] = 6
    b = BytesIO()
    im.save(b, "JPEG", exif=ex)
    return b.getvalue()


def _up(data, name="a.png", ctype="image/png"):
    return {"file": (name, data, ctype)}


async def _mk(h, tag, tenant_ids, super_admin=False):
    async with AsyncSessionLocal() as db:
        u = User(email=f"wf-test-{h}-{tag}@example.com", hashed_password=get_password_hash("x" * 14),
                 is_active=True, is_super_admin=super_admin)
        db.add(u)
        await db.flush()
        for t in tenant_ids:
            db.add(TenantMembership(user_id=u.id, tenant_id=t, role="analyst"))
        await db.commit()
        return int(u.id)


def _hdr(u, tid):
    return {"Authorization": f"Bearer {security.issue_access_token(u, tid)}"}


async def _user(uid):
    async with AsyncSessionLocal() as db:
        return (await db.execute(select(User).where(User.id == uid))).scalars().one()


def _scenario(body):
    async def go():
        await engine.dispose()
        h = secrets.token_hex(3)
        async with AsyncSessionLocal() as db:
            t1 = Tenant(name="wf-test", slug=f"wf-test-{h}-av1")
            t2 = Tenant(name="wf-test", slug=f"wf-test-{h}-av2")
            db.add_all([t1, t2])
            await db.commit()
            tids = (int(t1.id), int(t2.id))
        uids = [
            await _mk(h, "a", [tids[0]]),
            await _mk(h, "b", [tids[0]]),
            await _mk(h, "c", [tids[1]]),
            await _mk(h, "s", [], super_admin=True),
        ]
        keys = []
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
                await body(c, tids, uids, keys)
        finally:
            for u in uids:  # the key actually stored (if any) is whatever is on the row now
                row = await _user(u)
                if row.avatar_key:
                    keys.append(row.avatar_key)
            for k in set(keys):
                await get_storage().delete(k)
            async with AsyncSessionLocal() as db:
                await db.execute(delete(AuditLog).where(AuditLog.entity_type == "user", AuditLog.entity_id.in_(uids)))
                await db.execute(delete(TenantMembership).where(TenantMembership.user_id.in_(uids)))
                await db.execute(delete(User).where(User.id.in_(uids)))
                await db.execute(delete(Tenant).where(Tenant.id.in_(tids)))
                await db.commit()
            await engine.dispose()
    asyncio.run(go())


def test_avatar_reencoded():
    async def body(c, tids, uids, keys):
        a = await _user(uids[0])
        hdr = _hdr(a, tids[0])
        for data, name, ct in ((_png(), "a.png", "image/png"), (_exif_jpeg(), "a.jpg", "image/jpeg")):
            r = await c.put(URL, headers=hdr, files=_up(data, name, ct))
            assert r.status_code == 200, r.text
            assert r.json()["has_avatar"] is True
            g = await c.get(f"/api/v1/users/{uids[0]}/avatar", headers=hdr)
            assert g.status_code == 200
            assert g.headers["content-type"] == "image/webp"
            assert g.headers["cache-control"] == "private, max-age=300"
            assert g.headers["x-content-type-options"] == "nosniff"
            im = Image.open(BytesIO(g.content))
            assert im.format == "WEBP" and im.size == (256, 256)
            assert not im.getexif() and "exif" not in im.info
    _scenario(body)


def test_avatar_rejects_fake_and_bomb():
    async def body(c, tids, uids, keys):
        hdr = _hdr(await _user(uids[0]), tids[0])
        bomb = _png((9000, 9000), mode="1")  # >80 MP -> DecompressionBombError
        warn = _png((7000, 7000), mode="1")  # ~49 MP -> warning only, must still be 422
        cases = [
            (b"not an image", "x.png", "image/png"),
            (_png(fmt="GIF"), "x.gif", "image/gif"),
            (b"\0" * (2 * 1024 * 1024 + 1), "big.png", "image/png"),
            (bomb, "bomb.png", "image/png"),
            (warn, "warn.png", "image/png"),
        ]
        for data, name, ct in cases:
            r = await c.put(URL, headers=hdr, files=_up(data, name, ct))
            assert r.status_code == 422, (name, r.status_code, r.text)
        assert not (await _user(uids[0])).avatar_key
    _scenario(body)


def test_avatar_access_scoped():
    async def body(c, tids, uids, keys):
        a, b, other, sa = [await _user(u) for u in uids]
        r = await c.put(URL, headers=_hdr(a, tids[0]), files=_up(_png()))
        assert r.status_code == 200, r.text
        path = f"/api/v1/users/{uids[0]}/avatar"
        assert (await c.get(path, headers=_hdr(b, tids[0]))).status_code == 200
        assert (await c.get(path, headers=_hdr(other, tids[1]))).status_code == 404
        assert (await c.get(path, headers=_hdr(sa, None))).status_code == 200
        # target with no avatar -> 404 even for a same-tenant member
        assert (await c.get(f"/api/v1/users/{uids[1]}/avatar", headers=_hdr(a, tids[0]))).status_code == 404
    _scenario(body)


def test_avatar_replace_deletes_old(monkeypatch):
    async def body(c, tids, uids, keys):
        hdr = _hdr(await _user(uids[0]), tids[0])
        st = get_storage()
        deleted = []
        real = type(st).delete

        async def rec(self, key):
            deleted.append(key)
            await real(self, key)
        monkeypatch.setattr(type(st), "delete", rec)
        assert (await c.put(URL, headers=hdr, files=_up(_png()))).status_code == 200
        first = (await _user(uids[0])).avatar_key
        keys.append(first)
        assert (await c.put(URL, headers=hdr, files=_up(_png()))).status_code == 200
        second = (await _user(uids[0])).avatar_key
        assert second != first and first in deleted and second not in deleted
    _scenario(body)


def test_avatar_delete():
    async def body(c, tids, uids, keys):
        hdr = _hdr(await _user(uids[0]), tids[0])
        assert (await c.put(URL, headers=hdr, files=_up(_png()))).status_code == 200
        keys.append((await _user(uids[0])).avatar_key)
        assert (await c.delete(URL, headers=hdr)).status_code == 204
        assert (await c.get(f"/api/v1/users/{uids[0]}/avatar", headers=hdr)).status_code == 404
        assert (await _user(uids[0])).avatar_key is None
    _scenario(body)
