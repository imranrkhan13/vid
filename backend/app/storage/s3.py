"""S3/MinIO storage adapter (section 5).

Boto3 is an *optional* dependency and is imported lazily, only when this adapter
is constructed. The default deployment uses the local filesystem adapter, so the
harness runs and tests without any cloud SDK installed.
"""

from __future__ import annotations

from app.storage.local import StorageAdapter


class S3Storage(StorageAdapter):
    """Object storage via boto3. Constructed only when S3 is configured."""

    def __init__(
        self,
        bucket: str,
        *,
        endpoint_url: str | None = None,
        region: str | None = None,
    ) -> None:
        try:
            import boto3  # type: ignore
        except ImportError as exc:  # pragma: no cover - depends on optional dep
            raise RuntimeError(
                "boto3 is not installed; install it or use LocalFilesystemStorage"
            ) from exc
        self.bucket = bucket
        self._client = boto3.client("s3", endpoint_url=endpoint_url, region_name=region)

    def put(self, key: str, data: bytes) -> str:
        self._client.put_object(Bucket=self.bucket, Key=key, Body=data)
        return f"s3://{self.bucket}/{key}"

    def get(self, key: str) -> bytes:
        obj = self._client.get_object(Bucket=self.bucket, Key=key)
        return obj["Body"].read()

    def exists(self, key: str) -> bool:
        from botocore.exceptions import ClientError  # type: ignore

        try:
            self._client.head_object(Bucket=self.bucket, Key=key)
            return True
        except ClientError:
            return False
