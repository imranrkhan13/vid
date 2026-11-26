"""Storage budget boundary: no silent ephemeral-only success, tenant isolation of blobs."""
import os
os.environ["VGOH_DB_BLOBS"] = "1"
from pathlib import Path
import pytest
from app.core.config import settings
from app.database.models import MediaBlob
from app.database.session import init_db, new_session
from app.storage import local as L


@pytest.fixture(autouse=True)
def _db():
    init_db()
    db = new_session(); db.query(MediaBlob).delete(); db.commit(); db.close()


def _file(user, name, n):
    p = L.project_dir(user, "prj_x") / name
    p.write_bytes(b"x" * n)
    return p


def test_persist_refuses_over_user_budget(monkeypatch):
    monkeypatch.setenv("VGOH_DB_BLOB_USER_BUDGET", "1000")
    L.persist_file(_file("usr_a", "a.mp4", 600))
    with pytest.raises(L.StorageBudgetError):
        L.persist_file(_file("usr_a", "b.mp4", 600))
    assert L.headroom("usr_a") == 400
    assert L.headroom("usr_b") == 1000          # other tenant unaffected


def test_persist_refuses_over_total_budget(monkeypatch):
    monkeypatch.setenv("VGOH_DB_BLOB_BUDGET", "1000")
    monkeypatch.setenv("VGOH_DB_BLOB_USER_BUDGET", "5000")
    L.persist_file(_file("usr_c", "a.mp4", 700))
    with pytest.raises(L.StorageBudgetError):
        L.persist_file(_file("usr_d", "a.mp4", 700))


def test_replace_same_file_does_not_double_count(monkeypatch):
    monkeypatch.setenv("VGOH_DB_BLOB_USER_BUDGET", "1000")
    f = _file("usr_e", "a.mp4", 900)
    L.persist_file(f); L.persist_file(f)
