"""Storage adapter selection (section 5).

Default is the local filesystem. S3/MinIO is chosen only when configured, and its
SDK is imported lazily so it remains an optional dependency.
"""

from __future__ import annotations

import os

from app.storage.local import LocalFilesystemStorage, StorageAdapter


def default_storage(root: str | None = None) -> StorageAdapter:
    """Return local storage, or S3 when ``VGOH_S3_BUCKET`` is set."""
    bucket = os.environ.get("VGOH_S3_BUCKET")
    if bucket:
        try:
            from app.storage.s3 import S3Storage

            return S3Storage(
                bucket,
                endpoint_url=os.environ.get("VGOH_S3_ENDPOINT"),
                region=os.environ.get("VGOH_S3_REGION"),
            )
        except RuntimeError:
            # boto3 missing: fall back to local rather than crash at import time.
            pass
    return LocalFilesystemStorage(root or os.environ.get("VGOH_STORAGE_ROOT", "./.vgoh_media"))


__all__ = ["StorageAdapter", "LocalFilesystemStorage", "default_storage"]
