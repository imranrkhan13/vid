"""REAL two-provider failover end to end (not part of the default suite).

Proves the seamless-failover twist with REAL providers: the free Hugging Face
Space (LTX-Video) runs until its ZeroGPU quota is exhausted mid-story, then the
project continues on a second real provider (Replicate) from the SAME scene,
with the handoff package, and exports one complete 30-40s story.

Requirements (both must genuinely hold; the test skips honestly otherwise):
  * VGOH_REAL_FAILOVER=1                (opt-in: this makes real network calls)
  * REPLICATE_API_TOKEN                 (a real token; free-trial runs or credit)
  * VGOH_FREE_TRIAL_VERIFIED_USER_IDS   (your user id, if using free-trial mode)
  * the Space's anonymous/free ZeroGPU allowance must be partly used up, or the
    scene budget is set small so the first pool empties mid-story.

Run:
  VGOH_REAL_FAILOVER=1 REPLICATE_API_TOKEN=r8_... \
  VGOH_FREE_TRIAL_VERIFIED_USER_IDS=$(id) pytest tests/real_failover_e2e.py -q -s
"""
import subprocess
from pathlib import Path

import pytest

from conftest import drive, signup

pytestmark = [
    pytest.mark.skipif(not __import__("os").environ.get("VGOH_REAL_FAILOVER"),
                       reason="opt-in only: makes real provider calls (VGOH_REAL_FAILOVER=1)"),
    pytest.mark.skipif(not __import__("os").environ.get("REPLICATE_API_TOKEN"),
                       reason="needs a real Replicate API token (REPLICATE_API_TOKEN)"),
]


def test_real_failover_space_to_replicate_30s_story(app_client):
    cl = app_client()
    uid = signup(cl, "failover@example.com")["id"]
    # Provider 1: the free Space, with a small run budget so the pool empties mid-story
    c1 = cl.ok("post", "/api/v1/providers/hf_space_ltx/connect",
               json={"label": "free-space", "config": {}, "balance": 3, "unit": "runs"})
    assert c1["pools"][0]["available"] == 3
    # Provider 2: Replicate (real). Free-trial mode only for operator-verified accounts;
    # otherwise connect as paid with an explicit paid budget (the project must approve it).
    free_ids = {e.strip() for e in __import__("os").environ.get("VGOH_FREE_TRIAL_VERIFIED_USER_IDS", "").split(",") if e.strip()}
    if uid in free_ids:
        c2 = cl.ok("post", "/api/v1/providers/replicate/connect",
                   json={"label": "replicate", "secret": __import__("os").environ["REPLICATE_API_TOKEN"],
                         "config": {"free_trial_only": True}, "balance": 5, "unit": "runs"})
    else:
        c2 = cl.ok("post", "/api/v1/providers/replicate/connect",
                   json={"label": "replicate", "secret": __import__("os").environ["REPLICATE_API_TOKEN"],
                         "balance": 2.0, "unit": "usd", "is_paid": True})
    assert c2["health"]["ok"], c2["health"]

    p = cl.ok("post", "/api/v1/projects", json={
        "title": "Real failover story", "concept": "A friendly cloud explains how rain forms",
        "target_duration": 36, "resolution": "720p", "aspect_ratio": "16:9", "scene_count": 6,
        "require_storyboard_approval": False, "paid_budget": 2.0 if uid not in free_ids else 0})
    pid = p["id"]
    cl.ok("post", f"/api/v1/projects/{pid}/plan", json={})
    cl.ok("post", f"/api/v1/projects/{pid}/generate")

    # Drive to completion. The Space pool (3 runs) empties mid-story; the engine
    # must hand off to Replicate at the SAME scene and keep accepted clips.
    drive(lambda: cl.ok("get", f"/api/v1/projects/{pid}")["status"]
          in ("COMPLETED", "FAILED", "MANUAL_ACTION_REQUIRED", "PAUSED"), timeout=1800)
    st = cl.ok("get", f"/api/v1/projects/{pid}")
    print("status:", st["status"], st["status_detail"])
    assert st["status"] == "COMPLETED", (st["status"], st["status_detail"])

    scenes = cl.ok("get", f"/api/v1/projects/{pid}/scenes")
    prov_by_scene = []
    for s in scenes:
        acc = [c for c in s["candidates"] if c["status"] == "accepted"]
        prov_by_scene.append(acc[0]["provider"] if acc else None)
    print("provider per scene:", prov_by_scene)
    assert "hf_space_ltx" in prov_by_scene, "the free Space must have produced at least one clip"
    assert "replicate" in prov_by_scene, "the story must continue on the second real provider"
    # accepted scenes were not regenerated after the switch
    hs = cl.ok("get", f"/api/v1/projects/{pid}/handoffs")
    assert any(h["from_provider"] == "hf_space_ltx" and h["to_provider"] == "replicate" for h in hs), \
        "a handoff from the Space to Replicate must exist"
    sw = [h for h in hs if h["from_provider"] == "hf_space_ltx" and h["to_provider"] == "replicate"][0]
    assert sw["package"]["continuity"].get("previous_frame"), "handoff carries the last accepted frame"

    report = cl.ok("get", f"/api/v1/projects/{pid}/report")
    assert report["accepted_scenes_regenerated_unnecessarily"] == 0
    exp = [e for e in st["exports"] if e["status"] == "DONE"][0]
    val = exp["report"]["validation"]
    assert val["ok"] and 30 <= val["duration_s"] <= 40, val
    out = Path("/tmp/vgoh_real_failover.mp4")
    out.write_bytes(cl.get(f"/api/v1/exports/{exp['id']}/files/video").content)
    print(subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "stream=codec_name,width,height",
                                   "-of", "csv", str(out)]).decode())
    print("final:", out, out.stat().st_size, "bytes")
