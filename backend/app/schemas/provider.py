"""Provider-facing request/response schemas.

Adapters translate these canonical structures into provider payloads; provider
formatting never leaks into the scheduler (section 10).
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class GenerationMode(str, Enum):
    TEXT_TO_VIDEO = "text_to_video"
    IMAGE_TO_VIDEO = "image_to_video"
    VIDEO_TO_VIDEO = "video_to_video"
    CONTINUATION = "continuation"


class ReferenceImage(BaseModel):
    uri: str
    role: str = "character"  # character / style / first_frame / last_frame


class ProviderRequest(BaseModel):
    """Canonical generation request passed to every adapter."""

    request_id: str
    project_id: str
    scene_id: str
    mode: GenerationMode = GenerationMode.TEXT_TO_VIDEO
    prompt: str = ""
    negative_prompt: str = ""
    duration_seconds: float
    aspect_ratio: str = "9:16"
    resolution: str = "1080x1920"
    fps: int = 30
    references: list[ReferenceImage] = Field(default_factory=list)
    previous_frame_uri: str | None = None
    seed: int | None = None
    # Visual fingerprint carried between scenes for drift detection (section 15).
    style_signature: dict = Field(default_factory=dict)
    extra: dict = Field(default_factory=dict)


class ProviderCapabilities(BaseModel):
    """Adapter-reported capabilities. ``None`` means unknown, never assumed True."""

    model_name: str
    text_to_video: bool | None = None
    image_to_video: bool | None = None
    video_to_video: bool | None = None
    extension: bool | None = None
    first_last_frame: bool | None = None
    multiple_references: bool | None = None
    character_references: bool | None = None
    motion_control: bool | None = None
    audio: bool | None = None
    lip_sync: bool | None = None
    max_duration_seconds: float | None = None
    supported_aspect_ratios: list[str] = Field(default_factory=list)
    max_resolution: str = ""
    commercial_rights: bool | None = None
    watermark_free: bool | None = None


class ProviderCategory(str, Enum):
    """Resource category — determines quota accounting and automation rules."""

    NATIVE_API = "native_api"
    OPEN_SOURCE_GPU = "open_source_gpu"
    LOCAL = "local"
    MANUAL_ASSISTED = "manual_assisted"
    AGGREGATOR = "aggregator"
    MOCK = "mock"


class CreditGrant(BaseModel):
    """One credit allotment with its reset cadence (section 3, 12).

    ``limit`` is ``None`` when unknown — never assume unlimited. ``unit`` is the
    resource's own unit (e.g. "credits", "seconds", "generations").
    """

    limit: float | None = None
    used: float = 0.0
    period: str = "none"  # none / daily / weekly / monthly / trial / one_time
    reset_schedule: str = ""  # e.g. "00:00 UTC daily" or "monthly on signup day"
    expires_at: str | None = None
    unit: str = "credits"
    note: str = ""


class ProviderCatalogEntry(BaseModel):
    """A researched provider record (section 3). Unknowns are ``None``/empty.

    This is the single source of truth for provider facts; nothing here may be
    hardcoded in application logic.
    """

    provider_id: str
    name: str
    category: ProviderCategory
    is_mock: bool = False

    # Provenance — a fact is only trustworthy once verified against official docs.
    verified: bool = False
    verification_date: str = ""
    docs_url: str = ""
    website_url: str = ""
    notes: str = ""

    # Capabilities (None = unknown).
    model_name: str = ""
    text_to_video: bool | None = None
    image_to_video: bool | None = None
    video_to_video: bool | None = None
    extension: bool | None = None
    first_last_frame: bool | None = None
    multiple_references: bool | None = None
    character_references: bool | None = None
    motion_control: bool | None = None
    audio: bool | None = None
    lip_sync: bool | None = None
    max_duration_seconds: float | None = None
    supported_aspect_ratios: list[str] = Field(default_factory=list)
    max_resolution: str = ""
    commercial_rights: bool | None = None
    watermark_free: bool | None = None

    # Commercial / operational facts.
    cost_per_second: float | None = None
    api_pricing: str = ""
    concurrency: int | None = None
    rate_limit: str = ""
    download_rights: bool | None = None
    retention: str = ""

    # Credit allotments — daily/weekly/monthly/trial.
    credits: list[CreditGrant] = Field(default_factory=list)

    # Self-host / open-source facts (section 20).
    gpu_requirements: str = ""
    weights_url: str = ""
    license: str = ""

    # Feasibility: can this actually be driven by the harness today?
    feasible: bool | None = None

    def capabilities(self) -> ProviderCapabilities:
        """Project the capability subset into the router-facing schema."""
        return ProviderCapabilities(
            model_name=self.model_name,
            text_to_video=self.text_to_video,
            image_to_video=self.image_to_video,
            video_to_video=self.video_to_video,
            extension=self.extension,
            first_last_frame=self.first_last_frame,
            multiple_references=self.multiple_references,
            character_references=self.character_references,
            motion_control=self.motion_control,
            audio=self.audio,
            lip_sync=self.lip_sync,
            max_duration_seconds=self.max_duration_seconds,
            supported_aspect_ratios=list(self.supported_aspect_ratios),
            max_resolution=self.max_resolution,
            commercial_rights=self.commercial_rights,
            watermark_free=self.watermark_free,
        )

    def has_any_generation_capability(self) -> bool:
        """True if at least one generation mode is explicitly supported."""
        return any(
            v is True
            for v in (
                self.text_to_video,
                self.image_to_video,
                self.video_to_video,
                self.extension,
            )
        )


class QuotaInfo(BaseModel):
    available_credits: float | None = None
    reserved_credits: float = 0.0
    consumed_credits: float = 0.0
    limit_credits: float | None = None
    period: str = "none"
    reset_at: str | None = None
    balance_source: str = "unknown"
    confidence: float = 0.0
    concurrency: int | None = None

    def is_unknown(self) -> bool:
        return self.balance_source == "unknown" or self.available_credits is None


class CostEstimate(BaseModel):
    credits: float = 0.0
    is_paid: bool = False
    currency: str = "credits"
    note: str = ""


class JobState(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    AMBIGUOUS = "ambiguous"
    CANCELLED = "cancelled"


class JobStatus(BaseModel):
    job_id: str
    state: JobState
    progress: float = 0.0
    error_code: str | None = None
    message: str = ""


class ProviderResult(BaseModel):
    job_id: str
    clip_uri: str
    last_frame_uri: str | None = None
    duration_seconds: float = 0.0
    is_mock: bool = False
    checksum: str = ""
    raw: dict = Field(default_factory=dict)
