import os

import pytest

from app.storage import StorageError, get_storage
from app.storage.local import LocalStorage
from app.storage.s3 import S3Storage


async def agen(*parts):
    for p in parts:
        yield p


async def collect(it):
    return b"".join([c async for c in it])


@pytest.mark.asyncio
async def test_local_round_trip_and_delete(tmp_path):
    s = LocalStorage(str(tmp_path))
    n = await s.put("tenants/1/cases/2/abc", agen(b"hello ", b"world"))
    assert n == 11 and await collect(s.open("tenants/1/cases/2/abc")) == b"hello world"
    assert oct(os.stat(tmp_path / "tenants/1/cases/2").st_mode & 0o777) == "0o700"
    await s.delete("tenants/1/cases/2/abc")
    await s.delete("tenants/1/cases/2/abc")  # missing is fine
    assert not (tmp_path / "tenants/1/cases/2/abc").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("key", ["../x", "/etc/passwd", "a/../../x"])
async def test_local_rejects_escape(tmp_path, key):
    with pytest.raises(StorageError):
        await LocalStorage(str(tmp_path)).put(key, agen(b"x"))


@pytest.mark.asyncio
async def test_local_rejects_symlink_escape(tmp_path):
    outside = tmp_path.parent / f"outside-{os.getpid()}"
    outside.mkdir(exist_ok=True)
    (tmp_path / "link").symlink_to(outside)
    try:
        with pytest.raises(StorageError):
            await LocalStorage(str(tmp_path)).put("link/x", agen(b"x"))
    finally:
        (tmp_path / "link").unlink()
        outside.rmdir()


@pytest.mark.asyncio
async def test_local_partial_never_visible(tmp_path):
    async def boom():
        yield b"part"
        raise RuntimeError("client went away")
    s = LocalStorage(str(tmp_path))
    with pytest.raises(RuntimeError):
        await s.put("k/obj", boom())
    assert not (tmp_path / "k/obj").exists() and not (tmp_path / "k/obj.part").exists()


class FakeBody:
    def __init__(self, data):
        self.data = data

    def iter_chunks(self, size):
        for i in range(0, len(self.data), size):
            yield self.data[i:i + size]


class FakeS3:
    def __init__(self):
        self.objects, self.calls = {}, []

    def upload_fileobj(self, f, bucket, key, ExtraArgs=None):
        self.calls.append(("put", bucket, key, ExtraArgs))
        self.objects[key] = f.read()

    def get_object(self, Bucket, Key):
        return {"Body": FakeBody(self.objects[Key])}

    def delete_object(self, Bucket, Key):
        self.calls.append(("delete", Bucket, Key))
        self.objects.pop(Key, None)


@pytest.mark.asyncio
async def test_s3_put_open_delete(monkeypatch):
    fake, seen = FakeS3(), {}

    class Sess:
        def client(self, name, region_name=None, endpoint_url=None):
            seen.update(name=name, region=region_name, endpoint=endpoint_url)
            return fake
    monkeypatch.setattr("app.storage.s3.boto3.session.Session", lambda: Sess())
    s = S3Storage("bkt", region="eu-west-1", endpoint_url="http://minio:9000", prefix="soc/")
    assert await s.put("tenants/1/x", agen(b"ab", b"cd")) == 4
    assert fake.calls[0] == ("put", "bkt", "soc/tenants/1/x", {"ServerSideEncryption": "AES256"})
    assert seen == {"name": "s3", "region": "eu-west-1", "endpoint": "http://minio:9000"}
    assert await collect(s.open("tenants/1/x")) == b"abcd"
    await s.delete("tenants/1/x")
    assert fake.calls[-1] == ("delete", "bkt", "soc/tenants/1/x")


def test_get_storage_selects_backend(monkeypatch, tmp_path):
    from app.core.config import settings
    monkeypatch.setattr(settings, "STORAGE_BACKEND", "local")
    monkeypatch.setattr(settings, "STORAGE_LOCAL_PATH", str(tmp_path))
    assert isinstance(get_storage(), LocalStorage)
    monkeypatch.setattr(settings, "STORAGE_BACKEND", "s3")
    monkeypatch.setattr(settings, "S3_BUCKET", "b")
    monkeypatch.setattr("app.storage.s3.boto3.session.Session", lambda: type("S", (), {"client": lambda *a, **k: FakeS3()})())
    assert isinstance(get_storage(), S3Storage)


def test_blank_s3_settings_become_none():
    from app.core.config import Settings
    s = Settings(_env_file=None, ENVIRONMENT="test", S3_BUCKET="", S3_REGION=" ", S3_ENDPOINT_URL="", S3_PREFIX="")
    assert s.S3_BUCKET is None and s.S3_REGION is None and s.S3_ENDPOINT_URL is None and s.S3_PREFIX == ""


def test_s3_backend_requires_bucket():
    from app.core.config import Settings
    with pytest.raises(ValueError, match="S3_BUCKET"):
        Settings(_env_file=None, ENVIRONMENT="test", STORAGE_BACKEND="s3", S3_BUCKET="")
