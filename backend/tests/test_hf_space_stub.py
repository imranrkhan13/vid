"""Contract tests for the Hugging Face Space adapter against a stub of the Gradio call/SSE protocol (no network)."""
import asyncio
import json

import httpx
import pytest

from app.providers import hf_space_ltx as H
from app.providers.base import GenerationRequest, ProviderContext, ProviderError

REQ = GenerationRequest("k", "p", "s", 0, "a cloud", "", 4, 4, "9:16", 720, 1280, 24)
GOOD = H.HOST + "/gradio_api/file=/tmp/gradio/abc/out.mp4"


def mk(handler, secret=None):
    p = H.HFSpaceLTXProvider(ProviderContext("u", "c", "hf_space_ltx", secret=secret))
    p._client = lambda: httpx.AsyncClient(base_url=H.HOST, transport=httpx.MockTransport(handler), headers={"Authorization": f"Bearer {secret}"} if secret else {})
    return p


def q(*msgs):
    return httpx.Response(200, text="".join(f"data: {json.dumps(m)}\n\n" for m in msgs))


def done(ok, **out):
    return {"msg": "process_completed", "success": ok, "output": out}


def h_ok(body_ok=True, **out):
    def h(r):
        if r.url.path == "/config":
            return httpx.Response(200, json={"dependencies": [{"id": 4, "api_name": "text_to_video"}], "components": []})
        if r.url.path.endswith("/queue/join"):
            h.joined = json.loads(r.content)
            return httpx.Response(200, json={"event_id": "e"})
        if r.url.path.endswith("/queue/data"):
            return q({"msg": "estimation"}, {"msg": "process_starts"}, done(body_ok, **out))
        if r.url.path.startswith("/gradio_api/file="):
            return httpx.Response(200, content=b"\x00\x00\x00\x18ftypmp42" + b"x" * 100)
        return httpx.Response(404)
    return h


def test_submit_sends_portrait_dims_and_session_id():
    h = h_ok(data=[{"video": {"url": GOOD}}, 1])
    p = mk(h, secret="hf_tok_12345678")
    sid = asyncio.run(p.submit_generation(REQ))
    d = h.joined
    assert d["fn_index"] == 4 and d["session_hash"] == sid and d["data"][4:8] == [704, 384, "text-to-video", 4.0]


def test_complete_then_download_only_from_the_fixed_host(tmp_path):
    p = mk(h_ok(data=[{"video": {"url": GOOD}}, 1]))
    assert asyncio.run(p.get_job_status("sess1")).state == "succeeded"
    out = asyncio.run(p.download_result("sess1", tmp_path / "c.mp4"))
    assert out.read_bytes().startswith(b"\x00\x00\x00\x18ftyp")


def test_foreign_result_url_is_refused(tmp_path):
    evil = "https://evil.example/gradio_api/file=/tmp/gradio/a/b.mp4"
    p = mk(h_ok(data=[{"video": {"url": evil}}, 1]))
    assert asyncio.run(p.get_job_status("sess2")).state == "succeeded"
    with pytest.raises(ProviderError) as e:
        asyncio.run(p.download_result("sess2", tmp_path / "x.mp4"))
    assert e.value.code == "DOWNLOAD_FAILED"
    assert not H._is_result_url("https://lightricks-ltx-video-distilled.hf.space.evil.com/gradio_api/file=/tmp/gradio/a")
    assert not H._is_result_url("https://lightricks-ltx-video-distilled.hf.space/gradio_api/file=/etc/passwd")


def test_quota_error_maps_to_credits_exhausted_for_handoff():
    p = mk(h_ok(False, error="You have exceeded your ZeroGPU quota (120s requested vs. -60s left). Try again in 0:30:00. Authenticate with a Hugging Face token for more quota"))
    with pytest.raises(ProviderError) as e:
        asyncio.run(p.get_job_status("sess3"))
    assert e.value.code == "CREDITS_EXHAUSTED" and e.value.retry_after == 1800.0


def test_other_error_and_http_codes():
    p = mk(h_ok(False, error="CUDA out of memory"))
    s = asyncio.run(p.get_job_status("sess4"))
    assert s.state == "failed" and s.error_code == "GENERATION_FAILED"
    with pytest.raises(ProviderError) as e:
        asyncio.run(mk(lambda r: httpx.Response(429)).submit_generation(REQ))
    assert e.value.code == "RATE_LIMITED"


def test_health_detects_changed_interface():
    ok = {"components": [{"props": {"label": "Video Duration (seconds)"}}]}
    assert asyncio.run(mk(lambda r: httpx.Response(200, json=ok)).check_health())["ok"] is True
    assert asyncio.run(mk(lambda r: httpx.Response(200, json={"components": []})).check_health())["ok"] is False
    assert asyncio.run(mk(lambda r: httpx.Response(503)).check_health())["ok"] is False
