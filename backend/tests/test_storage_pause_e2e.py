"""Engine-level storage pause: over-budget storage pauses the project (never silent), nothing is accepted without a durable copy,
and after budget is freed the project resumes and completes. MOCK providers; proves orchestration only."""
import pytest

from conftest import drive, signup


def _project(cl):
    p = cl.ok("post", "/api/v1/projects", json={"title": "Storage", "concept": "A cloud explains rain", "target_duration": 8, "resolution": "720p",
                                                 "scene_count": 2, "require_storyboard_approval": False})
    pid = p["id"]
    cl.ok("post", f"/api/v1/projects/{pid}/plan", json={})
    cl.ok("post", "/api/v1/providers/mock_a/connect", json={"label": "A", "config": {"initial_credits": 100}})
    return pid


def test_pause_before_generation_when_reserve_unavailable(app_client, env, monkeypatch):
    monkeypatch.setenv("VGOH_DB_BLOBS", "1")
    monkeypatch.setenv("VGOH_DB_BLOB_USER_BUDGET", "1000")          # far below the reserve
    monkeypatch.setenv("VGOH_STORAGE_RESERVE", str(5 * 1024 * 1024))
    cl = app_client(); signup(cl)
    pid = _project(cl)
    cl.ok("post", f"/api/v1/projects/{pid}/generate")
    drive(lambda: cl.ok("get", f"/api/v1/projects/{pid}")["status"] == "PAUSED", timeout=60)
    st = cl.ok("get", f"/api/v1/projects/{pid}")
    assert "STORAGE LIMIT" in (st.get("status_reason") or st.get("reason") or str(st))
    assert st["accepted_duration"] == 0                               # nothing generated, nothing accepted
    # another tenant is unaffected by this user's budget being tiny
    monkeypatch.setenv("VGOH_DB_BLOB_USER_BUDGET", str(60 * 1024 * 1024))
    cl.ok("post", f"/api/v1/projects/{pid}/resume")
    drive(lambda: cl.ok("get", f"/api/v1/projects/{pid}")["status"] == "COMPLETED", timeout=240)
    assert cl.ok("get", f"/api/v1/projects/{pid}")["accepted_duration"] == pytest.approx(8.0, abs=0.01)


def test_clip_not_accepted_when_it_cannot_be_mirrored(app_client, env, monkeypatch):
    monkeypatch.setenv("VGOH_DB_BLOBS", "1")
    monkeypatch.setenv("VGOH_STORAGE_RESERVE", "0")                  # let generation start, then fail at mirroring
    monkeypatch.setenv("VGOH_DB_BLOB_USER_BUDGET", "3000")         # too small for a clip's frames + file
    cl = app_client(); signup(cl)
    pid = _project(cl)
    cl.ok("post", f"/api/v1/projects/{pid}/generate")
    try:
        drive(lambda: cl.ok("get", f"/api/v1/projects/{pid}")["status"] in ("PAUSED", "COMPLETED"), timeout=45)
    except AssertionError:
        raise AssertionError("stuck: " + str({k: v for k, v in cl.ok("get", f"/api/v1/projects/{pid}").items() if k in ("status", "accepted_duration", "status_reason")}))
    st = cl.ok("get", f"/api/v1/projects/{pid}")
    assert st["status"] == "PAUSED"
    assert st["accepted_duration"] == 0                               # candidate only, never counted as accepted
    monkeypatch.setenv("VGOH_DB_BLOB_USER_BUDGET", str(60 * 1024 * 1024))
    cl.ok("post", f"/api/v1/projects/{pid}/resume")
    drive(lambda: cl.ok("get", f"/api/v1/projects/{pid}")["status"] == "COMPLETED", timeout=240)
