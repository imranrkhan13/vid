"""Replicate native adapter (https://replicate.com/docs/reference/http), model minimax/video-01 by default.

Verified 2026-11-27 (owner account, web UI): a free account with no billing produced a real 6s clip from
minimax/video-01 (Replicate "Try for Free" collection). The size of the free allowance is NOT published or readable
by API, so quota is reported as unknown. When the allowance ends, Replicate returns HTTP 402 and this adapter maps
it to CREDITS_EXHAUSTED so the engine hands off to the next provider. Per-run list price is $0.50, so the pool is
flagged paid unless the user marks the connection as free-trial-only."""
from __future__ import annotations
from pathlib import Path
from typing import Any

import httpx

from app.core.config import settings
from .base import CostEstimate, GenerationRequest, JobStatus, ProviderError, QuotaInfo, VideoProvider

BASE = "https://api.replicate.com/v1"
DEFAULT_MODEL = "minimax/video-01"


class ReplicateProvider(VideoProvider):
    category = "native_api"

    @property
    def model(self) -> str:
        m = self.ctx.config.get("model") or DEFAULT_MODEL
        if not all(c.isalnum() or c in "-_./" for c in m) or m.count("/") != 1:
            raise ProviderError("UNSUPPORTED_REQUEST", "invalid model slug")
        return m

    def _client(self) -> httpx.AsyncClient:
        h = {"Authorization": f"Bearer {self.ctx.secret}"} if self.ctx.secret else {}
        return httpx.AsyncClient(base_url=BASE, headers=h, timeout=60, follow_redirects=False)

    @staticmethod
    def _norm(r: httpx.Response):
        c = r.status_code
        if c == 401:
            raise ProviderError("AUTHENTICATION_FAILED", "Replicate rejected the API token")
        if c == 402:
            raise ProviderError("CREDITS_EXHAUSTED", "Replicate: payment required (free runs used up or no credit)")
        if c == 403:
            raise ProviderError("AUTHENTICATION_FAILED", "HTTP 403")
        if c == 429:
            raise ProviderError("RATE_LIMITED", "HTTP 429", retry_after=float(r.headers.get("retry-after", 30)))
        if c >= 500:
            raise ProviderError("PROVIDER_UNAVAILABLE", f"HTTP {c}")
        if c >= 400:
            raise ProviderError("UNSUPPORTED_REQUEST", f"HTTP {c}: {r.text[:200]}")

    async def get_capabilities(self) -> dict[str, Any]:
        free = bool(self.ctx.config.get("free_trial_only", False))
        return {"label": f"Replicate / {self.model}", "modes": ["text_to_video", "image_to_video"], "min_duration": 6, "max_duration": 6,
                "durations": [6], "aspect_ratios": ["16:9"], "max_width": 1280, "max_height": 720, "first_frame": False, "multi_reference": False,
                "character_reference": False, "audio": False, "watermark": None, "commercial_ok": None,
                "cost_per_second": (1.0 / 6) if free else 0.5 / 6, "unit": "runs" if free else "usd", "paid": not free, "est_latency_s": 180, "speed_score": 0.3, "concurrency": 1,
                "source": "owner-verified 6s free run on 2026-11-27; list price $0.50/run; free allowance size unknown"}

    async def check_health(self):
        try:
            async with self._client() as cl:
                r = await cl.get("/account")
                self._norm(r)
                return {"ok": True, "detail": "token accepted"}
        except ProviderError as e:
            return {"ok": False, "detail": e.message}
        except httpx.HTTPError as e:
            return {"ok": False, "detail": str(e)[:200]}

    async def get_quota(self) -> QuotaInfo:
        return QuotaInfo(None, "runs" if self.ctx.config.get("free_trial_only") else "usd", "unknown", detail="Replicate does not expose the free-run allowance; exhaustion is detected from HTTP 402")

    async def estimate_cost(self, request: GenerationRequest) -> CostEstimate:
        free = bool(self.ctx.config.get("free_trial_only", False))
        return CostEstimate(1.0 if free else 0.5, "runs" if free else "usd", not free, "medium", "free-trial mode counts 1 run per clip against a run budget the user entered; the real allowance is unknown and 402 stops it. Otherwise list price $0.50")

    async def submit_generation(self, request: GenerationRequest) -> str:
        body = {"input": {"prompt": request.prompt[:2000], "prompt_optimizer": True}}
        try:
            async with self._client() as cl:
                r = await cl.post(f"/models/{self.model}/predictions", json=body, headers={"Cancel-After": "10m"})
                self._norm(r)
                return str(r.json()["id"])
        except httpx.TimeoutException:
            raise ProviderError("AMBIGUOUS_JOB_STATE", "submit timed out; job may exist")
        except httpx.HTTPError as e:
            raise ProviderError("PROVIDER_UNAVAILABLE", str(e)[:200])

    async def _get(self, job_id: str) -> dict:
        async with self._client() as cl:
            r = await cl.get(f"/predictions/{job_id}")
            if r.status_code == 404:
                return {"status": "unknown"}
            self._norm(r)
            return r.json()

    async def get_job_status(self, job_id: str) -> JobStatus:
        try:
            j = await self._get(job_id)
        except httpx.HTTPError as e:
            raise ProviderError("PROVIDER_UNAVAILABLE", str(e)[:200])
        st = j.get("status")
        if st == "succeeded":
            return JobStatus(state="succeeded")
        if st in ("failed", "canceled"):
            err = str(j.get("error") or "")[:300]
            code = "CONTENT_POLICY_REJECTION" if "flagged" in err.lower() or "sensitive" in err.lower() else "GENERATION_FAILED"
            return JobStatus(state="failed", error_code=code, detail=err)
        if st == "starting":
            return JobStatus(state="queued")
        if st == "processing":
            return JobStatus(state="running")
        return JobStatus(state="unknown", detail=str(st))

    async def download_result(self, job_id: str, dest: Path) -> Path:
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            j = await self._get(job_id)
            out = j.get("output")
            url = out[0] if isinstance(out, list) and out else out
            if not isinstance(url, str) or not url.startswith("https://") or not url.split("/")[2].endswith("replicate.delivery"):
                raise ProviderError("DOWNLOAD_FAILED", "no downloadable output URL from the provider")
            async with httpx.AsyncClient(timeout=120, follow_redirects=False) as cl:
                async with cl.stream("GET", url) as r:
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
            raise ProviderError("DOWNLOAD_FAILED", str(e)[:200])
        return dest

    async def cancel_job(self, job_id: str) -> None:
        try:
            async with self._client() as cl:
                await cl.post(f"/predictions/{job_id}/cancel")
        except Exception:  # noqa: BLE001
            pass
