"""Storage adapter interface and local filesystem implementation.

S3/MinIO adapters implement the same interface; the local adapter is the default
so the harness runs without external services.
"""

from __future__ import annotations

import abc
from pathlib import Path


class StorageAdapter(abc.ABC):
    @abc.abstractmethod
    def put(self, key: str, data: bytes) -> str: ...

    @abc.abstractmethod
    def get(self, key: str) -> bytes: ...

    @abc.abstractmethod
    def exists(self, key: str) -> bool: ...


class LocalFilesystemStorage(StorageAdapter):
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        # Reject traversal outside the storage root.
        candidate = (self.root / key).resolve()
        if not str(candidate).startswith(str(self.root.resolve())):
            raise ValueError("path traversal detected")
        return candidate

    def put(self, key: str, data: bytes) -> str:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return str(path)

    def get(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def exists(self, key: str) -> bool:
        return self._path(key).exists()
