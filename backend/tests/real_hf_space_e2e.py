"""REAL network end to end (not part of the default suite): connect the free Space provider, generate a 2-scene 8s video, export.
Run: VGOH_DATA_DIR=/tmp/x pytest tests/real_hf_space_e2e.py -q -s"""
import subprocess

import pytest

from conftest import drive, signup


def test_real_free_generation_to_export(app_client, env, monkeypatch):
    from app.providers import hf_space_ltx as H
    o = H.HFSpaceLTXProvider.submit_generation
    async def spy(self, request):
        print('REQ', request.width, request.height, request.duration, repr(request.prompt[:120]), repr(request.negative_prompt[:60]))
        return await o(self, request)
    monkeypatch.setattr(H.HFSpaceLTXProvider, 'submit_generation', spy)
    cl = app_client(); signup(cl)
    c = cl.ok("post", "/api/v1/providers/hf_space_ltx/connect", json={"label": "free-space", "config": {}, "balance": 10, "unit": "runs"})
    print("connect:", c.get("health"))
    pid = cl.ok("post", "/api/v1/projects", json={"title": "Real free", "concept": "A friendly cloud explains how rain forms", "target_duration": 8,
                                                   "resolution": "720p", "scene_count": 2, "require_storyboard_approval": False})["id"]
    cl.ok("post", f"/api/v1/projects/{pid}/plan", json={})
    cl.ok("post", f"/api/v1/projects/{pid}/generate")
    drive(lambda: cl.ok("get", f"/api/v1/projects/{pid}")["status"] in ("COMPLETED", "MANUAL_ACTION_REQUIRED", "FAILED", "WAITING_FOR_CREDITS", "WAITING_FOR_PROVIDER"), timeout=110)
    st = cl.ok("get", f"/api/v1/projects/{pid}")
    print("status:", st["status"], st["accepted_duration"])
    for ev in cl.ok("get", f"/api/v1/projects/{pid}/events")[-12:]:
        print(" ev:", ev.get("kind"), str(ev.get("message"))[:160])
    assert st["status"] == "COMPLETED"
    exp = [e for e in st["exports"] if e["status"] == "DONE"][0]
    out = env / "real.mp4"; out.write_bytes(cl.get(f"/api/v1/exports/{exp['id']}/files/video").content)
    print(subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "stream=codec_name,width,height,duration", "-of", "csv", str(out)]).decode())
    import shutil; shutil.copy(out, "/tmp/real_export.mp4")
