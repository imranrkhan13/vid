"""Provider, capability, credential and quota tables (sections 3, 10, 12)."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ProviderModel(Base):
    """A generation provider (an execution engine). Facts are never hardcoded."""

    __tablename__ = "generation_providers"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(256))
    category: Mapped[str] = mapped_column(String(32), default="native_api")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    # Mock providers must be clearly labelled and isolated (section 33).
    is_mock: Mapped[bool] = mapped_column(Boolean, default=False)
    docs_url: Mapped[str] = mapped_column(String(1024), default="")
    verification_date: Mapped[str] = mapped_column(String(32), default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class ProviderCapability(Base):
    """Machine-readable capability record; unknown facts are explicit (section 3)."""

    __tablename__ = "provider_capabilities"
    __table_args__ = (
        UniqueConstraint("provider_id", "model_name", name="uq_provider_model_cap"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    provider_id: Mapped[str] = mapped_column(ForeignKey("generation_providers.id"), index=True)
    model_name: Mapped[str] = mapped_column(String(256))
    # Capability flags. None means "unknown", which must never be assumed True.
    text_to_video: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    image_to_video: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    video_to_video: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    extension: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    first_last_frame: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    multiple_references: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    character_references: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    motion_control: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    audio: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    lip_sync: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    max_duration_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    supported_aspect_ratios: Mapped[str] = mapped_column(Text, default="")  # JSON list
    max_resolution: Mapped[str] = mapped_column(String(32), default="")
    commercial_rights: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    watermark_free: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    # Heuristic quality/consistency scores (0-100). None = unmeasured.
    quality_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    consistency_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    reliability_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    speed_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    cost_per_second: Mapped[float | None] = mapped_column(Float, nullable=True)
    verified: Mapped[bool] = mapped_column(Boolean, default=False)


class ProviderCredential(Base):
    """Encrypted credential record. Never exposed in frontend, git, prompts or logs."""

    __tablename__ = "provider_credentials"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    provider_id: Mapped[str] = mapped_column(ForeignKey("generation_providers.id"), index=True)
    billing_account: Mapped[str] = mapped_column(String(128), default="")
    organization: Mapped[str] = mapped_column(String(128), default="")
    encrypted_secret: Mapped[str] = mapped_column(Text, default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class QuotaPool(Base):
    """A shared quota pool (e.g. a host serving several models). Avoid double-counting."""

    __tablename__ = "quota_pools"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(256))
    description: Mapped[str] = mapped_column(Text, default="")


class ProviderQuota(Base):
    """Tracked credits for a provider/credential/pool/model combination (section 12)."""

    __tablename__ = "provider_quotas"
    __table_args__ = (
        UniqueConstraint(
            "provider_id",
            "credential_id",
            "model_name",
            "period",
            name="uq_provider_quota_scope",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    provider_id: Mapped[str] = mapped_column(ForeignKey("generation_providers.id"), index=True)
    credential_id: Mapped[str | None] = mapped_column(
        ForeignKey("provider_credentials.id"), nullable=True
    )
    pool_id: Mapped[str | None] = mapped_column(ForeignKey("quota_pools.id"), nullable=True)
    model_name: Mapped[str] = mapped_column(String(256), default="")
    # daily / weekly / monthly / trial / one_time / none
    period: Mapped[str] = mapped_column(String(16), default="none")
    balance_source: Mapped[str] = mapped_column(String(32), default="unknown")
    available_credits: Mapped[float | None] = mapped_column(Float, nullable=True)
    reserved_credits: Mapped[float] = mapped_column(Float, default=0.0)
    consumed_credits: Mapped[float] = mapped_column(Float, default=0.0)
    limit_credits: Mapped[float | None] = mapped_column(Float, nullable=True)
    # True when drawing this bucket spends real money (section 12, 27).
    is_paid: Mapped[bool] = mapped_column(Boolean, default=False)
    # When the bucket refills (daily/weekly/monthly resets). Null = no reset.
    reset_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reset_timezone: Mapped[str] = mapped_column(String(64), default="")
    # Trial buckets expire (never refill) at this time.
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    concurrency: Mapped[int | None] = mapped_column(Integer, nullable=True)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    last_success_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )
