"""Free real text-to-video through a public Hugging Face ZeroGPU Space (Lightricks/ltx-video-distilled, LTX-Video 0.9.8 13B distilled).

Verified 2026-11-27 from this project's own code path: anonymous requests (no key, no account, no money) returned real
AI-generated MP4s (2s and 4s clips). Facts and limits, none of them hard-coded as guarantees:
- It is a shared community demo Space, not a contracted API: no SLA, it can sleep, change or disappear.
- ZeroGPU gives free GPU time per user with a daily quota; anonymous callers get the smallest allowance. An optional
  Hugging Face token (free account) is sent ONLY to the Space host and counts the usage against that account's own quota.
  Quota exhaustion is detected from the Space's error text and mapped to CREDITS_EXHAUSTED so the engine hands off.
- The host is fixed in code (no user-supplied URL), and results are downloaded only from that same host.
- Output is small (about 704x384 at <=8s). The model is a weights-available open model; check its license before commercial use."""
from __future__ import annotations
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx

from app.core.config import settings
from .base import CostEstimate, GenerationRequest, JobStatus, ProviderError, QuotaInfo, VideoProvider

HOST = "https://lightricks-ltx-video-distilled.hf.space"
JOIN = "/gradio_api/queue/join"
DATA = "/gradio_api/queue/data"
_RESULTS: dict[str, dict] = {}      # session id -> parsed completion (a Gradio event stream is consumed as it is read)


def _is_result_url(url: str) -> bool:
    from urllib.parse import urlparse
    u = urlparse(url)
    return u.scheme == "https" and u.hostname == "lightricks-ltx-video-distilled.hf.space" and u.port in (None, 443) and not u.username \
        and u.path.startswith("/gradio_api/file=/tmp/gradio/") and ".." not in u.path


def _dims(w: int, h: int) -> tuple[int, int]:
    """(height, width) multiples of 32; long side 704, matching the request's orientation."""
    return (704, 384) if h >= w else (384, 704)


class HFSpaceLTXProvider(VideoProvider):
    category = "native_api"

    def _client(self) -> httpx.AsyncClient:
        h = {"Authorization": f"Bearer {self.ctx.secret}"} if self.ctx.secret else {}
        return httpx.AsyncClient(base_url=HOST, headers=h, timeout=httpx.Timeout(30, read=60), follow_redirects=False)

    @staticmethod
    def _norm_http(r: httpx.Response):
        c = r.status_code
        if c in (401, 403):
            raise ProviderError("AUTHENTICATION_FAILED", f"HTTP {c} from the Space (check the optional Hugging Face token)")
        if c == 429:
            raise ProviderError("RATE_LIMITED", "HTTP 429", retry_after=float(r.headers.get("retry-after", 60)))
        if c >= 500:
            raise ProviderError("PROVIDER_UNAVAILABLE", f"HTTP {c}")
        if c >= 400:
            raise ProviderError("UNSUPPORTED_REQUEST", f"HTTP {c}")

    @staticmethod
    def _error_from_text(msg: str) -> ProviderError:
        m = (msg or "").lower()
        if "quota" in m or "zerogpu" in m or "exceeded" in m:
            import re
            t = re.search(r"try again in (\d+):(\d+):(\d+)", m)
            wait = int(t.group(1)) * 3600 + int(t.group(2)) * 60 + int(t.group(3)) if t else 0
            wait = max(wait, 900)      # ZeroGPU recovers on a rolling window and may report 0s; do not hammer the Space
            return ProviderError("CREDITS_EXHAUSTED", "free ZeroGPU quota used up for this caller (anonymous, or the connected free Hugging Face account)" + (f"; the Space says retry in {wait}s" if wait else ""), retry_after=float(wait), reset_at=(datetime.now(timezone.utc) + timedelta(seconds=wait)).isoformat())
        if "queue" in m and "full" in m or "too many" in m or "busy" in m:
            return ProviderError("PROVIDER_UNAVAILABLE", "the Space queue is full")
        if "nsfw" in m or "safety" in m or "inappropriate" in m:
            return ProviderError("CONTENT_POLICY_REJECTION", "prompt rejected by the Space")
        return ProviderError("GENERATION_FAILED", (msg or "generation failed")[:200])

    async def get_capabilities(self) -> dict[str, Any]:
        return {"label": "Hugging Face Space / LTX-Video distilled (free, shared)", "modes": ["text_to_video", "image_to_video"], "min_duration": 1, "max_duration": 8,
                "aspect_ratios": ["9:16", "16:9"], "max_width": 704, "max_height": 704, "first_frame": True, "multi_reference": False,
                "character_reference": False, "audio": False, "watermark": None, "commercial_ok": None,
                "cost_per_second": 0.0, "unit": "runs", "paid": False, "est_latency_s": 30, "speed_score": 0.7, "concurrency": 1,
                "source": "verified live 2026-11-27 (anonymous calls returned real clips); community Space, no SLA; the free GPU allowance is not readable"}

    async def check_health(self):
        try:
            async with self._client() as cl:
                r = await cl.get("/config")
                if r.status_code != 200:
                    return {"ok": False, "detail": f"Space returned HTTP {r.status_code} (it may be sleeping or gone)"}
                names = {c.get("props", {}).get("label") for c in r.json().get("components", [])}
                if "Video Duration (seconds)" not in names:
                    return {"ok": False, "detail": "the Space's interface changed; adapter needs an update"}
                return {"ok": True, "detail": "Space is up (free shared GPU, quota not readable)"}
        except httpx.HTTPError as e:
            return {"ok": False, "detail": f"{type(e).__name__}"}

    async def get_quota(self) -> QuotaInfo:
        return QuotaInfo(None, "runs", "unknown", detail="ZeroGPU does not expose the remaining allowance; exhaustion is detected when a call is refused")

    async def estimate_cost(self, request: GenerationRequest) -> CostEstimate:
        return CostEstimate(1.0, "runs", False, "medium", "1 run per clip against the run budget you enter; no money is charged. The real free GPU allowance (ZeroGPU) is small, unreadable, and may allow only a few clips per day")

    async def submit_generation(self, request: GenerationRequest) -> str:
        import uuid
        h, w = _dims(request.width, request.height)
        dur = round(min(8.0, max(1.0, request.duration)), 1)
        neg = (request.negative_prompt or "worst quality, inconsistent motion, blurry, jittery, distorted")[:500]
        session = uuid.uuid4().hex[:16]
        frame = Path(request.previous_frame) if request.previous_frame else None
        try:
            async with self._client() as cl:
                cfg = await cl.get("/config")
                self._norm_http(cfg)
                api = "image_to_video" if frame and frame.is_file() else "text_to_video"
                fn = [d["id"] for d in cfg.json().get("dependencies", []) if d.get("api_name") == api]
                if not fn:
                    raise ProviderError("UNSUPPORTED_REQUEST", f"the Space's interface changed (no {api} endpoint)")
                if api == "image_to_video":
                    # continuity: the previous accepted frame becomes this clip's first frame
                    up = await cl.post("/gradio_api/upload", files={"files": (frame.name, frame.read_bytes(), "image/png")})
                    self._norm_http(up)
                    img = {"path": up.json()[0], "meta": {"_type": "gradio.FileData"}}
                    data = [request.prompt[:1500], neg, img, None, h, w, "image-to-video", dur, 9, 42, True, 1, True]
                else:
                    data = [request.prompt[:1500], neg, None, None, h, w, "text-to-video", dur, 9, 42, True, 1, True]
                r = await cl.post(JOIN, json={"data": data, "fn_index": fn[0], "session_hash": session})
                self._norm_http(r)
                return session
        except httpx.TimeoutException:
            raise ProviderError("AMBIGUOUS_JOB_STATE", "submit timed out; job may exist")
        except (httpx.HTTPError, KeyError, ValueError) as e:
            raise ProviderError("PROVIDER_UNAVAILABLE", type(e).__name__)

    async def get_job_status(self, job_id: str) -> JobStatus:
        if job_id in _RESULTS:
            res = _RESULTS[job_id]
            return JobStatus(state="succeeded") if res.get("ok") else JobStatus(state="failed", error_code=res["code"], detail=res["detail"])
        if not job_id.isalnum():
            raise ProviderError("UNSUPPORTED_REQUEST", "bad job id")
        done, state = None, "queued"
        try:
            async with self._client() as cl:
                async with cl.stream("GET", DATA, params={"session_hash": job_id}, timeout=httpx.Timeout(30, read=25)) as r:
                    self._norm_http(r)
                    async for line in r.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        try:
                            m = json.loads(line[5:])
                        except ValueError:
                            continue
                        if m.get("msg") == "process_starts":
                            state = "running"
                        elif m.get("msg") == "process_completed":
                            done = m
                            break
        except httpx.ReadTimeout:
            return JobStatus(state=state)
        except httpx.HTTPError as e:
            raise ProviderError("PROVIDER_UNAVAILABLE", type(e).__name__)
        if done is None:
            return JobStatus(state=state)
        out = done.get("output") or {}
        if done.get("success"):
            try:
                url = out["data"][0]["video"]["url"]
            except (KeyError, IndexError, TypeError):
                _RESULTS[job_id] = {"ok": False, "code": "GENERATION_FAILED", "detail": "unreadable result"}
                return JobStatus(state="failed", error_code="GENERATION_FAILED", detail="unreadable result")
            _RESULTS[job_id] = {"ok": True, "url": url}
            if len(_RESULTS) > 500:
                _RESULTS.pop(next(iter(_RESULTS)))
            return JobStatus(state="succeeded")
        e = self._error_from_text(str(out.get("error") or done.get("title") or ""))
        if e.code == "CREDITS_EXHAUSTED":      # raise so the engine marks the pool exhausted and hands off
            raise e
        _RESULTS[job_id] = {"ok": False, "code": e.code, "detail": e.message}
        return JobStatus(state="failed", error_code=e.code, detail=e.message)

    async def download_result(self, job_id: str, dest: Path) -> Path:
        res = _RESULTS.get(job_id)
        if not res or not res.get("ok") or not _is_result_url(res["url"]):
            raise ProviderError("DOWNLOAD_FAILED", "no downloadable output from the Space")
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            async with self._client() as cl:
                async with cl.stream("GET", res["url"][len(HOST):], timeout=httpx.Timeout(30, read=120)) as r:
                    if r.status_code >= 400:
                        raise ProviderError("DOWNLOAD_FAILED", f"HTTP {r.status_code}")
                    total = 0
                    with open(dest, "wb") as f:
                        async for chunk in r.aiter_bytes():
                            total += len(chunk)
                            if total > settings.max_upload_bytes:
                                raise ProviderError("DOWNLOAD_FAILED", "result too large")
                            f.write(chunk)
        except httpx.HTTPError as e:
            raise ProviderError("DOWNLOAD_FAILED", type(e).__name__)
        return dest

    async def cancel_job(self, job_id: str) -> None:
        return None
