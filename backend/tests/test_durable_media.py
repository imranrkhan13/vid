"""Media survives an ephemeral-disk wipe when VGOH_DB_BLOBS=1 (Render free tier restarts lose local files)."""
import shutil


def test_persist_and_restore_roundtrip(env, monkeypatch):
    monkeypatch.setenv("VGOH_DB_BLOBS", "1")
    from app.core.config import settings
    from app.storage.local import persist_file, restore_file, project_dir
    f = project_dir("usr_x", "prj_x") / "clips" / "a.mp4"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_bytes(b"clip-bytes" * 1000)
    persist_file(f)
    shutil.rmtree(settings.media_dir)  # simulate restart on an ephemeral disk
    assert not f.exists()
    assert restore_file(f) is True
    assert f.read_bytes() == b"clip-bytes" * 1000


def test_restore_disabled_without_flag(env, monkeypatch):
    monkeypatch.delenv("VGOH_DB_BLOBS", raising=False)
    from app.storage.local import restore_file, project_dir
    assert restore_file(project_dir("u", "p") / "missing.mp4") is False
