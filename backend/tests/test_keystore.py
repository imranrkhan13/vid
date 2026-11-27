"""Unified key store + catalog-driven adapters (one place for every key)."""

from __future__ import annotations

import asyncio
import json
import stat

import httpx
import pytest

from app.providers.real import (
    GenericCatalogProvider,
    PROVIDER_REGISTRY,
    build_default_registry,
)
from app.schemas.provider import GenerationMode, JobState, ProviderRequest
from app.security.credentials import InsecureTestCipher
from app.security.keystore import KeyStore


def _request(**kw) -> ProviderRequest:
    base = dict(
        request_id="r1", project_id="p1", scene_id="s1",
        mode=GenerationMode.TEXT_TO_VIDEO, prompt="hello",
        duration_seconds=5.0, aspect_ratio="9:16",
    )
    base.update(kw)
    return ProviderRequest(**base)


def _run(coro):
    return asyncio.run(coro)


# --- key store -------------------------------------------------------------

def test_keystore_round_trip_plaintext(tmp_path):
    store = KeyStore(tmp_path / "keys.json", cipher=None)
    assert store.encrypted is False
    store.set("runway", "sk-1")
    assert store.get("runway") == "sk-1"
    assert store.configured() == ["runway"]
    # File is written and restricted to the owner.
    mode = stat.S_IMODE((tmp_path / "keys.json").stat().st_mode)
    assert mode == 0o600


def test_keystore_encrypts_when_cipher_present(tmp_path):
    store = KeyStore(tmp_path / "keys.json", cipher=InsecureTestCipher())
    assert store.encrypted is True
    store.set("fal", "sk-secret")
    raw = json.loads((tmp_path / "keys.json").read_text())
    # On-disk value is not the plaintext.
    assert raw["keys"]["fal"] != "sk-secret"
    assert raw["_meta"]["encrypted"] is True
    assert store.get("fal") == "sk-secret"


def test_keystore_set_many_and_delete(tmp_path):
    store = KeyStore(tmp_path / "keys.json", cipher=None)
    stored = store.set_many({"runway": "a", "fal": "b", "": "ignored"})
    assert set(stored) == {"runway", "fal"}
    assert store.configured() == ["fal", "runway"]
    assert store.delete("fal") is True
    assert store.configured() == ["runway"]


def test_keystore_rejects_empty_key(tmp_path):
    store = KeyStore(tmp_path / "keys.json", cipher=None)
    from app.security.credentials import CredentialError

    with pytest.raises(CredentialError):
        store.set("runway", "")


def test_keystore_resolve_prefers_env(tmp_path, monkeypatch):
    store = KeyStore(tmp_path / "keys.json", cipher=None)
    store.set("runway", "from-store")
    monkeypatch.setenv("RUNWAY_API_KEY", "from-env")
    assert store.resolve("runway", "RUNWAY_API_KEY") == "from-env"
    monkeypatch.delenv("RUNWAY_API_KEY")
    assert store.resolve("runway", "RUNWAY_API_KEY") == "from-store"


# --- generic catalog adapters ----------------------------------------------

def test_registry_table_covers_real_api_providers():
    # The free/API providers the user wants key entry for are all present.
    for pid in ("runway", "replicate", "fal", "luma", "kling", "minimax_hailuo",
                "huggingface_inference", "google_flow"):
        assert pid in PROVIDER_REGISTRY


def test_generic_adapter_capabilities_from_catalog():
    provider = GenericCatalogProvider("luma")
    caps = _run(provider.get_capabilities())
    assert caps.max_duration_seconds == 9.0  # from the catalog
    assert caps.text_to_video is True
    _run(provider.aclose())


def test_generic_adapter_without_key_is_unhealthy():
    provider = GenericCatalogProvider("luma", api_key=None)
    assert provider.has_credentials() is False
    assert _run(provider.check_health()) is False
    _run(provider.aclose())


def test_generic_adapter_with_key_and_endpoints_is_healthy():
    provider = GenericCatalogProvider("luma", api_key="k")
    assert _run(provider.check_health()) is True
    _run(provider.aclose())


def test_undocumented_provider_stays_inert_even_with_key():
    # Pika has no confirmed endpoints: a stored key does not make it usable.
    provider = GenericCatalogProvider("pika", api_key="k")
    assert provider.has_endpoints() is False
    assert _run(provider.check_health()) is False
    _run(provider.aclose())


def test_generic_adapter_submit_and_status_mapping():
    calls = {}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["url"] = str(request.url)
        calls["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json={"id": "gen-1", "status": "queued"})

    provider = GenericCatalogProvider("luma", api_key="secret",
                                      transport=httpx.MockTransport(handler))
    job_id = _run(provider.submit_generation(_request()))
    assert job_id == "gen-1"
    assert calls["auth"] == "Bearer secret"
    _run(provider.aclose())


def test_generic_status_extracts_output_url():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"id": "g", "status": "completed",
                                         "output": {"video": "https://cdn/v.mp4"}})

    provider = GenericCatalogProvider("luma", api_key="k",
                                      transport=httpx.MockTransport(handler))
    status = _run(provider.get_job_status("g"))
    assert status.state == JobState.SUCCEEDED
    assert status.message == "https://cdn/v.mp4"
    _run(provider.aclose())


def test_build_default_registry_has_mock_and_free_paths():
    registry = build_default_registry()
    ids = set(registry.ids())
    assert "mock_a" in ids          # labelled mock always present
    assert "manual_assisted" in ids  # free manual-assisted path
    assert "runway" in ids          # real adapter registered (inert until keyed)
    assert "luma" in ids
