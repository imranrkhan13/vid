"""Provider adapter interface (README section 10).

Adapters translate a canonical GenerationRequest into provider payloads. The scheduler never sees
provider-specific formats. Every adapter normalizes failures into the error codes of section 19.
"""
from __future__ import annotations
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

ERROR_CODES = [
    "QUOTA_EXHAUSTED", "CREDITS_EXHAUSTED", "RATE_LIMITED", "AUTHENTICATION_FAILED", "PROVIDER_UNAVAILABLE",
    "MODEL_UNAVAILABLE", "UNSUPPORTED_REQUEST", "GENERATION_TIMEOUT", "AMBIGUOUS_JOB_STATE", "GENERATION_FAILED",
    "CONTENT_POLICY_REJECTION", "QUALITY_VALIDATION_FAILED", "DOWNLOAD_FAILED", "GPU_OUT_OF_MEMORY",
]
# Errors where the provider (or its credits) is the problem, so another provider should take over.
PROVIDER_LEVEL_ERRORS = {"QUOTA_EXHAUSTED", "CREDITS_EXHAUSTED", "AUTHENTICATION_FAILED", "PROVIDER_UNAVAILABLE",
                         "MODEL_UNAVAILABLE", "UNSUPPORTED_REQUEST", "RATE_LIMITED"}


class ProviderError(Exception):
    def __init__(self, code: str, message: str = "", retry_after: float | None = None, reset_at: str | None = None):
        assert code in ERROR_CODES, code
        super().__init__(f"{code}: {message}")
        self.code, self.message, self.retry_after, self.reset_at = code, message, retry_after, reset_at


@dataclass
class ProviderContext:
    user_id: str
    credential_id: str
    provider_id: str
    config: dict[str, Any] = field(default_factory=dict)
    secret: str | None = None  # decrypted in memory only; never logged or returned by the API


@dataclass
class GenerationRequest:
    idempotency_key: str
    project_id: str
    scene_id: str
    scene_index: int
    prompt: str
    negative_prompt: str
    duration: float            # seconds the provider is asked to produce
    needed_seconds: float      # seconds the timeline needs (<= duration when trimming is approved)
    aspect_ratio: str
    width: int
    height: int
    fps: int
    handoff: dict[str, Any] = field(default_factory=dict)
    reference_images: list[str] = field(default_factory=list)
    previous_frame: str | None = None

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class JobStatus:
    state: str                 # queued | running | succeeded | failed | cancelled | unknown
    progress: float | None = None
    error_code: str | None = None
    detail: str | None = None
    actual_cost: float | None = None


@dataclass
class CostEstimate:
    amount: float
    unit: str
    is_paid: bool
    confidence: str            # high | medium | low
    basis: str


@dataclass
class QuotaInfo:
    available: float | None
    unit: str
    source: str                # verified_api | usage_report | user_entered | estimated | unknown
    reset_at: str | None = None
    detail: str | None = None


class VideoProvider:
    """Abstract provider. Methods are async to match README section 10."""
    id: str = ""
    category: str = "native_api"
    is_mock: bool = False

    def __init__(self, ctx: ProviderContext):
        self.ctx = ctx

    async def get_capabilities(self) -> dict[str, Any]: raise NotImplementedError
    async def check_health(self) -> dict[str, Any]: raise NotImplementedError
    async def get_quota(self) -> QuotaInfo: raise NotImplementedError
    async def estimate_cost(self, request: GenerationRequest) -> CostEstimate: raise NotImplementedError
    async def submit_generation(self, request: GenerationRequest) -> str: raise NotImplementedError
    async def get_job_status(self, job_id: str) -> JobStatus: raise NotImplementedError
    async def download_result(self, job_id: str, dest: Path) -> Path: raise NotImplementedError
    async def cancel_job(self, job_id: str) -> None: raise NotImplementedError

    async def find_job_by_idempotency_key(self, key: str) -> str | None:
        """Optional. Lets the scheduler reconcile a submit that crashed before the job id was stored."""
        return None

    async def normalize_error(self, error: Exception) -> ProviderError:
        if isinstance(error, ProviderError):
            return error
        return ProviderError("GENERATION_FAILED", str(error)[:300])


def capability_supports(caps: dict[str, Any], req_duration: float) -> float | None:
    """Return the clip length to request for a scene (provider-compatible mapping), or None if unsupported."""
    ds = caps.get("durations")
    if ds:
        ok = [d for d in sorted(ds) if d >= req_duration - 1e-6]
        return float(ok[0]) if ok else None
    lo, hi = caps.get("min_duration"), caps.get("max_duration")
    if lo is None or hi is None:
        return None
    if req_duration > hi + 1e-6:
        return None
    return float(max(req_duration, lo))
