"""Storyboard, scene and timeline tables."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Storyboard(Base):
    __tablename__ = "storyboards"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("video_projects.id"), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(32), default="draft")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    scenes: Mapped[list["VideoScene"]] = relationship(back_populates="storyboard")


class VideoScene(Base):
    __tablename__ = "video_scenes"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    storyboard_id: Mapped[str] = mapped_column(ForeignKey("storyboards.id"), index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("video_projects.id"), index=True)
    index: Mapped[int] = mapped_column(Integer)
    description: Mapped[str] = mapped_column(Text, default="")
    duration_seconds: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String(32), default="PENDING", index=True)
    prompt: Mapped[str] = mapped_column(Text, default="")
    negative_prompt: Mapped[str] = mapped_column(Text, default="")
    camera: Mapped[str] = mapped_column(Text, default="")
    motion: Mapped[str] = mapped_column(Text, default="")
    lighting: Mapped[str] = mapped_column(Text, default="")
    environment: Mapped[str] = mapped_column(Text, default="")
    accepted_artifact_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    storyboard: Mapped["Storyboard"] = relationship(back_populates="scenes")
    dependencies: Mapped[list["SceneDependency"]] = relationship(
        back_populates="scene", foreign_keys="SceneDependency.scene_id"
    )


class SceneDependency(Base):
    __tablename__ = "scene_dependencies"
    __table_args__ = (
        UniqueConstraint("scene_id", "depends_on_scene_id", name="uq_scene_dependency"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    scene_id: Mapped[str] = mapped_column(ForeignKey("video_scenes.id"), index=True)
    depends_on_scene_id: Mapped[str] = mapped_column(ForeignKey("video_scenes.id"))
    # e.g. "continuity", "first_frame_from"
    kind: Mapped[str] = mapped_column(String(32), default="continuity")

    scene: Mapped["VideoScene"] = relationship(
        back_populates="dependencies", foreign_keys=[scene_id]
    )


class SceneVersion(Base):
    __tablename__ = "scene_versions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    scene_id: Mapped[str] = mapped_column(ForeignKey("video_scenes.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    artifact_uri: Mapped[str] = mapped_column(String(1024), default="")
    provider: Mapped[str] = mapped_column(String(64), default="")
    decision: Mapped[str] = mapped_column(String(32), default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class MediaTimeline(Base):
    """One canonical media timeline (sections 9, 21)."""

    __tablename__ = "media_timeline"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("video_projects.id"), index=True)
    scene_id: Mapped[str] = mapped_column(ForeignKey("video_scenes.id"), index=True)
    start_seconds: Mapped[float] = mapped_column(Float)
    duration_seconds: Mapped[float] = mapped_column(Float)
    fps: Mapped[int] = mapped_column(Integer, default=30)
    artifact_uri: Mapped[str] = mapped_column(String(1024), default="")
    transition: Mapped[str] = mapped_column(String(32), default="cut")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
