from __future__ import annotations
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def _b(name: str, default: bool = False) -> bool:
    v = os.environ.get(name)
    return default if v is None else v.lower() in ("1", "true", "yes", "on")


class Settings:
    """Environment-driven settings. Read on access so tests can override os.environ."""

    @property
    def data_dir(self) -> Path:
        p = Path(os.environ.get("VGOH_DATA_DIR", ROOT / "data")).resolve()
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def database_url(self) -> str:
        url = os.environ.get("DATABASE_URL")
        if url:
            if url.startswith("postgres://"):
                url = "postgresql://" + url[len("postgres://"):]
            if url.startswith("postgresql://"):  # pin the driver we ship (psycopg2); SQLAlchemy's default may differ by version
                url = "postgresql+psycopg2://" + url[len("postgresql://"):]
            return url
        return f"sqlite:///{self.data_dir / 'vgoh.sqlite'}"

    @property
    def media_dir(self) -> Path:
        p = self.data_dir / "media"
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def remote_dir(self) -> Path:
        p = self.data_dir / "mock_remote"
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def tick_seconds(self) -> float:
        return float(os.environ.get("VGOH_TICK_SECONDS", "0.5"))

    @property
    def max_upload_bytes(self) -> int:
        return int(os.environ.get("VGOH_MAX_UPLOAD_BYTES", str(200 * 1024 * 1024)))

    @property
    def cookie_secure(self) -> bool:
        return _b("VGOH_COOKIE_SECURE", False)

    @property
    def registration_open(self) -> bool:
        return os.environ.get("VGOH_REGISTRATION", "open") != "closed"

    @property
    def allow_private_provider_hosts(self) -> bool:
        return _b("VGOH_ALLOW_PRIVATE_PROVIDER_HOSTS", False)

    @property
    def run_worker(self) -> bool:
        return _b("VGOH_RUN_WORKER", True)

    @property
    def cors_origins(self) -> list[str]:
        return [o for o in os.environ.get("VGOH_CORS_ORIGINS", "").split(",") if o]

    @property
    def frontend_dir(self) -> Path:
        return Path(os.environ.get("VGOH_FRONTEND_DIR", ROOT / "frontend" / "out"))

    @property
    def mock_fast(self) -> bool:
        return _b("VGOH_MOCK_FAST", False)


settings = Settings()
