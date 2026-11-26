"""Multi-user isolation, credential handling and abuse controls (README sections 23, 28)."""
import sqlite3

import pytest

from conftest import signup


def two_users(app_client):
    a, b = app_client(), app_client()
    signup(a, "alice@example.com")
    signup(b, "bob@example.com")
    return a, b


def test_requires_auth_and_csrf_header(app_client):
    cl = app_client()
    assert cl.get("/api/v1/projects").status_code == 401
    from fastapi.testclient import TestClient
    from app.main import app
    raw = TestClient(app)
    assert raw.post("/api/v1/auth/login", json={"email": "x@y.zz", "password": "p"}).status_code == 403  # no X-VGOH


def test_users_cannot_see_or_touch_each_others_data(app_client):
    a, b = two_users(app_client)
    ch = a.ok("post", "/api/v1/characters", json={"name": "Mia"})
    p = a.ok("post", "/api/v1/projects", json={"title": "A's film", "target_duration": 8, "character_ids": [ch["id"]]})
    a.ok("post", f"/api/v1/projects/{p['id']}/plan", json={})
    cr = a.ok("post", "/api/v1/providers/mock_a/connect", json={"label": "mine", "config": {"initial_credits": 10}})
    pid, cid = p["id"], cr["id"]
    sid = a.ok("get", f"/api/v1/projects/{pid}")["scenes"][0]["id"]
    assert a.ok("get", "/api/v1/projects")[0]["id"] == pid
    assert b.ok("get", "/api/v1/projects") == []
    for method, url in [("get", f"/api/v1/projects/{pid}"), ("get", f"/api/v1/projects/{pid}/scenes"), ("get", f"/api/v1/projects/{pid}/events"),
                        ("post", f"/api/v1/projects/{pid}/generate"), ("delete", f"/api/v1/projects/{pid}"), ("get", f"/api/v1/projects/{pid}/handoffs"),
                        ("patch", f"/api/v1/scenes/{sid}"), ("post", f"/api/v1/scenes/{sid}/regenerate"), ("delete", f"/api/v1/credentials/{cid}"),
                        ("post", f"/api/v1/credentials/{cid}/balance"), ("get", f"/api/v1/credentials/{cid}/ledger"), ("patch", f"/api/v1/characters/{ch['id']}"),
                        ("get", f"/api/v1/credentials/{cid}/health")]:
        kw = {"json": {}} if method in ("post", "patch") else {}
        assert getattr(b, method)(url, **kw).status_code == 404, (method, url)
    # B cannot attach A's character to B's project
    assert b.post("/api/v1/projects", json={"title": "x", "target_duration": 8, "character_ids": [ch["id"]]}).status_code == 404
    # B's provider list shows only B's connections
    assert b.ok("get", "/api/v1/providers")["connections"] == []
    assert len(a.ok("get", "/api/v1/providers")["connections"]) == 1


def test_secrets_encrypted_never_returned_and_bound_to_owner(app_client, env):
    a, b = two_users(app_client)
    secret = "sk-test-SUPERSECRET-1234567890"
    cr = a.ok("post", "/api/v1/providers/custom_http/connect", json={"label": "ent", "secret": secret, "balance": 5, "config": {"base_url": "https://example.invalid", "cost_per_second": 1, "paid": False}}) if False else None
    # custom_http verification needs a live endpoint: use the contract server from test_custom_http for the full path; here store via a mock with a secret
    cr = a.ok("post", "/api/v1/providers/mock_a/connect", json={"label": "k", "secret": secret, "config": {"initial_credits": 5}})
    assert secret not in str(cr) and cr["has_secret"] and cr["secret_hint"].endswith("7890")
    assert secret not in str(a.ok("get", "/api/v1/providers")) and secret not in str(a.ok("get", "/api/v1/dashboard"))
    from app.database.session import new_session
    from app.database.models import ProviderCredential
    from app.core.security import decrypt_secret
    db = new_session()
    row = db.get(ProviderCredential, cr["id"])
    assert secret not in row.secret_enc and "SUPERSECRET" not in row.secret_enc
    assert decrypt_secret(row.secret_enc, f"{row.user_id}|{row.provider_id}|{row.id}") == secret
    other = [u for u in [row.user_id]][0]
    with pytest.raises(Exception):   # copying a row to another user (different AAD) cannot be decrypted
        decrypt_secret(row.secret_enc, f"someone-else|{row.provider_id}|{row.id}")
    db.close()


