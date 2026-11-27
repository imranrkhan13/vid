"""Canonical project state and workflow enums (README sections 8 and 24).

``CanonicalProjectState`` is the authoritative structured record of a project.
It mirrors the JSON example in section 8 and is used by the orchestrator for
handoff, continuation and progress calculation.

Progress is always computed from accepted clips on the final timeline, never
from completed API requests.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class ProjectStatus(str, Enum):
    """High-level workflow states (section 24)."""

    DRAFT = "DRAFT"
    PLANNING = "PLANNING"
    STORYBOARD_READY = "STORYBOARD_READY"
    AWAITING_USER_APPROVAL = "AWAITING_USER_APPROVAL"
    READY = "READY"
    SELECTING_PROVIDER = "SELECTING_PROVIDER"
    GENERATING = "GENERATING"
    DOWNLOADING = "DOWNLOADING"
    VALIDATING = "VALIDATING"
    RETRYING = "RETRYING"
    SWITCHING_PROVIDER = "SWITCHING_PROVIDER"
    MANUAL_ACTION_REQUIRED = "MANUAL_ACTION_REQUIRED"
    WAITING_FOR_CREDITS = "WAITING_FOR_CREDITS"
    WAITING_FOR_PROVIDER = "WAITING_FOR_PROVIDER"
    COMPOSING = "COMPOSING"
    FINAL_VALIDATION = "FINAL_VALIDATION"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    PAUSED = "PAUSED"
    CANCELLED = "CANCELLED"


class SceneStatus(str, Enum):
    """Per-scene states, tracked separately from the project state (section 24)."""

    PENDING = "PENDING"
    SELECTING_PROVIDER = "SELECTING_PROVIDER"
    GENERATING = "GENERATING"
    DOWNLOADING = "DOWNLOADING"
    VALIDATING = "VALIDATING"
    RETRYING = "RETRYING"
    SWITCHING_PROVIDER = "SWITCHING_PROVIDER"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    MANUAL_ACTION_REQUIRED = "MANUAL_ACTION_REQUIRED"
    FAILED = "FAILED"


class BalanceSource(str, Enum):
    """How a credit balance was obtained (section 12)."""

    VERIFIED_API = "verified_balance_api"
    USAGE_REPORT = "usage_report"
    USER_ENTERED = "user_entered"
    ESTIMATED = "estimated"
    UNKNOWN = "unknown"


class AudioStrategy(str, Enum):
    EXTERNAL_MASTER_NARRATION = "external_master_narration"
    TTS = "tts"
    PROVIDER_NATIVE = "provider_native"


class CanonicalProjectState(BaseModel):
    """Authoritative structured project state (section 8).

    Structured, versioned data is authoritative; embeddings only supplement it.
    """

    project_id: str
    title: str
    target_duration_seconds: float
    accepted_duration_seconds: float = 0.0
    remaining_duration_seconds: float = 0.0
    aspect_ratio: str = "9:16"
    resolution: str = "1080x1920"
    fps: int = 30
    style_version: int = 1
    character_bible_version: int = 1
    audio_strategy: AudioStrategy = AudioStrategy.EXTERNAL_MASTER_NARRATION
    completed_scenes: list[str] = Field(default_factory=list)
    next_scene: str | None = None
    last_accepted_provider: str | None = None
    last_accepted_frame: str | None = None
    status: ProjectStatus = ProjectStatus.DRAFT

    def recompute(self) -> "CanonicalProjectState":
        """Recompute derived durations from accepted scenes.

        ``accepted_duration_seconds`` is supplied by the timeline engine; this
        helper keeps ``remaining_duration_seconds`` consistent and never negative.
        """
        self.remaining_duration_seconds = max(
            0.0, self.target_duration_seconds - self.accepted_duration_seconds
        )
        return self
