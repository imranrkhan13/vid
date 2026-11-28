"""SQLAlchemy models. Names follow README section 23. Media bytes live on the storage backend, metadata here."""
from __future__ import annotations
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import LargeBinary, JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint, Index
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.core.security import new_id


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSON, list[Any]: JSON}


def pk(prefix: str):
    return mapped_column(String(40), primary_key=True, default=lambda: new_id(prefix))


def fk(target: str, **kw):
    return mapped_column(String(40), ForeignKey(target, ondelete="CASCADE"), **kw)


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = pk("usr")
    email: Mapped[str] = mapped_column(String(320), unique=True)
    name: Mapped[str | None] = mapped_column(String(200))
    pw_salt: Mapped[str] = mapped_column(String(64))
    pw_hash: Mapped[str] = mapped_column(String(256))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class UserSession(Base):
    __tablename__ = "sessions"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = fk("users.id", index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class VideoProject(Base):
    __tablename__ = "video_projects"
    id: Mapped[str] = pk("prj")
    user_id: Mapped[str] = fk("users.id", index=True)
    title: Mapped[str] = mapped_column(String(300))
    brief: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(40), default="DRAFT", index=True)
    status_detail: Mapped[str | None] = mapped_column(Text)
    paused_from: Mapped[str | None] = mapped_column(String(40))
    target_duration: Mapped[float] = mapped_column(Float)
    accepted_duration: Mapped[float] = mapped_column(Float, default=0)
    aspect_ratio: Mapped[str] = mapped_column(String(10), default="9:16")
    width: Mapped[int] = mapped_column(Integer, default=1080)
    height: Mapped[int] = mapped_column(Integer, default=1920)
    fps: Mapped[int] = mapped_column(Integer, default=30)
    mode: Mapped[str] = mapped_column(String(30), default="free_only")
    weights: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    paid_budget: Mapped[float] = mapped_column(Float, default=0)
    require_no_watermark: Mapped[bool] = mapped_column(Boolean, default=False)
    require_commercial: Mapped[bool] = mapped_column(Boolean, default=False)
    require_storyboard_approval: Mapped[bool] = mapped_column(Boolean, default=True)
    style_version: Mapped[int] = mapped_column(Integer, default=1)
    character_bible_version: Mapped[int] = mapped_column(Integer, default=1)
    last_accepted_provider: Mapped[str | None] = mapped_column(String(60))
    last_accepted_frame: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class ProjectVersion(Base):
    __tablename__ = "project_versions"
    id: Mapped[str] = pk("pv")
    project_id: Mapped[str] = fk("video_projects.id", index=True)
    version: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(40))  # instruction | style | character_bible | approval
    data: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Character(Base):
    __tablename__ = "characters"
    id: Mapped[str] = pk("chr")
    user_id: Mapped[str] = fk("users.id", index=True)
    name: Mapped[str] = mapped_column(String(200))
    profile: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class CharacterReferenceAsset(Base):
    __tablename__ = "character_reference_assets"
    id: Mapped[str] = pk("cra")
    character_id: Mapped[str] = fk("characters.id", index=True)
    kind: Mapped[str] = mapped_column(String(40))  # front, side, back, three_quarter, close_up, expression
    path: Mapped[str] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ProjectCharacter(Base):
    __tablename__ = "project_characters"
    project_id: Mapped[str] = fk("video_projects.id", primary_key=True)
    character_id: Mapped[str] = fk("characters.id", primary_key=True)


