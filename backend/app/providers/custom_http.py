"""Custom/enterprise provider: a configurable private HTTP endpoint.

Contract (documented in docs/PROVIDER_CONTRACT.md):
  POST {base}/jobs            JSON GenerationRequest subset -> {"id": "..."}            (Idempotency-Key header sent)
  GET  {base}/jobs/{id}       -> {"state": queued|running|succeeded|failed, "error_code"?, "detail"?, "cost"?}
  GET  {base}/jobs/{id}/result -> video bytes
  POST {base}/jobs/{id}/cancel
  GET  {base}/health, GET {base}/quota -> {"available": number|null, "unit": "credits", "reset_at"?}
Capabilities and cost are taken from the user's connection config, never invented here.
"""
from __future__ import annotations
import ipaddress
import socket
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from app.core.config import settings
from .base import CostEstimate, GenerationRequest, JobStatus, ProviderError, QuotaInfo, VideoProvider


def validate_base_url(url: str) -> str:
    u = urlparse(url)
    if u.scheme not in ("https", "http") or not u.hostname:
        raise ProviderError("UNSUPPORTED_REQUEST", "base_url must be http(s)")
    if u.scheme == "http" and not settings.allow_private_provider_hosts:
        raise ProviderError("UNSUPPORTED_REQUEST", "plain http base_url is not allowed")
    if not settings.allow_private_provider_hosts:
        try:
            for fam, _, _, _, sa in socket.getaddrinfo(u.hostname, u.port or 443):
                ip = ipaddress.ip_address(sa[0])
                if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
                    raise ProviderError("UNSUPPORTED_REQUEST", "base_url resolves to a private address")
        except socket.gaierror:
            raise ProviderError("PROVIDER_UNAVAILABLE", "base_url does not resolve")
    return url.rstrip("/")


class CustomHttpProvider(VideoProvider):
    category = "custom"

    def _client(self) -> httpx.AsyncClient:
        base = validate_base_url(self.ctx.config.get("base_url", ""))
        headers = {}
        if self.ctx.secret:
            headers[self.ctx.config.get("auth_header", "Authorization")] = (self.ctx.config.get("auth_prefix", "Bearer ") + self.ctx.secret)
        from app.core.netguard import pinned_client
        return pinned_client(base_url=base, headers=headers, timeout=30)

    @staticmethod
    def _norm(resp: httpx.Response):
        if resp.status_code in (401, 403):
            raise ProviderError("AUTHENTICATION_FAILED", f"HTTP {resp.status_code}")
        if resp.status_code == 402:
            raise ProviderError("CREDITS_EXHAUSTED", "HTTP 402")
        if resp.status_code == 429:
            raise ProviderError("RATE_LIMITED", "HTTP 429", retry_after=float(resp.headers.get("retry-after", 30)))
        if resp.status_code >= 500:
            raise ProviderError("PROVIDER_UNAVAILABLE", f"HTTP {resp.status_code}")
        if resp.status_code >= 400:
            raise ProviderError("UNSUPPORTED_REQUEST", f"HTTP {resp.status_code}: {resp.text[:200]}")

    async def get_capabilities(self) -> dict[str, Any]:
        c = self.ctx.config
        caps = {"label": c.get("label", "Custom HTTP endpoint"), "modes": c.get("modes", ["text_to_video"]),
                "min_duration": c.get("min_duration"), "max_duration": c.get("max_duration"), "durations": c.get("durations"),
                "aspect_ratios": c.get("aspect_ratios", []), "max_width": c.get("max_width"), "max_height": c.get("max_height"),
                "first_frame": bool(c.get("first_frame", False)), "multi_reference": bool(c.get("multi_reference", False)),
                "character_reference": bool(c.get("character_reference", False)), "audio": bool(c.get("audio", False)),
                "watermark": c.get("watermark"), "commercial_ok": c.get("commercial_ok"),
                "cost_per_second": c.get("cost_per_second"), "unit": c.get("unit", "credits"), "paid": bool(c.get("paid", True)),
                "est_latency_s": c.get("est_latency_s", 120), "speed_score": c.get("speed_score"), "concurrency": c.get("concurrency", 1),
                "source": "user-supplied connection config (unverified by the platform)"}
        return caps

    async def check_health(self):
        try:
            async with self._client() as cl:
                r = await cl.get("/health")
                self._norm(r)
                return {"ok": True, "detail": "health endpoint OK"}
        except ProviderError as e:
            return {"ok": False, "detail": e.message}
        except httpx.HTTPError as e:
            return {"ok": False, "detail": str(e)[:200]}

    async def get_quota(self) -> QuotaInfo:
        try:
            async with self._client() as cl:
                r = await cl.get("/quota")
                if r.status_code == 404:
                    return QuotaInfo(None, self.ctx.config.get("unit", "credits"), "unknown", detail="provider has no quota endpoint")
                self._norm(r)
                j = r.json()
                return QuotaInfo(j.get("available"), j.get("unit", "credits"), "verified_api" if j.get("available") is not None else "unknown", reset_at=j.get("reset_at"))
        except httpx.HTTPError as e:
            raise ProviderError("PROVIDER_UNAVAILABLE", str(e)[:200])

    async def estimate_cost(self, request: GenerationRequest) -> CostEstimate:
        cps = self.ctx.config.get("cost_per_second")
        if cps is None:
            return CostEstimate(float("inf"), self.ctx.config.get("unit", "credits"), True, "low", "cost per second not configured; unknown is not free")
        return CostEstimate(round(cps * request.duration, 4), self.ctx.config.get("unit", "credits"), bool(self.ctx.config.get("paid", True)), "medium", "user-configured cost per second")

    async def submit_generation(self, request: GenerationRequest) -> str:
        body = {"prompt": request.prompt, "negative_prompt": request.negative_prompt, "duration": request.duration,
                "aspect_ratio": request.aspect_ratio, "width": request.width, "height": request.height, "fps": request.fps,
                "continuity": request.handoff.get("continuity", {}), "style": request.handoff.get("style", {})}
        try:
            async with self._client() as cl:
                r = await cl.post("/jobs", json=body, headers={"Idempotency-Key": request.idempotency_key})
                self._norm(r)
                return str(r.json()["id"])
        except httpx.TimeoutException:
            raise ProviderError("AMBIGUOUS_JOB_STATE", "submit timed out; job may exist")
        except httpx.HTTPError as e:
            raise ProviderError("PROVIDER_UNAVAILABLE", str(e)[:200])

    async def get_job_status(self, job_id: str) -> JobStatus:
        try:
            async with self._client() as cl:
                r = await cl.get(f"/jobs/{job_id}")
                if r.status_code == 404:
                    return JobStatus(state="unknown", detail="job not found")
                self._norm(r)
                j = r.json()
                return JobStatus(state=j.get("state", "unknown"), error_code=j.get("error_code"), detail=j.get("detail"), actual_cost=j.get("cost"))
        except httpx.HTTPError as e:
            raise ProviderError("PROVIDER_UNAVAILABLE", str(e)[:200])

    async def download_result(self, job_id: str, dest: Path) -> Path:
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            async with self._client() as cl:
                async with cl.stream("GET", f"/jobs/{job_id}/result") as r:
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

    async def cancel_job(self, job_id: str) -> None:
        try:
            async with self._client() as cl:
                await cl.post(f"/jobs/{job_id}/cancel")
        except Exception:  # noqa: BLE001
            pass
