"""One-shot prompt -> video (POST /api/v1/stories) and full-context handoff."""
import pytest

from conftest import drive, signup


def test_one_shot_story_creates_plans_and_starts(app_client, monkeypatch):
    cl = app_client()
    signup(cl)
    # The Space is a real network provider; stub its health/quota so the test is
    # deterministic and offline. The manual fallback needs no stubbing.
    from app.providers import hf_space_ltx as H

    async def fake_health(self):
        return {"ok": True, "detail": "stubbed for test"}

    async def fake_quota(self):
        from app.providers.base import QuotaInfo
        return QuotaInfo(None, "runs", "unknown")

    monkeypatch.setattr(H.HFSpaceLTXProvider, "check_health", fake_health)
    monkeypatch.setattr(H.HFSpaceLTXProvider, "get_quota", fake_quota)

    r = cl.post("/api/v1/stories", json={"prompt": "A friendly cloud explains how rain forms",
                                         "target_duration": 16, "scene_count": 2})
    assert r.status_code < 300, r.text[:400]
    st = r.json()
    assert st["status"] in ("GENERATING", "SELECTING_PROVIDER", "READY"), st["status"]
    assert "hf_space_ltx" in st["providers_connected"], "the free real provider must be auto-connected"
    assert "manual" in st["providers_connected"], "the manual fallback must be auto-connected"
    # the storyboard was planned and auto-approved in the same call
    scenes = cl.ok("get", f"/api/v1/projects/{st['id']}/scenes") if False else cl.ok("get", f"/api/v1/projects/{st['id']}")["scenes"]
    assert len(scenes) == 2
    assert abs(sum(s["duration"] for s in scenes) - 16) < 1e-6


def test_one_shot_story_is_idempotent_for_existing_connections(app_client, monkeypatch):
    cl = app_client()
    signup(cl, "repeat@example.com")
    from app.providers import hf_space_ltx as H

    async def fake_health(self):
        return {"ok": True, "detail": "stubbed"}

    async def fake_quota(self):
        from app.providers.base import QuotaInfo
        return QuotaInfo(None, "runs", "unknown")

    monkeypatch.setattr(H.HFSpaceLTXProvider, "check_health", fake_health)
    monkeypatch.setattr(H.HFSpaceLTXProvider, "get_quota", fake_quota)
    a = cl.ok("post", "/api/v1/stories", json={"prompt": "first story about clouds", "target_duration": 8, "scene_count": 1})
    b = cl.ok("post", "/api/v1/stories", json={"prompt": "second story about rain", "target_duration": 8, "scene_count": 1})
    assert a["id"] != b["id"]
    conns = cl.ok("get", "/api/v1/providers")["connections"]
    auto = [c for c in conns if c["label"] == "auto"]
    assert len(auto) == 2, "second story must reuse the existing connections, not duplicate them"


def test_handoff_carries_the_full_story_so_far(app_client):
    """When the provider changes mid-story, the next provider's handoff package
    contains every accepted scene so far, and the enriched prompt folds that
    story in — not just the previous shot."""
    from app.continuity.handoff import provider_prompt
    pkg = {"scene_index": 3, "continuity": {"previous_scene_id": "s3"},
           "story_so_far": [
               {"scene": 1, "title": "Cloud forms", "description": "A cloud gathers over the hills"},
               {"scene": 2, "title": "Droplets", "description": "Water vapor condenses into droplets"},
               {"scene": 3, "title": "Rain", "description": "Rain falls on the city"},
           ]}
    p = provider_prompt(pkg, "Scene 4: the sun returns", 1400)
    assert "Story so far" in p
    assert "A cloud gathers over the hills" in p
    assert "Water vapor condenses into droplets" in p
    assert "Rain falls on the city" in p
    assert "scene 4" in p.lower()
    assert p.startswith("Scene 4: the sun returns"), "the scene's own prompt keeps priority"


def test_handoff_without_story_is_unchanged():
    from app.continuity.handoff import provider_prompt
    pkg = {"scene_index": 0, "continuity": {}}
    assert provider_prompt(pkg, "A cloud", 1400) == "A cloud"