class StyleProfile(Base):
    __tablename__ = "style_profiles"
    project_id: Mapped[str] = fk("video_projects.id", primary_key=True)
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    data: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Storyboard(Base):
    __tablename__ = "storyboards"
    id: Mapped[str] = pk("sb")
    project_id: Mapped[str] = fk("video_projects.id", index=True)
    version: Mapped[int] = mapped_column(Integer)
    data: Mapped[dict[str, Any]] = mapped_column(JSON)
    director: Mapped[str] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class VideoScene(Base):
    __tablename__ = "video_scenes"
    __table_args__ = (UniqueConstraint("project_id", "idx"),)
    id: Mapped[str] = pk("scn")
    project_id: Mapped[str] = fk("video_projects.id", index=True)
    idx: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(300))
    description: Mapped[str] = mapped_column(Text)
    prompt: Mapped[str] = mapped_column(Text)
    negative_prompt: Mapped[str | None] = mapped_column(Text)
    camera: Mapped[str | None] = mapped_column(Text)
    motion: Mapped[str | None] = mapped_column(Text)
    lighting: Mapped[str | None] = mapped_column(Text)
    environment: Mapped[str | None] = mapped_column(Text)
    emotion: Mapped[str | None] = mapped_column(Text)
    narration: Mapped[str | None] = mapped_column(Text)
    characters: Mapped[list[Any]] = mapped_column(JSON, default=list)
    acceptance: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    duration: Mapped[float] = mapped_column(Float)
    start_time: Mapped[float] = mapped_column(Float, default=0)
    status: Mapped[str] = mapped_column(String(40), default="PENDING", index=True)
    accepted_artifact_id: Mapped[str | None] = mapped_column(String(40))
    retries: Mapped[int] = mapped_column(Integer, default=0)
    attempt_counter: Mapped[int] = mapped_column(Integer, default=0)
    scene_version: Mapped[int] = mapped_column(Integer, default=1)
    provider_hint: Mapped[str | None] = mapped_column(String(60))
    avoid_providers: Mapped[list[Any]] = mapped_column(JSON, default=list)
    corrective_notes: Mapped[list[Any]] = mapped_column(JSON, default=list)
    last_error: Mapped[str | None] = mapped_column(Text)
    not_before: Mapped[str | None] = mapped_column(String(40))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class SceneDependency(Base):
    __tablename__ = "scene_dependencies"
    scene_id: Mapped[str] = fk("video_scenes.id", primary_key=True)
    depends_on: Mapped[str] = fk("video_scenes.id", primary_key=True)


