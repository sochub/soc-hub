"""Evidence file storage: local disk or S3-compatible, chosen by settings."""
from app.core.config import settings


class StorageError(Exception):
    pass


def get_storage():
    if settings.STORAGE_BACKEND == "s3":
        from app.storage.s3 import S3Storage
        return S3Storage(settings.S3_BUCKET, region=settings.S3_REGION,
                         endpoint_url=settings.S3_ENDPOINT_URL, prefix=settings.S3_PREFIX)
    from app.storage.local import LocalStorage
    return LocalStorage(settings.STORAGE_LOCAL_PATH)