def test_passwords_hashed_login_ratelimit_and_logout(app_client):
    cl = app_client()
    signup(cl, "c@example.com", "a-long-password-1")
    from app.database.session import new_session
    from app.database.models import User
    db = new_session()
    u = db.query(User).filter_by(email="c@example.com").one()
    assert "a-long-password-1" not in u.pw_hash and len(u.pw_hash) == 128
    db.close()
    assert cl.post("/api/v1/auth/register", json={"email": "d@example.com", "password": "short"}).status_code == 422
    assert cl.post("/api/v1/auth/register", json={"email": "c@example.com", "password": "another-long-password"}).status_code == 409
    cl.ok("post", "/api/v1/auth/logout")
    assert cl.get("/api/v1/auth/me").status_code == 401
    codes = [cl.post("/api/v1/auth/login", json={"email": "c@example.com", "password": "wrong-password-x"}).status_code for _ in range(12)]
    assert codes[0] == 401 and codes[-1] == 429


def test_upload_limits_and_path_safety(app_client, env, monkeypatch):
    cl = app_client()
    signup(cl)
    p = cl.ok("post", "/api/v1/projects", json={"title": "t", "target_duration": 8})
    assert cl.put(f"/api/v1/projects/{p['id']}/audio?filename=../../etc/passwd.wav", content=b"not audio").status_code == 422
    assert cl.put(f"/api/v1/projects/{p['id']}/audio?filename=x.exe", content=b"MZ").status_code == 415
    monkeypatch.setenv("VGOH_MAX_UPLOAD_BYTES", "1000")
    assert cl.put(f"/api/v1/projects/{p['id']}/audio?filename=a.wav", content=b"0" * 5000).status_code == 413
    ch = cl.ok("post", "/api/v1/characters", json={"name": "x"})
    assert cl.put(f"/api/v1/characters/{ch['id']}/references?filename=a.png", content=b"this is not an image").status_code == 415


def test_unknown_balance_is_not_unlimited_and_paid_needs_approval(app_client):
    """Router hard filters: unknown balance and paid providers are excluded unless the user explicitly allowed them."""
    import asyncio
    cl = app_client()
    signup(cl)
    p = cl.ok("post", "/api/v1/projects", json={"title": "t", "target_duration": 8, "scene_count": 2})
    cl.ok("post", f"/api/v1/projects/{p['id']}/plan", json={})
    from app.database.session import new_session
    from app.database.models import VideoProject, VideoScene, QuotaPool
    from app.routing.router import select_provider
    from app.workflows.engine import scene_request
    cr = cl.ok("post", "/api/v1/providers/mock_a/connect", json={"label": "A", "config": {"initial_credits": 100}})
    db = new_session()
    pool = db.query(QuotaPool).one()
    pool.available = None
    pool.balance_source = "unknown"
    db.commit()
    proj = db.get(VideoProject, p["id"])
    sc = db.query(VideoScene).filter_by(project_id=proj.id).order_by(VideoScene.idx).first()
    sel = asyncio.run(select_provider(db, proj, sc, scene_request(db, proj, sc, 1)))
    assert sel.chosen is None and "unknown is not unlimited" in sel.rejected[0]["reason"]
    pool.available, pool.balance_source, pool.is_paid = 100, "user_entered", True
    db.commit()
    sel = asyncio.run(select_provider(db, proj, sc, scene_request(db, proj, sc, 1)))
    assert sel.chosen is None and "paid" in sel.rejected[0]["reason"] or "free_only" in sel.rejected[0]["reason"]
    db.close()


def test_atomic_reservation_prevents_overspend(app_client):
    import threading
    cl = app_client()
    signup(cl)
    cr = cl.ok("post", "/api/v1/providers/mock_a/connect", json={"label": "A", "config": {"initial_credits": 10}})
    from app.database.session import new_session
    from app.database.models import QuotaPool, User
    from app.credits import ledger
    db0 = new_session()
    pool = db0.query(QuotaPool).one()
    pid, uid = pool.id, pool.user_id
    db0.close()
    results = []

    def worker():
        db = new_session()
        results.append(ledger.reserve(db, pid, 4.0, uid, "job"))
        db.close()
    ts = [threading.Thread(target=worker) for _ in range(6)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert sum(results) == 2, results   # 10 credits cover exactly two 4-credit reservations
    db = new_session()
    assert db.get(QuotaPool, pid).reserved == 8.0
    db.close()
