"""Authenticated download: another tenant and anonymous callers get nothing; the owner can fetch and seek (HTTP Range)."""
from conftest import drive, signup


def test_download_isolation_and_range(app_client, env):
    a, b = app_client(), app_client()
    signup(a, "alice@example.com"); signup(b, "bob@example.com")
    anon = app_client()
    a.ok("post", "/api/v1/providers/mock_a/connect", json={"label": "A", "config": {"initial_credits": 100}})
    pid = a.ok("post", "/api/v1/projects", json={"title": "D", "concept": "rain", "target_duration": 4, "scene_count": 1, "resolution": "720p", "require_storyboard_approval": False})["id"]
    a.ok("post", f"/api/v1/projects/{pid}/plan", json={})
    a.ok("post", f"/api/v1/projects/{pid}/generate")
    drive(lambda: a.ok("get", f"/api/v1/projects/{pid}")["status"] == "COMPLETED", timeout=120)
    st = a.ok("get", f"/api/v1/projects/{pid}")
    eid = [e for e in st["exports"] if e["status"] == "DONE"][0]["id"]
    aid = st["scenes"][0]["accepted_artifact_id"]
    assert aid, "accepted artifact id must be in project state"
    urls = [f"/api/v1/exports/{eid}/files/{k}" for k in ("video", "subtitles", "report", "storyboard")]
    urls += [f"/api/v1/media/{aid}", f"/api/v1/media/{aid}?frame=first"]
    for u in urls:
        assert b.get(u).status_code == 404, ("other tenant", u)
        assert anon.get(u).status_code in (401, 403), ("anonymous", u)
    full = a.get(urls[0])
    assert full.status_code == 200 and len(full.content) > 1000
    part = a.get(urls[0], headers={"Range": "bytes=0-99"})
    assert part.status_code == 206 and len(part.content) == 100 and part.headers["content-range"].startswith("bytes 0-99/")
    m = a.get(f"/api/v1/media/{aid}", headers={"Range": "bytes=0-49"})
    assert m.status_code == 206 and len(m.content) == 50
    # path traversal in the file kind is not a way to read other files
    assert a.get(f"/api/v1/exports/{eid}/files/..%2F..%2Fetc%2Fpasswd").status_code == 404
