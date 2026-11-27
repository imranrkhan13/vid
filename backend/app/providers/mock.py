"""Deterministic mock provider (section 31: "deterministic mocks first").

This provider performs **no** network I/O and produces **no** real media. It is
clearly labelled ``is_mock=True`` and is isolated from any real integration. It
can be scripted to simulate the exact failure modes the acceptance test requires:
quota exhaustion, timeouts, ambiguous job states and policy rejection.

Results are deterministic: the same request yields the same synthetic clip
descriptor, so tests are reproducible.
"""

from __future__ import annotations

import hashlib
from collections import deque
from dataclasses import dataclass, field

from app.core.errors import ErrorCode, NormalizedError
from app.schemas.provider import (
    CostEstimate,
    JobState,
    JobStatus,
    ProviderCapabilities,
    ProviderRequest,
    ProviderResult,
    QuotaInfo,
)
from app.providers.base import VideoProvider


@dataclass
class MockScript:
    """Scripted behaviour for a mock provider.

    * ``fail_next`` — errors raised on ``submit_generation`` once
      ``succeed_first`` submissions have already succeeded.
    * ``succeed_first`` — number of submissions that succeed before ``fail_next``
      is consulted (drives "A completes 12s then exhausts").
    * ``status_script`` — per-call script for ``get_job_status``:
      ``("timeout",)`` raises GENERATION_TIMEOUT, ``("ambiguous",)`` returns
      AMBIGUOUS, ``("failed",)`` returns FAILED, ``("succeeded",)`` returns OK.
      When empty, behaviour falls back to ``ambiguous_jobs`` then success.
    """

    # Errors raised on subsequent submit calls, in order.
    fail_next: deque = field(default_factory=deque)
    # Number of submissions that succeed before fail_next is consulted.
    succeed_first: int = 0
    # If set, every submission after succeed_first raises this error (a provider
    # that is permanently down for the remainder of the run).
    fail_forever: ErrorCode | None = None
    # Per-call get_job_status script.
    status_script: deque = field(default_factory=deque)
    # Available credits. None means unknown.
    available_credits: float | None = 100.0
    balance_source: str = "estimated"
    # If True, jobs succeed but status polling reports AMBIGUOUS until reconciled.
    ambiguous_jobs: bool = False
    healthy: bool = True

    def fail_with(self, code: ErrorCode, *, times: int = 1) -> "MockScript":
        for _ in range(times):
            self.fail_next.append(code)
        return self

    def script_status(self, *actions: str) -> "MockScript":
        for action in actions:
            self.status_script.append((action,))
        return self


