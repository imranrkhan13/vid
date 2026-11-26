"""Crash recovery (README section 31 step 9): kill -9 the backend mid-generation, restart, finish without duplicates."""
import asyncio
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

from conftest import drive, signup

BACKEND = Path(__file__).resolve().parents[1]


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def start_server(data_dir, port):
    env = {**os.environ, "VGOH_DATA_DIR": str(data_dir), "VGOH_MASTER_KEY": "k", "VGOH_MOCK_FAST": "1", "VGOH_RUN_WORKER": "1", "PORT": str(port),
           "VGOH_TICK_SECONDS": "0.3"}
    env.pop("DATABASE_URL", None)
    p = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(port), "--log-level", "warning"], cwd=BACKEND, env=env,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(100):
        try:
            if httpx.get(f"http://127.0.0.1:{port}/api/health").status_code == 200:
                return p
        except Exception:  # noqa: BLE001
            time.sleep(0.2)
    p.kill()
    raise RuntimeError("server did not start")


def test_kill9_mid_generation_then_resume(tmp_path):
    data = tmp_path / "data"
    port = free_port()
    srv = start_server(data, port)
    cl = httpx.Client(base_url=f"http://127.0.0.1:{port}", headers={"X-VGOH": "1"}, timeout=30)
    try:
        assert cl.post("/api/v1/auth/register", json={"email": "r@example.com", "password": "long-enough-password"}).status_code == 200
        ch = cl.post("/api/v1/characters", json={"name": "Mia"}).json()
        p = cl.post("/api/v1/projects", json={"title": "Recovery", "target_duration": 12, "scene_count": 3, "character_ids": [ch["id"]], "require_storyboard_approval": False}).json()
        pid = p["id"]
        assert cl.post(f"/api/v1/projects/{pid}/plan", json={}).status_code == 200
        r = cl.post("/api/v1/providers/mock_a/connect", json={"label": "A", "config": {"initial_credits": 100, "job_timeout_s": 60}})
        assert r.status_code == 200, r.text
        assert cl.post(f"/api/v1/projects/{pid}/generate").status_code == 200
        # wait until scene 1 has a job in flight or accepted scene 1, then kill -9
        t0 = time.time()
        while time.time() - t0 < 60:
            st = cl.get(f"/api/v1/projects/{pid}").json()
            if st["status"] == "GENERATING" and any(s["status"] == "GENERATING" for s in st["scenes"]) and st["canonical_state"]["accepted_duration_seconds"] >= 4.0:
                break
            time.sleep(0.2)
        else:
            pytest.fail("generation never reached the kill point")
        accepted_before = st["canonical_state"]["accepted_duration_seconds"]
        os.kill(srv.pid, signal.SIGKILL)
        srv.wait()
        cl.close()
        srv = start_server(data, port)
        cl = httpx.Client(base_url=f"http://127.0.0.1:{port}", headers={"X-VGOH": "1"}, timeout=30)
        assert cl.post("/api/v1/auth/login", json={"email": "r@example.com", "password": "long-enough-password"}).status_code == 200
        t0 = time.time()
        while time.time() - t0 < 120:
            st = cl.get(f"/api/v1/projects/{pid}").json()
            if st["status"] in ("COMPLETED", "FAILED"):
                break
            time.sleep(0.5)
        assert st["status"] == "COMPLETED", (st["status"], st["status_detail"])
        assert st["canonical_state"]["accepted_duration_seconds"] == 12.0
        ev = cl.get(f"/api/v1/projects/{pid}/events").json()
        assert any(e["kind"] == "recovered" for e in ev), "restart recovery is recorded"
        rep = cl.get(f"/api/v1/projects/{pid}/report").json()
        assert rep["accepted_scenes_regenerated_unnecessarily"] == 0
        # no duplicate remote jobs: exactly one provider job per scene (the kill must not cause a resubmit)
        jobs = list((data / "mock_remote").glob("*/jobs/*"))
        assert len(jobs) == 3, len(jobs)
        assert st["canonical_state"]["accepted_duration_seconds"] >= accepted_before
    finally:
        cl.close()
        srv.kill()


def test_submit_crash_window_reconciles_by_idempotency_key(app_client, env):
    """Job row committed (SUBMITTING) and the provider got the request, but the provider job id was never stored."""
    cl = app_client()
    signup(cl)
    ch = cl.ok("post", "/api/v1/characters", json={"name": "Mia"})
    p = cl.ok("post", "/api/v1/projects", json={"title": "Window", "target_duration": 4, "scene_count": 1, "character_ids": [ch["id"]], "require_storyboard_approval": False})
    cl.ok("post", f"/api/v1/projects/{p['id']}/plan", json={})
    cr = cl.ok("post", "/api/v1/providers/mock_a/connect", json={"label": "A", "config": {"initial_credits": 50}})
    from app.database.session import new_session
    from app.database.models import GenerationJob, VideoScene, VideoProject, ProviderCredential, QuotaPool
    from app.providers.registry import build_provider, context_for
    from app.workflows import engine as E
    from app.credits import ledger
    db = new_session()
    proj, sc, cred, pool = db.get(VideoProject, p["id"]), db.query(VideoScene).filter_by(project_id=p["id"]).one(), db.query(ProviderCredential).one(), db.query(QuotaPool).one()
    proj.status = "GENERATING"
    db.commit()
    req = E.scene_request(db, proj, sc, 1)
    prov = build_provider("mock_a", context_for(cred))
    req.duration = 4
    from app.continuity.handoff import build_handoff, store_handoff
    pkg = build_handoff(db, proj, sc, reason='initial', previous_provider=None, to_caps={'first_frame': True, 'multi_reference': True}, requested_seconds=4)
    store_handoff(db, proj, sc, pkg, 'mock_a')
    req.handoff = pkg
    remote_id = asyncio.run(prov.submit_generation(req))          # provider received it...
    job = GenerationJob(user_id=proj.user_id, project_id=proj.id, scene_id=sc.id, credential_id=cred.id, provider_id="mock_a", pool_id=pool.id, status="SUBMITTING",
                        idempotency_key=req.idempotency_key, requested_seconds=4, reserved=4.0, request={})
    db.add(job)
    sc.attempt_counter, sc.status = 1, "GENERATING"
    db.commit()
    ledger.reserve(db, pool.id, 4.0, proj.user_id, job.id)
    db.close()
    # "restart": recover() then normal ticks
    E.recover()
    drive(lambda: cl.ok("get", f"/api/v1/projects/{p['id']}")["status"] in ("COMPLETED", "FAILED"))
    st = cl.ok("get", f"/api/v1/projects/{p['id']}")
    assert st["status"] == "COMPLETED"
    assert len(list((env / "data" / "mock_remote").glob("*/jobs/*"))) == 1, "no duplicate remote job"
    ev = cl.ok("get", f"/api/v1/projects/{p['id']}/events")
    assert any(e["kind"] == "recovered" and remote_id in e["message"] for e in ev)
