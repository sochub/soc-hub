import asyncio
import tempfile

import boto3

CHUNK = 65536


class S3Storage:
    def __init__(self, bucket, region=None, endpoint_url=None, prefix=""):
        self.bucket, self.prefix = bucket, prefix or ""
        # Fresh session per instance: the default session is not thread-safe and calls run in to_thread.
        self.client = boto3.session.Session().client("s3", region_name=region, endpoint_url=endpoint_url)

    def _key(self, key):
        return f"{self.prefix}{key}"

    async def put(self, key, chunks) -> int:
        n = 0
        with tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024) as tmp:
            async for c in chunks:
                tmp.write(c)
                n += len(c)
            tmp.seek(0)
            await asyncio.to_thread(self.client.upload_fileobj, tmp, self.bucket, self._key(key),
                                    ExtraArgs={"ServerSideEncryption": "AES256"})
        return n

    async def open(self, key):
        body = (await asyncio.to_thread(self.client.get_object, Bucket=self.bucket, Key=self._key(key)))["Body"]
        it = body.iter_chunks(CHUNK)
        while True:
            c = await asyncio.to_thread(next, it, None)
            if c is None:
                break
            yield c

    async def delete(self, key) -> None:
        await asyncio.to_thread(self.client.delete_object, Bucket=self.bucket, Key=self._key(key))