class MockProvider(VideoProvider):
    """A deterministic, labelled mock execution engine."""

    category = "mock"

    def __init__(
        self,
        provider_id: str = "mock_a",
        *,
        name: str | None = None,
        script: MockScript | None = None,
        quality_score: float = 85.0,
        consistency_score: float = 85.0,
        reliability_score: float = 95.0,
        speed_score: float = 80.0,
        cost_per_second: float = 0.0,
        supports_character_references: bool = True,
        supports_first_last_frame: bool = True,
        commercial_rights: bool = True,
        watermark_free: bool = True,
    ) -> None:
        self.provider_id = provider_id
        self.name = name or f"Mock Provider {provider_id}"
        self.is_mock = True
        self.script = script or MockScript()
        self._jobs: dict[str, ProviderRequest] = {}
        self._counter = 0
        self._submitted = 0
        self._quality_score = quality_score
        self._consistency_score = consistency_score
        self._reliability_score = reliability_score
        self._speed_score = speed_score
        self._cost_per_second = cost_per_second
        self._supports_character_references = supports_character_references
        self._supports_first_last_frame = supports_first_last_frame
        self._commercial_rights = commercial_rights
        self._watermark_free = watermark_free

    # --- capabilities & health -------------------------------------------------
    async def get_capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            model_name=f"{self.provider_id}-model",
            text_to_video=True,
            image_to_video=True,
            video_to_video=True,
            extension=True,
            first_last_frame=self._supports_first_last_frame,
            multiple_references=self._supports_character_references,
            character_references=self._supports_character_references,
            motion_control=True,
            audio=False,
            lip_sync=False,
            max_duration_seconds=10.0,
            supported_aspect_ratios=["9:16", "16:9", "1:1"],
            max_resolution="1080x1920",
            commercial_rights=self._commercial_rights,
            watermark_free=self._watermark_free,
        )

    async def check_health(self) -> bool:
        return self.script.healthy

    async def get_quota(self) -> QuotaInfo:
        return QuotaInfo(
            available_credits=self.script.available_credits,
            balance_source=self.script.balance_source,
            confidence=0.5,
        )

    async def estimate_cost(self, request: ProviderRequest) -> CostEstimate:
        credits = round(request.duration_seconds * self._cost_per_second, 4)
        return CostEstimate(
            credits=credits,
            is_paid=False,
            currency="credits",
            note="mock estimate",
        )

    # --- execution -------------------------------------------------------------
    async def submit_generation(self, request: ProviderRequest) -> str:
        if self._submitted >= self.script.succeed_first:
            if self.script.fail_forever is not None:
                raise NormalizedError(
                    self.script.fail_forever,
                    f"scripted permanent failure on {self.provider_id}",
                    provider=self.provider_id,
                )
            if self.script.fail_next:
                code = self.script.fail_next.popleft()
                raise NormalizedError(
                    code, f"scripted failure on {self.provider_id}", provider=self.provider_id
                )
        self._submitted += 1
        self._counter += 1
        job_id = f"{self.provider_id}-job-{self._counter}"
        self._jobs[job_id] = request
        return job_id

    async def get_job_status(self, job_id: str) -> JobStatus:
        if job_id not in self._jobs:
            return JobStatus(job_id=job_id, state=JobState.FAILED, error_code="AMBIGUOUS_JOB_STATE")
        if self.script.status_script:
            action = self.script.status_script.popleft()[0]
            if action == "timeout":
                raise NormalizedError(
                    ErrorCode.GENERATION_TIMEOUT,
                    f"scripted timeout on {self.provider_id}",
                    provider=self.provider_id,
                )
            if action == "ambiguous":
                return JobStatus(job_id=job_id, state=JobState.AMBIGUOUS, progress=0.5)
            if action == "failed":
                return JobStatus(job_id=job_id, state=JobState.FAILED, error_code="GENERATION_FAILED")
        if self.script.ambiguous_jobs:
            return JobStatus(job_id=job_id, state=JobState.AMBIGUOUS, progress=0.5)
        return JobStatus(job_id=job_id, state=JobState.SUCCEEDED, progress=1.0)

    async def download_result(self, job_id: str) -> ProviderResult:
        request = self._jobs.get(job_id)
        if request is None:
            raise NormalizedError(
                ErrorCode.AMBIGUOUS_JOB_STATE, "unknown job", provider=self.provider_id
            )
        digest = hashlib.sha256(
            f"{self.provider_id}:{request.scene_id}:{request.duration_seconds}".encode()
        ).hexdigest()[:16]
        return ProviderResult(
            job_id=job_id,
            clip_uri=f"mock://{self.provider_id}/{request.scene_id}/{digest}.mp4",
            last_frame_uri=f"mock://{self.provider_id}/{request.scene_id}/{digest}_last.png",
            duration_seconds=request.duration_seconds,
            is_mock=True,
            checksum=digest,
        )

    async def cancel_job(self, job_id: str) -> None:
        self._jobs.pop(job_id, None)

    async def normalize_error(self, error: Exception) -> NormalizedError:
        if isinstance(error, NormalizedError):
            return error
        return NormalizedError(
            ErrorCode.GENERATION_FAILED, str(error), provider=self.provider_id
        )
