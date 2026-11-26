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


def _durable() -> bool:
    import os
    return os.environ.get("VGOH_DB_BLOBS", "").lower() in ("1", "true", "yes")


class StorageBudgetError(Exception):
    """The database media budget would be exceeded. Callers must refuse or pause, never report durable success."""


def _budgets():
    import os
    return (int(os.environ.get("VGOH_DB_BLOB_BUDGET", str(300 * 1024 * 1024))), int(os.environ.get("VGOH_DB_BLOB_USER_BUDGET", str(60 * 1024 * 1024))))


def headroom(user_id: str) -> int | None:
    """Bytes the user may still mirror, or None when durable storage is off (local disk is then the store)."""
    if not _durable():
        return None
    from sqlalchemy import func
    from app.database.models import MediaBlob
    from app.database.session import new_session
    total, per_user = _budgets()
    prefix = str(settings.media_dir.resolve()) + "/" + safe_name(user_id) + "/"
    db = new_session()
    try:
        all_used = db.query(func.coalesce(func.sum(MediaBlob.size), 0)).scalar() or 0
        mine = db.query(func.coalesce(func.sum(MediaBlob.size), 0)).filter(MediaBlob.path.like(prefix + "%")).scalar() or 0
        return max(0, min(total - all_used, per_user - mine))
    finally:
        db.close()


def persist_file(path) -> None:
    """Copy a media file into the database when VGOH_DB_BLOBS=1 (needed on hosts with an ephemeral disk)."""
    if not path or not _durable():
        return
    p = Path(path)
    if not p.is_file():
        return
    from app.database.models import MediaBlob
    from app.database.session import new_session
    rp = str(resolve_inside(p))
    db = new_session()
    try:
        data = p.read_bytes()
        row = db.get(MediaBlob, rp)
        total_budget, user_budget = _budgets()
        prefix = str(settings.media_dir.resolve()) + "/"
        rel = rp[len(prefix):] if rp.startswith(prefix) else ""
        uid = rel.split("/", 1)[0]
        from sqlalchemy import func
        old = row.size if row is not None else 0
        used_all = db.query(func.coalesce(func.sum(MediaBlob.size), 0)).scalar() or 0
        used_user = (db.query(func.coalesce(func.sum(MediaBlob.size), 0)).filter(MediaBlob.path.like(prefix + uid + "/%")).scalar() or 0) if uid else 0
        if used_all - old + len(data) > total_budget or used_user - old + len(data) > user_budget:
            raise StorageBudgetError("storage budget reached for durable media")
        if row is None:
            db.add(MediaBlob(path=rp, size=len(data), data=data))
        else:
            row.data, row.size = data, len(data)
        db.commit()
    finally:
        db.close()


def restore_file(path) -> bool:
    """Recreate a missing media file from its database copy. Returns True when the file exists afterwards."""
    if not path:
        return False
    p = Path(path)
    if p.is_file():
        return True
    if not _durable():
        return False
    from app.database.models import MediaBlob
    from app.database.session import new_session
    db = new_session()
    try:
        row = db.get(MediaBlob, str(resolve_inside(p)))
        if row is None:
            return False
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(row.data)
        return True
    finally:
        db.close()
