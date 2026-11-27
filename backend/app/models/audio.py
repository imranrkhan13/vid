"""Audio track and narration segment tables (section 17)."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class AudioTrack(Base):
    """One master audio timeline per project."""

    __tablename__ = "audio_tracks"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("video_projects.id"), index=True)
    kind: Mapped[str] = mapped_column(String(32), default="narration")  # narration/music/sfx
    uri: Mapped[str] = mapped_column(String(1024), default="")
    duration_seconds: Mapped[float] = mapped_column(Float, default=0.0)
    is_master: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class NarrationSegment(Base):
    __tablename__ = "narration_segments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("video_projects.id"), index=True)
    audio_track_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    scene_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    text: Mapped[str] = mapped_column(Text, default="")
    start_seconds: Mapped[float] = mapped_column(Float, default=0.0)
    end_seconds: Mapped[float] = mapped_column(Float, default=0.0)
    alignment_method: Mapped[str] = mapped_column(String(32), default="forced_alignment")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