class SceneVersion(Base):
    __tablename__ = "scene_versions"
    id: Mapped[str] = pk("sv")
    scene_id: Mapped[str] = fk("video_scenes.id", index=True)
    version: Mapped[int] = mapped_column(Integer)
    data: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class GenerationProvider(Base):
    """Catalog of provider integrations (global, not per user)."""
    __tablename__ = "generation_providers"
    id: Mapped[str] = mapped_column(String(60), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    category: Mapped[str] = mapped_column(String(40))  # native_api | open_source_gpu | manual | custom | mock
    implemented: Mapped[bool] = mapped_column(Boolean, default=False)
    verification: Mapped[str] = mapped_column(String(40), default="unverified")
    docs_url: Mapped[str | None] = mapped_column(String(500))
    key_url: Mapped[str | None] = mapped_column(String(500))  # where to get a key for this provider
    host_family: Mapped[str | None] = mapped_column(String(80))  # shared quota pool family
    notes: Mapped[str | None] = mapped_column(Text)
    verified_at: Mapped[str | None] = mapped_column(String(40))


class ProviderCredential(Base):
    __tablename__ = "provider_credentials"
    __table_args__ = (UniqueConstraint("user_id", "provider_id", "label"),)
    id: Mapped[str] = pk("crd")
    user_id: Mapped[str] = fk("users.id", index=True)
    provider_id: Mapped[str] = mapped_column(String(60))
    label: Mapped[str] = mapped_column(String(120))
    secret_enc: Mapped[str | None] = mapped_column(Text)
    secret_hint: Mapped[str | None] = mapped_column(String(20))
    config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class QuotaPool(Base):
    __tablename__ = "quota_pools"
    id: Mapped[str] = pk("qp")
    user_id: Mapped[str] = fk("users.id", index=True)
    credential_id: Mapped[str] = fk("provider_credentials.id", index=True)
    provider_id: Mapped[str] = mapped_column(String(60))
    model: Mapped[str | None] = mapped_column(String(120))
    unit: Mapped[str] = mapped_column(String(30), default="credits")
    balance_source: Mapped[str] = mapped_column(String(30), default="unknown")  # verified_api|usage_report|user_entered|estimated|unknown
    available: Mapped[float | None] = mapped_column(Float)
    reserved: Mapped[float] = mapped_column(Float, default=0)
    consumed: Mapped[float] = mapped_column(Float, default=0)
    is_paid: Mapped[bool] = mapped_column(Boolean, default=False)
    reset_at: Mapped[str | None] = mapped_column(String(40))
    reset_tz: Mapped[str | None] = mapped_column(String(60))
    exhausted_until: Mapped[str | None] = mapped_column(String(40))
    exhausted: Mapped[bool] = mapped_column(Boolean, default=False)
    last_success: Mapped[str | None] = mapped_column(String(40))
    concurrency: Mapped[int] = mapped_column(Integer, default=1)
    confidence: Mapped[str] = mapped_column(String(20), default="low")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class CreditTransaction(Base):
    __tablename__ = "credit_transactions"
    id: Mapped[str] = pk("ctx")
    pool_id: Mapped[str] = fk("quota_pools.id", index=True)
    user_id: Mapped[str] = mapped_column(String(40))
    job_id: Mapped[str | None] = mapped_column(String(40))
    kind: Mapped[str] = mapped_column(String(20))  # reserve | release | consume | adjust
    amount: Mapped[float] = mapped_column(Float)
    estimated: Mapped[float | None] = mapped_column(Float)
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class GenerationJob(Base):
    __tablename__ = "generation_jobs"
    id: Mapped[str] = pk("job")
    user_id: Mapped[str] = mapped_column(String(40), index=True)
    project_id: Mapped[str] = fk("video_projects.id", index=True)
    scene_id: Mapped[str] = fk("video_scenes.id", index=True)
    credential_id: Mapped[str] = mapped_column(String(40))
    provider_id: Mapped[str] = mapped_column(String(60))
    pool_id: Mapped[str | None] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(30), index=True)  # SUBMITTING|SUBMITTED|SUCCEEDED|FAILED|CANCELLED|AMBIGUOUS|MANUAL|DONE|ABANDONED
    idempotency_key: Mapped[str] = mapped_column(String(200), unique=True)
    provider_job_id: Mapped[str | None] = mapped_column(String(200))
    requested_seconds: Mapped[float] = mapped_column(Float)
    reserved: Mapped[float] = mapped_column(Float, default=0)
    request: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    error_code: Mapped[str | None] = mapped_column(String(40))
    error_detail: Mapped[str | None] = mapped_column(Text)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class GenerationAttempt(Base):
    __tablename__ = "generation_attempts"
    id: Mapped[str] = pk("att")
    job_id: Mapped[str] = fk("generation_jobs.id", index=True)
    provider_id: Mapped[str] = mapped_column(String(60))
    n: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(30))
    error_code: Mapped[str | None] = mapped_column(String(40))
    detail: Mapped[str | None] = mapped_column(Text)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class GenerationArtifact(Base):
    __tablename__ = "generation_artifacts"
    id: Mapped[str] = pk("art")
    user_id: Mapped[str] = mapped_column(String(40), index=True)
    project_id: Mapped[str] = fk("video_projects.id", index=True)
    scene_id: Mapped[str] = fk("video_scenes.id", index=True)
    job_id: Mapped[str | None] = mapped_column(String(40))
    provider_id: Mapped[str] = mapped_column(String(60))
    kind: Mapped[str] = mapped_column(String(30))  # clip | first_frame | last_frame | export | upload
    path: Mapped[str] = mapped_column(String(500))
    duration: Mapped[float | None] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(20))  # candidate | accepted | rejected
    meta: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ProjectCheckpoint(Base):
    __tablename__ = "project_checkpoints"
    id: Mapped[str] = pk("ckp")
    project_id: Mapped[str] = fk("video_projects.id", index=True)
    label: Mapped[str] = mapped_column(String(120))
    state: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ContextHandoff(Base):
    __tablename__ = "context_handoffs"
    id: Mapped[str] = pk("hof")
    project_id: Mapped[str] = fk("video_projects.id", index=True)
    scene_id: Mapped[str] = mapped_column(String(40))
    reason: Mapped[str] = mapped_column(String(60))
    from_provider: Mapped[str | None] = mapped_column(String(60))
    to_provider: Mapped[str | None] = mapped_column(String(60))
    package: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class QualityReport(Base):
    __tablename__ = "quality_reports"
    id: Mapped[str] = pk("qr")
    artifact_id: Mapped[str] = fk("generation_artifacts.id", index=True)
    scene_id: Mapped[str] = mapped_column(String(40), index=True)
    report: Mapped[dict[str, Any]] = mapped_column(JSON)
    decision: Mapped[str] = mapped_column(String(30))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AudioTrack(Base):
    __tablename__ = "audio_tracks"
    id: Mapped[str] = pk("aud")
    project_id: Mapped[str] = fk("video_projects.id", index=True)
    kind: Mapped[str] = mapped_column(String(30))  # master_narration | music
    path: Mapped[str] = mapped_column(String(500))
    duration: Mapped[float | None] = mapped_column(Float)
    meta: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class NarrationSegment(Base):
    __tablename__ = "narration_segments"
    id: Mapped[str] = pk("nar")
    project_id: Mapped[str] = fk("video_projects.id", index=True)
    scene_id: Mapped[str | None] = mapped_column(String(40))
    idx: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    start_time: Mapped[float] = mapped_column(Float)
    end_time: Mapped[float] = mapped_column(Float)
    alignment: Mapped[str] = mapped_column(String(40))  # user_supplied | even_split | forced_alignment


