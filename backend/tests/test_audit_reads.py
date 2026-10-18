from app.core.config import settings
from tests.audit_fixtures import hdr, rows, run


def test_case_view_is_a_read_and_list_is_not():
    async def body(c, w):
        assert (await c.get(f"/api/v1/cases/{w.case}", headers=hdr(w, "analyst"))).status_code == 200
        got = await rows(w, category="read")
        assert len(got) == 1 and got[0].action == "case.view" and got[0].entity_id == w.case
        assert got[0].tenant_id == w.ta and got[0].user_id == w.users["analyst"]
        await c.get("/api/v1/cases/", headers=hdr(w, "analyst"))
        assert len(await rows(w, category="read")) == 1
    run(body)


def test_failed_read_is_not_a_read():
    async def body(c, w):
        assert (await c.get(f"/api/v1/cases/{w.case}", headers=hdr(w, "admin_b"))).status_code == 404
        assert await rows(w, category="read") == []
    run(body)


def test_secret_list_is_a_read():
    async def body(c, w):
        assert (await c.get("/api/v1/secrets/", headers=hdr(w, "admin"))).status_code == 200
        got = await rows(w, action="secret.list")
        assert len(got) == 1 and got[0].tenant_id == w.ta
    run(body)


def test_download_streams_and_records_read(tmp_path, monkeypatch):
    """Upload a 2 MB attachment as the admin, download it, compare bytes, expect one attachment.download row."""
    monkeypatch.setattr(settings, "STORAGE_LOCAL_PATH", str(tmp_path))
    monkeypatch.setattr(settings, "MAX_UPLOAD_MB", 5)

    async def body(c, w):
        blob = bytes(range(256)) * 8192  # 2 MiB
        up = await c.post(f"/api/v1/cases/{w.case}/attachments", headers=hdr(w, "admin"),
                          files={"file": ("big.bin", blob, "application/octet-stream")})
        assert up.status_code in (200, 201), up.text
        att_id = up.json()["id"]
        dl = await c.get(f"/api/v1/cases/{w.case}/attachments/{att_id}/download", headers=hdr(w, "admin"))
        assert dl.status_code == 200 and dl.content == blob
        got = await rows(w, action="attachment.download")
        assert len(got) == 1 and (got[0].entity_type, got[0].entity_id) == ("attachment", att_id)
    run(body)
