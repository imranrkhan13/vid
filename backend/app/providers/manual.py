"""Manual-assisted provider: the platform produces prompt + references; the user generates the clip in an
official consumer interface and imports it. No scraping or automation of consumer sites."""
from __future__ import annotations
from pathlib import Path
from typing import Any

from .base import CostEstimate, GenerationRequest, JobStatus, ProviderContext, QuotaInfo, VideoProvider


class ManualProvider(VideoProvider):
    category = "manual"

    async def get_capabilities(self) -> dict[str, Any]:
        return {"label": "Manual import", "min_duration": 1, "max_duration": 120, "aspect_ratios": ["9:16", "16:9", "1:1"],
                "max_width": 4096, "max_height": 4096, "first_frame": True, "last_frame": True, "multi_reference": True,
                "character_reference": True, "audio": False, "watermark": None, "commercial_ok": None,
                "cost_per_second": 0.0, "unit": "none", "paid": False, "est_latency_s": 3600, "speed_score": 0.05, "concurrency": 99,
                "manual": True, "source": "user generates externally; rights and watermarks are the user's responsibility"}

    async def check_health(self): return {"ok": True, "detail": "manual import always available"}
    async def get_quota(self): return QuotaInfo(available=1e9, unit="none", source="user_entered", detail="no platform-side quota")
    async def estimate_cost(self, request): return CostEstimate(0.0, "none", False, "high", "the user generates the clip outside the platform")
    async def submit_generation(self, request: GenerationRequest) -> str: return "manual_" + request.idempotency_key[-16:]
    async def get_job_status(self, job_id: str): return JobStatus(state="running", detail="waiting for the user to import a clip")
    async def download_result(self, job_id: str, dest: Path): raise NotImplementedError("manual jobs complete via import")
    async def cancel_job(self, job_id: str): return None
