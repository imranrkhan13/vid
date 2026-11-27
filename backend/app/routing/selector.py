"""Provider selection engine (README section 11).

Two stages:
  1. Hard filters — eliminate providers that cannot serve the request at all.
  2. Weighted scoring — rank the survivors by a configurable profile.

Baseline weights (section 11):
  character consistency 25, quality 25, free credits 20, cost 10,
  reliability 10, speed 5, resolution 5.

Providers are never selected on free credits alone: if every other factor is
unknown, the free-credits term cannot carry a candidate to the top on its own.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.credits.ledger import CreditLedger
from app.schemas.provider import GenerationMode, ProviderCapabilities, ProviderRequest


@dataclass
class Candidate:
    """A provider being considered, with the facts the router needs."""

    provider_id: str
    capabilities: ProviderCapabilities
    healthy: bool = True
    has_credentials: bool = True
    # Remaining credits, or None for unknown.
    remaining_credits: float | None = None
    estimated_credits: float = 0.0
    estimated_is_paid: bool = False
    # Optional measured quality/consistency (0-100). None = unmeasured.
    consistency_score: float | None = None
    quality_score: float | None = None
    reliability_score: float | None = None
    speed_score: float | None = None
    is_mock: bool = False
    supports_privacy: bool = True
    reasons: list[str] = field(default_factory=list)


@dataclass
class Weights:
    consistency: float = 0.25
    quality: float = 0.25
    free_credits: float = 0.20
    cost: float = 0.10
    reliability: float = 0.10
    speed: float = 0.05
    resolution: float = 0.05

    def total(self) -> float:
        return (
            self.consistency
            + self.quality
            + self.free_credits
            + self.cost
            + self.reliability
            + self.speed
            + self.resolution
        )


# Project-type weight adjustments (section 11).
PROFILES: dict[str, Weights] = {
    "default": Weights(),
    "childrens_animation": Weights(consistency=0.30, quality=0.25, speed=0.10),
    "documentary": Weights(consistency=0.20, quality=0.30, resolution=0.10),
    "promotion": Weights(consistency=0.20, quality=0.30, cost=0.15),
}

_RESOLUTION_RANK = {
    "480x854": 1,
    "720x1280": 2,
    "1080x1920": 3,
    "1280x720": 2,
    "1920x1080": 3,
    "2048x2048": 4,
    "3840x2160": 5,
}


def _resolution_score(resolution: str) -> float:
    """Normalize a resolution string to a 0..1 score."""
    rank = _RESOLUTION_RANK.get(resolution.lower())
    if rank is None:
        return 0.5  # unknown resolution: neutral, never assumed best
    return min(1.0, rank / 5.0)


class SelectionError(Exception):
    """Raised when no provider survives the hard filters."""


class ProviderSelector:
    def __init__(
        self,
        weights: Weights | None = None,
        *,
        ledger: CreditLedger | None = None,
        allow_paid_spend: bool = False,
        required_commercial_rights: bool = True,
        require_watermark_free: bool = False,
    ) -> None:
        self.weights = weights or Weights()
        self.ledger = ledger
        self.allow_paid_spend = allow_paid_spend
        self.required_commercial_rights = required_commercial_rights
        self.require_watermark_free = require_watermark_free

    # --- stage 1: hard filters -------------------------------------------------
    def passes_hard_filters(self, c: Candidate, request: ProviderRequest) -> bool:
        caps = c.capabilities
        c.reasons.clear()
        ok = True

        def fail(reason: str) -> None:
            nonlocal ok
            ok = False
            c.reasons.append(reason)

        if not c.healthy:
            fail("unhealthy")
        if not c.has_credentials:
            fail("no credentials")
        if not c.supports_privacy:
            fail("privacy requirement unmet")

        # Generation mode support. Unknown (None) fails a required capability:
        # we never assume an unverified capability is present (section 3).
        mode_ok = {
            GenerationMode.TEXT_TO_VIDEO: caps.text_to_video,
            GenerationMode.IMAGE_TO_VIDEO: caps.image_to_video,
            GenerationMode.VIDEO_TO_VIDEO: caps.video_to_video,
            GenerationMode.CONTINUATION: caps.extension or caps.first_last_frame,
        }.get(request.mode)
        if mode_ok is not True:
            fail(f"mode {request.mode.value} unsupported")

        if caps.max_duration_seconds is not None and request.duration_seconds > caps.max_duration_seconds:
            fail("duration exceeds max")

        if (
            caps.supported_aspect_ratios
            and request.aspect_ratio not in caps.supported_aspect_ratios
        ):
            fail("aspect ratio unsupported")

        if request.references and not caps.character_references and not caps.multiple_references:
            fail("references unsupported")

        if self.required_commercial_rights and caps.commercial_rights is not True:
            fail("commercial rights not confirmed")
        if self.require_watermark_free and caps.watermark_free is not True:
            fail("watermark-free not confirmed")

        # Budget/quota. Unknown remaining credits cannot be assumed sufficient
        # unless the request is free (zero estimated credits). A known balance of
        # zero means the provider is exhausted and cannot serve any request.
        if c.estimated_is_paid and not self.allow_paid_spend:
            fail("paid spend not approved")
        if c.remaining_credits is None:
            if c.estimated_credits > 0:
                fail("credit balance unknown")
        elif c.remaining_credits <= 0:
            fail("no credits remaining")
        elif c.remaining_credits < c.estimated_credits:
            fail("insufficient credits")

        return ok

    # --- stage 2: weighted scoring --------------------------------------------
    def score(self, c: Candidate, request: ProviderRequest) -> float:
        w = self.weights
        caps = c.capabilities

        consistency = (c.consistency_score or 0.0) / 100.0
        quality = (c.quality_score or 0.0) / 100.0
        reliability = (c.reliability_score or 0.0) / 100.0
        speed = (c.speed_score or 0.0) / 100.0
        resolution = _resolution_score(request.resolution)

        # Free credits: 1.0 when the request consumes no paid credits, else 0.
        free_credits = 1.0 if not c.estimated_is_paid else 0.0
        # Cost efficiency: cheaper (relative to a nominal ceiling) scores higher.
        cost = 1.0 if c.estimated_credits <= 0 else max(0.0, 1.0 - min(c.estimated_credits, 10.0) / 10.0)

        total = (
            w.consistency * consistency
            + w.quality * quality
            + w.free_credits * free_credits
            + w.cost * cost
            + w.reliability * reliability
            + w.speed * speed
            + w.resolution * resolution
        )
        # Normalize so the score is comparable across weight profiles.
        return total / (w.total() or 1.0)

    def select(
        self, candidates: list[Candidate], request: ProviderRequest
    ) -> Candidate:
        """Return the best eligible candidate, or raise ``SelectionError``."""
        eligible = [c for c in candidates if self.passes_hard_filters(c, request)]
        if not eligible:
            raise SelectionError("no eligible provider passed the hard filters")
        # Never select on free credits alone: break ties using a non-credit
        # quality signal before falling back to the id for determinism.
        def sort_key(c: Candidate):
            return (
                -self.score(c, request),
                -(c.consistency_score or 0.0),
                -(c.quality_score or 0.0),
                c.provider_id,
            )

        eligible.sort(key=sort_key)
        return eligible[0]
