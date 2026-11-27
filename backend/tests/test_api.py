"""API surface tests (section 25) and the served frontend (section 22).

Uses FastAPI's TestClient against an in-memory database. No network, no real
providers: the process registry is seeded with a labelled mock.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("VGOH_DATABASE_URL", "sqlite+pysqlite:///:memory:")
    monkeypatch.setenv("VGOH_KEYS_FILE", str(tmp_path / "keys.json"))
    monkeypatch.delenv("VGOH_S3_BUCKET", raising=False)

    from app.api import deps
    from app.api.app import create_app
    from app.database.base import create_all, get_session
    from app.providers.mock import MockProvider, MockScript
    from app.services.projects import ProjectService

    # Seed the process registry with a labelled mock provider.
    deps._REGISTRY.unregister("mock_api")
    provider = MockProvider("mock_api", script=MockScript(), quality_score=95, consistency_score=95)
    deps._REGISTRY.register(provider)

    app = create_app(configure_database=True)
    create_all()
    # Register the provider row + quota so the orchestrator can select it.
    session = get_session()
    service = ProjectService(session, deps._REGISTRY)
    service.register_provider(provider)
    service.set_quota(
        provider.provider_id, available_credits=500.0, balance_source="estimated",
        model_name="mock_api-model",
    )
    session.commit()
    session.close()

    with TestClient(app) as c:
        yield c


def _create(client) -> str:
    res = client.post(
        "/api/v1/projects",
        json={"title": "API test", "concept": "a robot", "target_duration_seconds": 16.0},
    )
    assert res.status_code == 200
    return res.json()["project_id"]


def test_health(client):
    assert client.get("/health").json()["status"] == "ok"


def test_frontend_index_and_assets_served(client):
    root = client.get("/")
    assert root.status_code == 200
    assert "VGOH" in root.text
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/static/styles.css").status_code == 200


def test_create_plan_generate_flow(client):
    project_id = _create(client)
    scenes = client.post(f"/api/v1/projects/{project_id}/plan", json={"scene_count": 4}).json()
    assert len(scenes) == 4

    report = client.post(
        f"/api/v1/projects/{project_id}/generate", json={"allow_paid_spend": False}
    ).json()
    assert report["final_status"] == "COMPLETED"
    assert report["scenes_accepted"] == 4

    events = client.get(f"/api/v1/projects/{project_id}/events").json()
    assert any(e["event_type"] == "accepted" for e in events)


def test_providers_and_stats(client):
    providers = client.get("/api/v1/providers").json()
    assert any(p["provider_id"] == "mock_api" for p in providers)
    stats = client.get("/api/v1/providers/mock_api/stats").json()
    assert stats["provider_id"] == "mock_api"


def test_cost_plan_endpoint(client):
    project_id = _create(client)
    plan = client.get(f"/api/v1/projects/{project_id}/cost-plan").json()
    assert "ordered" in plan
    assert isinstance(plan["ordered"], list)


def test_style_and_character_endpoints(client):
    project_id = _create(client)
    style = client.post(
        f"/api/v1/projects/{project_id}/style",
        json={"name": "neon noir", "palette": ["#001", "#f0f"], "grade": "cool"},
    ).json()
    assert style["name"] == "neon noir"
    char = client.post(
        f"/api/v1/projects/{project_id}/characters",
        json={"name": "Robo", "reference_uris": ["front.png"]},
    ).json()
    assert char["name"] == "Robo"


def test_credential_endpoint_never_returns_secret(client):
    res = client.post(
        "/api/v1/providers/credentials",
        json={"provider_id": "runway", "secret": "sk-super-secret"},
    )
    # No cipher configured in this environment -> honest 400, secret never echoed.
    if res.status_code == 200:
        assert "sk-super-secret" not in res.text
    else:
        assert res.status_code == 400
        assert "sk-super-secret" not in res.text


def test_key_store_status_lists_providers(client):
    meta = client.get("/api/v1/keys").json()
    assert "providers" in meta
    ids = {p["provider_id"] for p in meta["providers"]}
    # Free paths are present and need no key.
    assert "manual_assisted" in ids
    free = [p for p in meta["providers"] if p["provider_id"] == "manual_assisted"][0]
    assert free["free"] is True and free["requires_key"] is False
    # Real providers are listed and require a key.
    runway = [p for p in meta["providers"] if p["provider_id"] == "runway"][0]
    assert runway["requires_key"] is True and runway["has_key"] is False


def test_unified_key_entry_round_trip_never_echoes_secret(client):
    res = client.put(
        "/api/v1/keys/bulk",
        json={"keys": {"runway": "sk-secret-1", "fal": "sk-secret-2", "luma": ""}},
    )
    assert res.status_code == 200
    assert set(res.json()["stored"]) == {"runway", "fal"}
    assert "sk-secret" not in res.text  # never echoed back

    meta = client.get("/api/v1/keys").json()
    assert meta["configured_count"] == 2
    runway = [p for p in meta["providers"] if p["provider_id"] == "runway"][0]
    assert runway["has_key"] is True
    assert "sk-secret" not in client.get("/api/v1/keys").text

    removed = client.delete("/api/v1/keys/fal").json()
    assert removed["removed"] is True
    assert client.get("/api/v1/keys").json()["configured_count"] == 1


def test_single_key_endpoint(client):
    res = client.put("/api/v1/keys", json={"provider_id": "replicate", "api_key": "r8-xyz"})
    assert res.status_code == 200
    assert res.json()["configured"] is True
    assert "r8-xyz" not in res.text