class MediaTimeline(Base):
    __tablename__ = "media_timeline"
    __table_args__ = (UniqueConstraint("project_id", "scene_id"),)
    id: Mapped[str] = pk("tl")
    project_id: Mapped[str] = fk("video_projects.id", index=True)
    scene_id: Mapped[str] = mapped_column(String(40))
    artifact_id: Mapped[str] = mapped_column(String(40))
    start_time: Mapped[float] = mapped_column(Float)
    duration: Mapped[float] = mapped_column(Float)
    transition: Mapped[str] = mapped_column(String(30), default="cut")


class ExportJob(Base):
    __tablename__ = "export_jobs"
    id: Mapped[str] = pk("exp")
    user_id: Mapped[str] = mapped_column(String(40), index=True)
    project_id: Mapped[str] = fk("video_projects.id", index=True)
    status: Mapped[str] = mapped_column(String(30), default="QUEUED")
    options: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    files: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    report: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class WorkflowEvent(Base):
    __tablename__ = "workflow_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[str] = mapped_column(String(40), index=True)
    scene_id: Mapped[str | None] = mapped_column(String(40))
    kind: Mapped[str] = mapped_column(String(40))
    message: Mapped[str] = mapped_column(Text)
    data: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str | None] = mapped_column(String(40))
    action: Mapped[str] = mapped_column(String(80))
    detail: Mapped[str | None] = mapped_column(Text)
    ip: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class MediaBlob(Base):
    """Durable copy of a generated/uploaded media file, so ephemeral-disk hosts can resume after restart.
    Rows are keyed by the file's path under the media root; only the owner's own projects ever write them."""
    __tablename__ = "media_blobs"
    path: Mapped[str] = mapped_column(String(600), primary_key=True)
    size: Mapped[int] = mapped_column(Integer, default=0)
    data: Mapped[bytes] = mapped_column(LargeBinary)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
