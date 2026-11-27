import asyncio
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """Fresh data dir + database per test. DATABASE_URL may point at PostgreSQL (see test_postgres.py)."""
    monkeypatch.setenv("VGOH_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("VGOH_MASTER_KEY", "test-master-key")
    monkeypatch.setenv("VGOH_MOCK_FAST", "1")
    monkeypatch.setenv("VGOH_RUN_WORKER", "0")
    monkeypatch.setenv("VGOH_ALLOW_PRIVATE_PROVIDER_HOSTS", "1")
    if "VGOH_TEST_DATABASE_URL" in os.environ:
        monkeypatch.setenv("DATABASE_URL", os.environ["VGOH_TEST_DATABASE_URL"])
    else:
        monkeypatch.delenv("DATABASE_URL", raising=False)
    from app.database import session
    session.reset_engine()
    from app.database.session import init_db, get_engine
    from app.database.models import Base
    eng = init_db()
    if "VGOH_TEST_DATABASE_URL" in os.environ:
        Base.metadata.drop_all(eng)
        Base.metadata.create_all(eng)
    from app.auth.auth import reset_rate_limits
    reset_rate_limits()
    from app.database.session import new_session
    from app.providers.registry import seed_catalog
    db = new_session()
    seed_catalog(db)
    db.close()
    yield tmp_path
    session.reset_engine()


class Client:
    def __init__(self, app):
        from fastapi.testclient import TestClient
        self.c = TestClient(app, headers={"X-VGOH": "1"})

    def __getattr__(self, name):
        return getattr(self.c, name)

    def ok(self, method, url, **kw):
        r = getattr(self.c, method)(url, **kw)
        assert r.status_code < 300, f"{method} {url} -> {r.status_code} {r.text[:400]}"
        return r.json()


@pytest.fixture()
def app_client(env):
    from app.main import app
    return lambda: Client(app)


def drive(pred, timeout=240, interval=0.15):
    """Run the engine tick loop (what the worker thread does) until pred() is true."""
    from app.workflows import engine
    t0 = time.time()
    while time.time() - t0 < timeout:
        asyncio.run(engine.tick())
        if pred():
            return True
        time.sleep(interval)
    raise AssertionError("condition not reached in time")


def make_wav(path: Path, seconds: float):
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"sine=frequency=330:duration={seconds}", "-ar", "48000", str(path)], check=True)
    return path


def signup(cl, email="a@example.com", pw="correct horse battery"):
    return cl.ok("post", "/api/v1/auth/register", json={"email": email, "password": pw, "name": email.split("@")[0]})
