"""README section 31 acceptance test, run against the deterministic MOCK providers.
Mock results prove orchestration, not video model quality."""
import json
from pathlib import Path

import pytest

from conftest import drive, make_wav, signup


def project_status(cl, pid):
    return cl.ok("get", f"/api/v1/projects/{pid}")


def test_32s_failover_handoff_and_compose(app_client, env):
    cl = app_client()
    signup(cl)
    c1 = cl.ok("post", "/api/v1/characters", json={"name": "Mia", "appearance": "curly hair, red hoodie", "clothing": "red hoodie"})
    c2 = cl.ok("post", "/api/v1/characters", json={"name": "Leo", "appearance": "short hair, blue jacket", "clothing": "blue jacket"})
    narr = " ".join(f"Scene {i + 1} narration sentence." for i in range(8))
    p = cl.ok("post", "/api/v1/projects", json={
        "title": "Educational Animation", "concept": "Two kids learn how plants grow", "script": "\n".join(f"Mia and Leo do step {i + 1} of growing a plant." for i in range(8)),
        "narration": narr, "target_duration": 32, "aspect_ratio": "9:16", "resolution": "1080p", "fps": 30, "visual_style": "soft colorful 3D educational animation",
        "character_ids": [c1["id"], c2["id"]], "scene_count": 8, "require_storyboard_approval": True})
    pid = p["id"]
    plan = cl.ok("post", f"/api/v1/projects/{pid}/plan", json={})
    assert plan["status"] == "AWAITING_USER_APPROVAL"
    assert len(plan["scenes"]) == 8 and abs(sum(s["duration"] for s in plan["scenes"]) - 32) < 1e-6
    # cannot generate before approval
    assert cl.post(f"/api/v1/projects/{pid}/generate").status_code == 409
    cl.ok("post", f"/api/v1/projects/{pid}/approve")
    # master narration upload (generated tone stands in for real narration; labeled in the test)
    wav = make_wav(env / "narration.wav", 32)
    cl.ok("put", f"/api/v1/projects/{pid}/audio?filename=narration.wav", content=wav.read_bytes())

    # Provider A: simulated remote account with exactly 12 mock-credits = 12 seconds at 1 credit/s
    ca = cl.ok("post", "/api/v1/providers/mock_a/connect", json={"label": "A", "config": {"initial_credits": 12, "job_timeout_s": 8}})
    assert ca["pools"][0]["balance_source"] == "verified_api" and ca["pools"][0]["available"] == 12
    cl.ok("post", f"/api/v1/projects/{pid}/generate")
    drive(lambda: project_status(cl, pid)["status"] in ("WAITING_FOR_CREDITS", "WAITING_FOR_PROVIDER", "FAILED", "MANUAL_ACTION_REQUIRED"))
    st = project_status(cl, pid)
    assert st["status"] == "WAITING_FOR_CREDITS", st["status_detail"]
    cs = st["canonical_state"]
    assert cs["accepted_duration_seconds"] == 12.0 and cs["remaining_duration_seconds"] == 20.0     # steps 1, 2
    assert len(cs["completed_scenes"]) == 3 and cs["last_accepted_provider"] == "mock_a"
    a_scenes = [s for s in st["scenes"] if s["status"] == "ACCEPTED"]
    accepted_ids_before = {s["id"]: s["accepted_artifact_id"] for s in a_scenes}

    # Provider B (hangs once on scene 6 = index 5) and C (no reference conditioning)
    cb = cl.ok("post", "/api/v1/providers/mock_b/connect", json={"label": "B", "config": {"initial_credits": 20, "job_timeout_s": 6, "faults": [{"scene_index": 5, "kind": "timeout", "times": 1}]}})
    cc = cl.ok("post", "/api/v1/providers/mock_c/connect", json={"label": "C", "config": {"initial_credits": 50}})
    drive(lambda: project_status(cl, pid)["status"] in ("COMPLETED", "FAILED", "MANUAL_ACTION_REQUIRED"), timeout=480)
    st = project_status(cl, pid)
    assert st["status"] == "COMPLETED", (st["status"], st["status_detail"])
    # accepted scenes were not regenerated
    for s in st["scenes"]:
        if s["id"] in accepted_ids_before:
            assert s["accepted_artifact_id"] == accepted_ids_before[s["id"]]
    used = [next(c for c in cl.ok("get", f"/api/v1/projects/{pid}/scenes") if c["id"] == s["id"]) for s in st["scenes"]]
    prov_by_scene = [[c["provider"] for c in s["candidates"] if c["status"] == "accepted"][0] for s in used]
    assert prov_by_scene[:3] == ["mock_a"] * 3
    assert "mock_a" not in prov_by_scene[3:], "exhausted provider must not be reused"
    assert len(set(prov_by_scene)) >= 2

    # handoff carried context to the next provider
    hs = cl.ok("get", f"/api/v1/projects/{pid}/handoffs")
    h4 = [h for h in hs if h["scene_id"] == st["scenes"][3]["id"]][0]
    pkg = h4["package"]
    assert pkg["handoff_reason"] == "quota_exhausted" and pkg["previous_provider"] == "mock_a"
    assert pkg["remaining_project_seconds"] == 20.0
    assert [c["name"] for c in pkg["characters"]] == ["Mia", "Leo"] and pkg["style"]["palette"]
    assert pkg["continuity"]["previous_frame"] and Path(pkg["continuity"]["previous_frame"]).exists()
    assert pkg["continuity"]["character_positions"], "ending positions of the last accepted scene are passed on"
    assert "latents" in pkg["transfer_limits"]
    # B timeout was reconciled (status re-read + cancelled) before any retry
    ev = cl.ok("get", f"/api/v1/projects/{pid}/events")
    kinds = [e["kind"] for e in ev]
    print("\n".join(f"{e['kind']}: {e['message'][:160]}" for e in ev if e['kind'] in ('provider_selected','error','reconcile','provider_exhausted')))
    assert "reconcile" in kinds and "provider_exhausted" in kinds
    msgs = " ".join(e["message"] for e in ev)
    assert "GENERATION_TIMEOUT" in msgs

    # export: duration within one frame, 1080x1920, audio present, budget verified
    exp = [e for e in st["exports"] if e["status"] == "DONE"][0]
    val = exp["report"]["validation"]
    assert val["ok"] and val["checks"]["duration_within_one_frame"] and (val["width"], val["height"]) == (1080, 1920)
    assert val["audio"] == "aac"
    report = cl.ok("get", f"/api/v1/projects/{pid}/report")
    assert report["accepted_scenes_regenerated_unnecessarily"] == 0
    assert report["paid_spend"] == 0
    assert len(report["provider_switches"]) >= 1
    r = cl.get(f"/api/v1/exports/{exp['id']}/files/video")
    assert r.status_code == 200 and r.headers["content-type"] == "video/mp4" and len(r.content) > 10000
    (env / "final.mp4").write_bytes(r.content)
    import shutil
    out = Path("/tmp/vgoh_acceptance_final.mp4")
    shutil.copyfile(env / "final.mp4", out)
