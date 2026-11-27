"""Real adapter contract tests (sections 3, 10).

No network is used: HTTP adapters are exercised through ``httpx.MockTransport``.
The tests assert the guardrails that keep real integrations honest —
capabilities come from the catalog, missing credentials fail closed, and payload
and status mappings are correct.
"""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from app.core.errors import ErrorCode, NormalizedError
from app.providers.real import (
    ManualAssistedProvider,
    ReplicateProvider,
    RunwayProvider,
    map_http_error,
)
from app.schemas.provider import GenerationMode, JobState, ProviderRequest


def _request(**kw) -> ProviderRequest:
    base = dict(
        request_id="r1",
        project_id="p1",
        scene_id="s1",
        mode=GenerationMode.TEXT_TO_VIDEO,
        prompt="a robot walks",
        duration_seconds=5.0,
        aspect_ratio="9:16",
    )
    base.update(kw)
    return ProviderRequest(**base)


def _run(coro):
    return asyncio.run(coro)


# --- catalog-backed facts --------------------------------------------------

def test_runway_capabilities_come_from_catalog():
    provider = RunwayProvider(api_key="x")
    caps = _run(provider.get_capabilities())
    # Values match the catalog, not a hardcoded literal in the adapter.
    assert caps.model_name == "gen4_turbo"
    assert caps.max_duration_seconds == 10.0
    assert caps.text_to_video is True
    assert caps.character_references is None  # unknown stays unknown
    _run(provider.aclose())


def test_replicate_capabilities_unknown_stays_unknown():
    provider = ReplicateProvider(api_key="x")
    caps = _run(provider.get_capabilities())
    assert caps.video_to_video is None
    assert caps.character_references is None
    _run(provider.aclose())


def test_runway_cost_estimate_uses_catalog_rate():
    provider = RunwayProvider(api_key="x")
    est = _run(provider.estimate_cost(_request(duration_seconds=10.0)))
    assert est.credits == pytest.approx(0.5)  # $0.05/s * 10s
    assert est.is_paid is True
    _run(provider.aclose())


def test_unknown_cost_is_marked_paid_not_free():
    """A provider with no known price must never score as free."""
    provider = ReplicateProvider(api_key="x")
    est = _run(provider.estimate_cost(_request()))
    assert est.is_paid is True
    assert est.credits == 0.0
    _run(provider.aclose())


# --- credentials fail closed ----------------------------------------------

def test_missing_credentials_fail_closed(monkeypatch):
    monkeypatch.delenv("RUNWAY_API_KEY", raising=False)
    provider = RunwayProvider(api_key=None)
    assert provider.has_credentials() is False
    assert _run(provider.check_health()) is False
    with pytest.raises(NormalizedError) as exc:
        _run(provider.submit_generation(_request()))
    assert exc.value.code == ErrorCode.AUTHENTICATION_FAILED
    _run(provider.aclose())


def test_construction_performs_no_network(monkeypatch):
    # A transport that fails the test if any request is attempted.
    def boom(request):  # pragma: no cover
        raise AssertionError("network call attempted at construction")

    provider = RunwayProvider(api_key="x", transport=httpx.MockTransport(boom))
    # No exception means no request was sent.
    _run(provider.aclose())


# --- HTTP mapping via MockTransport ---------------------------------------

def test_runway_submit_and_status_mapping():
    calls = {}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["submit"] = (str(request.url), request.headers.get("authorization"))
        return httpx.Response(200, json={"id": "task-123", "status": "pending"})

    provider = RunwayProvider(api_key="secret", transport=httpx.MockTransport(handler))
    job_id = _run(provider.submit_generation(_request()))
    assert job_id == "task-123"
    assert calls["submit"][1] == "Bearer secret"
    _run(provider.aclose())


def test_runway_status_succeeded_carries_output_url():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"id": "task-123", "status": "succeeded", "output": ["https://cdn/x.mp4"]},
        )

    provider = RunwayProvider(api_key="secret", transport=httpx.MockTransport(handler))
    status = _run(provider.get_job_status("task-123"))
    assert status.state == JobState.SUCCEEDED
    assert status.message == "https://cdn/x.mp4"
    _run(provider.aclose())


def test_runway_status_failed_maps_to_generation_failed():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"id": "t", "status": "failed", "failure": "nsfw"})

    provider = RunwayProvider(api_key="secret", transport=httpx.MockTransport(handler))
    status = _run(provider.get_job_status("t"))
    assert status.state == JobState.FAILED
    assert status.error_code == ErrorCode.GENERATION_FAILED.value
    _run(provider.aclose())


def test_replicate_submit_payload_includes_prompt():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"id": "pred-1", "status": "starting"})

    provider = ReplicateProvider(api_key="tok", transport=httpx.MockTransport(handler))
    _run(provider.submit_generation(_request(negative_prompt="blurry")))
    assert captured["body"]["input"]["prompt"] == "a robot walks"
    assert captured["body"]["input"]["negative_prompt"] == "blurry"
    _run(provider.aclose())


def test_http_error_mapping():
    assert map_http_error(401, "no", provider="x").code == ErrorCode.AUTHENTICATION_FAILED
    assert map_http_error(402, "pay", provider="x").code == ErrorCode.CREDITS_EXHAUSTED
    assert map_http_error(429, "slow", provider="x").code == ErrorCode.RATE_LIMITED
    assert map_http_error(503, "down", provider="x").code == ErrorCode.PROVIDER_UNAVAILABLE


def test_402_is_switch_error_not_retry():
    err = map_http_error(402, "out of credits", provider="x")
    assert err.requires_switch is True


# --- manual-assisted adapter (fully functional, no network) ----------------

def test_manual_adapter_round_trip(tmp_path):
    provider = ManualAssistedProvider(work_dir=tmp_path)
    job_id = _run(provider.submit_generation(_request(scene_id="s7")))
    # A brief was written for the human.
    brief_path = tmp_path / "requests" / f"{job_id}.json"
    assert brief_path.exists()
    brief = json.loads(brief_path.read_text())
    assert brief["scene_id"] == "s7"

    # Before import the job is genuinely still running.
    assert _run(provider.get_job_status(job_id)).state == JobState.RUNNING
    with pytest.raises(NormalizedError) as exc:
        _run(provider.download_result(job_id))
    assert exc.value.code == ErrorCode.DOWNLOAD_FAILED

    # The human drops the clip in; the job then succeeds and serves the file.
    (tmp_path / "imports").mkdir(parents=True, exist_ok=True)
    (tmp_path / "imports" / f"{job_id}.mp4").write_bytes(b"fake-mp4-bytes")
    status = _run(provider.get_job_status(job_id))
    assert status.state == JobState.SUCCEEDED
    result = _run(provider.download_result(job_id))
    assert result.clip_uri.endswith(f"{job_id}.mp4")
    assert result.duration_seconds == 5.0


def test_manual_adapter_is_not_mock_and_needs_no_credentials():
    provider = ManualAssistedProvider()
    assert provider.is_mock is False
    assert _run(provider.check_health()) is True
