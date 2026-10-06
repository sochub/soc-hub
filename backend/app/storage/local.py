import asyncio
import os
from pathlib import Path

from app.storage import StorageError

CHUNK = 65536


class LocalStorage:
    def __init__(self, root: str):
        self.root = Path(root)

    def _path(self, key: str) -> Path:
        if not key or key.startswith("/") or "\\" in key:
            raise StorageError("invalid storage key")
        root = self.root.resolve()
        p = (root / key).resolve()
        if root not in p.parents:
            raise StorageError("invalid storage key")
        return p

    def _mkdirs(self, parent: Path) -> None:
        root = self.root.resolve()
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        for comp in parent.relative_to(root).parts:
            root = root / comp
            if not root.exists():
                root.mkdir()
                os.chmod(root, 0o700)  # nosemgrep: python.lang.security.audit.insecure-file-permissions.insecure-file-permissions -- 0700 is owner-only

    async def put(self, key, chunks) -> int:
        path = self._path(key)
        await asyncio.to_thread(self._mkdirs, path.parent)
        self._path(key)  # re-check after mkdir: a symlinked parent must still resolve inside root
        part = path.with_name(path.name + ".part")
        fd = await asyncio.to_thread(
            os.open, part, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        f = os.fdopen(fd, "wb")
        n = 0
        try:
            async for c in chunks:
                await asyncio.to_thread(f.write, c)
                n += len(c)
            await asyncio.to_thread(f.flush)
            await asyncio.to_thread(os.fsync, f.fileno())
            f.close()
            await asyncio.to_thread(os.replace, part, path)
        except BaseException:
            try:
                f.close()
            except Exception:
                pass
            await asyncio.to_thread(lambda: part.unlink(missing_ok=True))
            raise
        return n

    async def open(self, key):
        path = self._path(key)
        f = await asyncio.to_thread(open, path, "rb")
        try:
            while True:
                c = await asyncio.to_thread(f.read, CHUNK)
                if not c:
                    break
                yield c
        finally:
            f.close()

    async def delete(self, key) -> None:
        path = self._path(key)
        await asyncio.to_thread(lambda: path.unlink(missing_ok=True))
