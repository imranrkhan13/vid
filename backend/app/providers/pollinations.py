"""Pollinations native adapter (https://gen.pollinations.ai), written from the official OpenAPI document
(https://gen.pollinations.ai/api/docs/open-api/generate-schema, read 2026-11-27).

Verification status: capabilities come from the public /video/models listing (live-read). Submit, poll and
download follow the documented async job API (Prefer: respond-async, GET /v1/jobs/{id}, GET /v1/jobs/{id}/result).
NOT live-verified with a funded key. Every video model in the listing was marked paid_only on 2026-11-27, so
the credit pool is treated as paid and is blocked unless the user approved paid spend for the project."""
from __future__ import annotations
import re
import urllib.parse
from pathlib import Path
from typing import Any

import httpx

from app.core.config import settings
from .base import CostEstimate, GenerationRequest, JobStatus, ProviderError, QuotaInfo, VideoProvider

BASE = "https://gen.pollinations.ai"
DEFAULT_MODEL = "kwaivgi/kling-v4.0-flash"


class PollinationsProvider(VideoProvider):
    category = "native_api"

    @property
    def model(self) -> str:
        return self.ctx.config.get("model") or DEFAULT_MODEL

    def _client(self) -> httpx.AsyncClient:
        h = {"Authorization": f"Bearer {self.ctx.secret}"} if self.ctx.secret else {}
        return httpx.AsyncClient(base_url=BASE, headers=h, timeout=60, follow_redirects=False)

    @staticmethod
    def _norm(r: httpx.Response):
        c = r.status_code
        if c == 401:
            raise ProviderError("AUTHENTICATION_FAILED", "invalid or missing Pollinations key")
        if c == 402:
            raise ProviderError("CREDITS_EXHAUSTED", "balance too low or key budget exhausted")
        if c == 403:
            raise ProviderError("AUTHENTICATION_FAILED", "key lacks permission for this resource")
        if c == 429:
            raise ProviderError("RATE_LIMITED", "HTTP 429", retry_after=float(r.headers.get("retry-after", 30)))
        if c >= 500:
            raise ProviderError("PROVIDER_UNAVAILABLE", f"HTTP {c}")
        if c >= 400:
            raise ProviderError("UNSUPPORTED_REQUEST", f"HTTP {c}: {r.text[:200]}")

    async def _model_info(self) -> dict[str, Any]:
        async with httpx.AsyncClient(base_url=BASE, timeout=30) as cl:
            r = await cl.get("/video/models")
            self._norm(r)
            for m in r.json():
                if m.get("name") == self.model or self.model in (m.get("aliases") or []):
                    return m
        raise ProviderError("MODEL_UNAVAILABLE", f"model {self.model} is not in the provider's current video model list")

    async def get_capabilities(self) -> dict[str, Any]:
        m = await self._model_info()
        price = float((m.get("pricing") or {}).get("completionVideoSeconds") or 0) or None
        res = m.get("resolutions") or ["720p"]
        h = {"480p": 854, "720p": 1280, "768p": 1366, "1080p": 1920, "2k": 2560}.get(res[-1], 1280)
        return {"label": f"Pollinations / {m.get('title') or m['name']}", "modes": ["text_to_video", "image_to_video"] if "start_frame" in (m.get("video_capabilities") or []) else ["text_to_video"],
                "min_duration": m.get("min_duration"), "max_duration": m.get("max_duration"), "aspect_ratios": ["9:16", "16:9", "1:1"],
                "max_width": h, "max_height": h, "first_frame": "start_frame" in (m.get("video_capabilities") or []), "multi_reference": False,
                "character_reference": False, "audio": "audio_output" in (m.get("video_capabilities") or []), "watermark": None, "commercial_ok": None,
                "cost_per_second": price, "unit": "pollen", "paid": True, "est_latency_s": 120, "speed_score": 0.5, "concurrency": 1,
                "source": "gen.pollinations.ai/video/models (live read); paid_only flag: %s; health: %s" % (m.get("paid_only"), (m.get("health") or {}).get("status"))}

    async def check_health(self):
        try:
            m = await self._model_info()
            return {"ok": (m.get("health") or {}).get("status") != "down", "detail": f"model health: {(m.get('health') or {}).get('status')}"}
        except ProviderError as e:
            return {"ok": False, "detail": e.message}
        except httpx.HTTPError as e:
            return {"ok": False, "detail": str(e)[:200]}

    async def get_quota(self) -> QuotaInfo:
        try:
            async with self._client() as cl:
                r = await cl.get("/account/balance")
                self._norm(r)
                j = r.json()
        except httpx.HTTPError as e:
            raise ProviderError("PROVIDER_UNAVAILABLE", str(e)[:200])
        bal = j.get("balance")
        if bal is None and isinstance(j.get("accountBalance"), dict):
            bal = j["accountBalance"].get("total")
        return QuotaInfo(float(bal) if bal is not None else None, "pollen", "verified_api" if bal is not None else "unknown")

    async def estimate_cost(self, request: GenerationRequest) -> CostEstimate:
        caps = await self.get_capabilities()
        if not caps.get("cost_per_second"):
            return CostEstimate(float("inf"), "pollen", True, "low", "price not published for this model; unknown is not free")
        return CostEstimate(round(caps["cost_per_second"] * request.duration, 4), "pollen", True, "medium", "published per-second price from /video/models")

    async def submit_generation(self, request: GenerationRequest) -> str:
        m = await self._model_info()
        res = (m.get("resolutions") or ["720p"])[0]
        q = {"model": m["name"], "duration": int(round(request.duration)), "aspectRatio": request.aspect_ratio, "resolution": res, "audio": "false"}
        prompt = request.prompt[:3000]
        try:
            async with self._client() as cl:
                r = await cl.get("/video/" + urllib.parse.quote(prompt, safe=""), params=q, headers={"Prefer": "respond-async", "Idempotency-Key": request.idempotency_key})
                if r.status_code not in (200, 202):
                    self._norm(r)
                loc = r.headers.get("location", "")
                mt = re.search(r"([0-9a-f]{64})", loc) or (re.search(r"([0-9a-f]{64})", r.text) if r.headers.get("content-type", "").startswith("application/json") else None)
                if not mt:
                    raise ProviderError("AMBIGUOUS_JOB_STATE", "provider returned no job id; the job may exist")
                return mt.group(1)
        except httpx.TimeoutException:
            raise ProviderError("AMBIGUOUS_JOB_STATE", "submit timed out; job may exist")
        except httpx.HTTPError as e:
            raise ProviderError("PROVIDER_UNAVAILABLE", str(e)[:200])

    async def get_job_status(self, job_id: str) -> JobStatus:
        try:
            async with self._client() as cl:
                r = await cl.get(f"/v1/jobs/{job_id}")
                if r.status_code == 404:
                    return JobStatus(state="unknown", detail="job not found")
                self._norm(r)
                j = r.json()
        except httpx.HTTPError as e:
            raise ProviderError("PROVIDER_UNAVAILABLE", str(e)[:200])
        st = str(j.get("status") or j.get("state") or "").lower()
        if st in ("succeeded", "success", "completed", "done"):
            return JobStatus(state="succeeded")
        if st in ("failed", "error", "cancelled"):
            return JobStatus(state="failed", error_code="GENERATION_FAILED", detail=str(j.get("error") or j.get("message") or "")[:300])
        if st in ("pending", "queued"):
            return JobStatus(state="queued")
        return JobStatus(state="running" if st else "unknown", detail=st or "unrecognized status payload")

    async def download_result(self, job_id: str, dest: Path) -> Path:
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            async with self._client() as cl:
                async with cl.stream("GET", f"/v1/jobs/{job_id}/result") as r:
                    self._norm(r)
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
