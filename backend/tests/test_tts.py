"""Free local TTS (eSpeak NG): real synthesis, real ffmpeg mix, and the narration ends up in the exported MP4's audio stream."""
import subprocess

import pytest

from conftest import drive, signup
from test_manual_import_e2e import make_clip


def test_synthesized_narration_is_in_the_export(app_client, env):
    cl = app_client(); signup(cl)
    pid = cl.ok("post", "/api/v1/projects", json={"title": "Voice", "concept": "A cloud explains rain", "target_duration": 8, "resolution": "720p",
                                                   "scene_count": 2, "require_storyboard_approval": False})["id"]
    cl.ok("post", f"/api/v1/projects/{pid}/plan", json={})
    segs = cl.ok("put", f"/api/v1/projects/{pid}/narration", json={"segments": [
        {"text": "Clouds hold tiny drops of water.", "start_time": 0.2, "end_time": 3.8},
        {"text": "When they grow heavy, the drops fall as rain.", "start_time": 4.2, "end_time": 7.8}]})
    r = cl.ok("post", f"/api/v1/projects/{pid}/narration/synthesize")
    assert r["segments"] == 2 and r["engine"] == "espeak-ng" and 7.0 < r["duration"] <= 8.05
    cl.ok("post", "/api/v1/providers/manual/connect", json={"label": "me", "config": {}})
    cl.ok("post", f"/api/v1/projects/{pid}/generate")
    for n in range(2):
        drive(lambda: len(cl.ok("get", f"/api/v1/projects/{pid}/manual-tasks")) > 0, timeout=60)
        t = cl.ok("get", f"/api/v1/projects/{pid}/manual-tasks")[0]
        clip = make_clip(env / f"c{n}.mp4", t["duration"])
        assert cl.put(f"/api/v1/scenes/{t['scene_id']}/import?filename=c{n}.mp4", content=clip.read_bytes()).status_code < 300
    drive(lambda: cl.ok("get", f"/api/v1/projects/{pid}")["status"] == "COMPLETED", timeout=100)
    exp = [e for e in cl.ok("get", f"/api/v1/projects/{pid}")["exports"] if e["status"] == "DONE"][0]
    out = env / "voiced.mp4"; out.write_bytes(cl.get(f"/api/v1/exports/{exp['id']}/files/video").content)
    streams = subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(out)]).decode().split()
    assert "audio" in streams and "video" in streams
    # the audio is real speech energy, not silence: speech gaps exist but mean volume is above the noise floor
    v = subprocess.run(["ffmpeg", "-i", str(out), "-vn", "-af", "volumedetect", "-f", "null", "-"], capture_output=True, text=True).stderr
    mean = float([l for l in v.splitlines() if "mean_volume" in l][0].split(":")[1].split()[0])
    assert mean > -50, v[-300:]
