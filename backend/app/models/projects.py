"""Project, character, style and checkpoint tables."""

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
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    projects: Mapped[list["VideoProject"]] = relationship(back_populates="owner")


class VideoProject(Base):
    __tablename__ = "video_projects"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    title: Mapped[str] = mapped_column(String(512))
    concept: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(32), default="DRAFT", index=True)
    target_duration_seconds: Mapped[float] = mapped_column(Float, default=0.0)
    accepted_duration_seconds: Mapped[float] = mapped_column(Float, default=0.0)
    aspect_ratio: Mapped[str] = mapped_column(String(16), default="9:16")
    resolution: Mapped[str] = mapped_column(String(32), default="1080x1920")
    fps: Mapped[int] = mapped_column(Integer, default=30)
    style_version: Mapped[int] = mapped_column(Integer, default=1)
    character_bible_version: Mapped[int] = mapped_column(Integer, default=1)
    audio_strategy: Mapped[str] = mapped_column(String(64), default="external_master_narration")
    # Budget policy (section 12, 27): total credit cap and paid-spend controls.
    max_budget: Mapped[float | None] = mapped_column(Float, nullable=True)
    allow_paid: Mapped[bool] = mapped_column(Boolean, default=False)
    max_paid_spend: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )

    owner: Mapped["User | None"] = relationship(back_populates="projects")
    versions: Mapped[list["ProjectVersion"]] = relationship(back_populates="project")
    checkpoints: Mapped[list["ProjectCheckpoint"]] = relationship(back_populates="project")


class ProjectVersion(Base):
    __tablename__ = "project_versions"
    __table_args__ = (UniqueConstraint("project_id", "version", name="uq_project_version"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("video_projects.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    # Important instructions are versioned (section 7).
    instructions: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    project: Mapped["VideoProject"] = relationship(back_populates="versions")


class Character(Base):
    __tablename__ = "characters"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str | None] = mapped_column(
        ForeignKey("video_projects.id"), nullable=True, index=True
    )
    # Reusable character library: a null project_id means it is library-global.
    name: Mapped[str] = mapped_column(String(256))
    description: Mapped[str] = mapped_column(Text, default="")
    palette: Mapped[str] = mapped_column(Text, default="")
    proportions: Mapped[str] = mapped_column(Text, default="")
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    references: Mapped[list["CharacterReferenceAsset"]] = relationship(back_populates="character")


class CharacterReferenceAsset(Base):
    __tablename__ = "character_reference_assets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    character_id: Mapped[str] = mapped_column(ForeignKey("characters.id"), index=True)
    # front / side / back / three_quarter / closeup / expression / clothing
    view: Mapped[str] = mapped_column(String(32))
    uri: Mapped[str] = mapped_column(String(1024))
    checksum: Mapped[str] = mapped_column(String(128), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    character: Mapped["Character"] = relationship(back_populates="references")


class StyleProfile(Base):
    __tablename__ = "style_profiles"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str | None] = mapped_column(
        ForeignKey("video_projects.id"), nullable=True, index=True
    )
    name: Mapped[str] = mapped_column(String(256))
    description: Mapped[str] = mapped_column(Text, default="")
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class ProjectCheckpoint(Base):
    """Durable checkpoint so work resumes after a restart (sections 13, 24)."""

    __tablename__ = "project_checkpoints"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("video_projects.id"), index=True)
    state_json: Mapped[str] = mapped_column(Text)
    label: Mapped[str] = mapped_column(String(128), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    project: Mapped["VideoProject"] = relationship(back_populates="checkpoints")
