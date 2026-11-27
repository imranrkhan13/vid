"""Runtime configuration.

Defaults favour local, dependency-light execution (SQLite + local filesystem +
in-process task runner). PostgreSQL, Redis/Celery and S3 are *optional* adapters
selected through environment variables — the spec (section 5) proposes them but
does not require them to run or test.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    database_url: str
    storage_root: Path
    allow_paid_spend: bool
    ffmpeg_bin: str
    provider_catalog_path: Path

    @staticmethod
    def from_env() -> "Settings":
        root = Path(os.environ.get("VGOH_STORAGE_ROOT", "./.vgoh_media")).resolve()
        catalog = Path(
            os.environ.get(
                "VGOH_PROVIDER_CATALOG",
                str(Path(__file__).resolve().parents[2] / "docs" / "provider_catalog.json"),
            )
        )
        return Settings(
            database_url=os.environ.get("VGOH_DATABASE_URL", "sqlite+pysqlite:///:memory:"),
            storage_root=root,
            # Default is zero paid spending until explicitly approved (section 27).
            allow_paid_spend=os.environ.get("VGOH_ALLOW_PAID_SPEND", "false").lower()
            == "true",
            ffmpeg_bin=os.environ.get("VGOH_FFMPEG_BIN", "ffmpeg"),
            provider_catalog_path=catalog,
        )
