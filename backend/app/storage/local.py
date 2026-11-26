"""Storage adapter. Local filesystem here; the interface is small so an S3-compatible adapter can replace it.
All paths are resolved inside the media root; user input never becomes a path component without sanitizing."""
from __future__ import annotations
import re
from pathlib import Path

from app.core.config import settings

_SAFE = re.compile(r"[^A-Za-z0-9._-]")


def safe_name(name: str, default: str = "file") -> str:
    n = _SAFE.sub("_", Path(name or default).name)[:80].strip(".") or default
    return n


def project_dir(user_id: str, project_id: str) -> Path:
    p = settings.media_dir / safe_name(user_id) / safe_name(project_id)
    p.mkdir(parents=True, exist_ok=True)
    return p


def resolve_inside(path: str | Path) -> Path:
    p = Path(path).resolve()
    root = settings.media_dir.resolve()
    if root not in p.parents and p != root:
        raise PermissionError("path outside media root")
    return p
