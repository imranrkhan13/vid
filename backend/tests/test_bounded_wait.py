"""A project with no eligible provider must not poll forever: it pauses after a bounded number of checks, keeps state, and resumes on request."""
from conftest import drive, signup


def test_wait_for_provider_is_bounded_then_resumes(app_client, env, monkeypatch):
    from app.workflows import engine
    monkeypatch.setattr(engine, "WAIT_SECONDS", 0.0)
    monkeypatch.setenv("VGOH_MAX_PROVIDER_WAITS", "3")
    cl = app_client(); signup(cl)
    p = cl.ok("post", "/api/v1/projects", json={"title": "Wait", "concept": "A cloud explains rain", "target_duration": 8, "resolution": "720p",
                                                 "scene_count": 2, "require_storyboard_approval": False})
    pid = p["id"]
    cl.ok("post", f"/api/v1/projects/{pid}/plan", json={})
    cl.ok("post", f"/api/v1/projects/{pid}/generate")                    # no provider connected
    drive(lambda: cl.ok("get", f"/api/v1/projects/{pid}")["status"] == "PAUSED", timeout=60)
    ev = cl.ok("get", f"/api/v1/projects/{pid}/events") if cl.get(f"/api/v1/projects/{pid}/events").status_code == 200 else None
    if ev is not None:
        evs = ev if isinstance(ev, list) else ev.get("events", [])
        assert sum(1 for e in evs if e.get("kind") == "no_provider") <= 4
        assert any(e.get("kind") == "wait_limit" for e in evs)
    cl.ok("post", "/api/v1/providers/mock_a/connect", json={"label": "A", "config": {"initial_credits": 100}})
    cl.ok("post", f"/api/v1/projects/{pid}/resume")
    drive(lambda: cl.ok("get", f"/api/v1/projects/{pid}")["status"] == "COMPLETED", timeout=100)
