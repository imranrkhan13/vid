"""Manual-assisted route end to end with REAL media files made by ffmpeg (not a provider): the platform asks for clips,
the user imports them, each goes through the normal QA, compose and export produce a playable file.
This proves the import/QA/compose path, not any AI model."""
import subprocess

import pytest

from conftest import drive, signup


def make_clip(path, seconds, w=720, h=1280):
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"testsrc2=size={w}x{h}:rate=30:duration={seconds}", "-pix_fmt", "yuv420p",
                    "-c:v", "libx264", "-preset", "veryfast", str(path)], check=True)
    return path


def test_manual_import_two_scenes_to_export(app_client, env):
    cl = app_client(); signup(cl)
    p = cl.ok("post", "/api/v1/projects", json={"title": "Manual", "concept": "A cloud explains rain", "target_duration": 8, "resolution": "720p",
                                                 "scene_count": 2, "require_storyboard_approval": False})
    pid = p["id"]
    cl.ok("post", f"/api/v1/projects/{pid}/plan", json={})
    cl.ok("post", "/api/v1/providers/manual/connect", json={"label": "me", "config": {}})
    cl.ok("post", f"/api/v1/projects/{pid}/generate")
    for n in range(2):
        drive(lambda: len(cl.ok("get", f"/api/v1/projects/{pid}/manual-tasks")) > 0 or cl.ok("get", f"/api/v1/projects/{pid}")["status"] == "COMPLETED", timeout=60)
        tasks = cl.ok("get", f"/api/v1/projects/{pid}/manual-tasks")
        assert tasks and tasks[0]["prompt"], "manual task must carry the prompt"
        t = tasks[0]
        clip = make_clip(env / f"clip{n}.mp4", t["duration"])
        r = cl.put(f"/api/v1/scenes/{t['scene_id']}/import?filename=clip{n}.mp4", content=clip.read_bytes())
        assert r.status_code < 300, r.text
    drive(lambda: cl.ok("get", f"/api/v1/projects/{pid}")["status"] == "COMPLETED", timeout=100)
    st = cl.ok("get", f"/api/v1/projects/{pid}")
    assert st["accepted_duration"] == pytest.approx(8.0, abs=0.1)
    exp = [e for e in st["exports"] if e["status"] == "DONE"][0]
    r = cl.get(f"/api/v1/exports/{exp['id']}/files/video")
    assert r.status_code == 200 and len(r.content) > 1000
    out = env / "final.mp4"; out.write_bytes(r.content)
    dur = float(subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(out)]).decode().strip())
    assert dur == pytest.approx(8.0, abs=0.2)
    assert all(sc["status"] == "ACCEPTED" for sc in st["scenes"])
    assert exp["report"]["validation"]["ok"]
    assert cl.ok("get", f"/api/v1/projects/{pid}/report")["paid_spend"] == 0
