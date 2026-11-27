"""Project service: create projects, plan storyboards, register providers/quotas.

Keeps DB writes in one place so both the API and the demo/tests share behaviour.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.state import AudioStrategy, ProjectStatus, SceneStatus
from app.directors.planner import Director
from app.models.audio import AudioTrack, NarrationSegment
from app.models.projects import (
    Character,
    CharacterReferenceAsset,
    ProjectVersion,
    StyleProfile,
    VideoProject,
)
from app.models.providers import ProviderModel, ProviderQuota
from app.models.storyboard import Storyboard, VideoScene
from app.providers.base import VideoProvider
from app.providers.registry import ProviderRegistry


class ProjectService:
    def __init__(self, session: Session, registry: ProviderRegistry | None = None) -> None:
        self.session = session
        self.registry = registry
        self.director = Director()

    # --- projects --------------------------------------------------------------
    def create_project(
        self,
        *,
        title: str,
        concept: str = "",
        target_duration_seconds: float = 32.0,
        aspect_ratio: str = "9:16",
        resolution: str = "1080x1920",
        fps: int = 30,
        style: str = "",
        max_budget: float | None = None,
    ) -> VideoProject:
        project = VideoProject(
            id=f"video-project-{uuid.uuid4().hex[:8]}",
            title=title,
            concept=concept,
            status=ProjectStatus.DRAFT.value,
            target_duration_seconds=target_duration_seconds,
            aspect_ratio=aspect_ratio,
            resolution=resolution,
            fps=fps,
            audio_strategy=AudioStrategy.EXTERNAL_MASTER_NARRATION.value,
            max_budget=max_budget,
        )
        self.session.add(project)
        self.session.add(
            ProjectVersion(project_id=project.id, version=1, instructions=concept)
        )
        self.session.flush()
        return project

    def register_provider(
        self, provider: VideoProvider, *, category: str | None = None, verified: bool = False
    ) -> ProviderModel:
        row = ProviderModel(
            id=provider.provider_id,
            name=provider.name,
            category=category or provider.category,
            enabled=True,
            is_mock=provider.is_mock,
            verification_date="",
            notes="mock provider" if provider.is_mock else "",
        )
        self.session.merge(row)
        if self.registry is not None:
            self.registry.register(provider)
        self.session.flush()
        return row

    def set_quota(
        self,
        provider_id: str,
        *,
        available_credits: float | None,
        balance_source: str = "estimated",
        model_name: str = "",
        limit_credits: float | None = None,
        period: str = "none",
        confidence: float = 0.5,
        is_paid: bool = False,
        reset_at=None,
        expires_at=None,
        reset_timezone: str = "",
    ) -> ProviderQuota:
        existing = self.session.execute(
            select(ProviderQuota).where(
                ProviderQuota.provider_id == provider_id,
                ProviderQuota.model_name == model_name,
            )
        ).scalars().first()
        if existing is None:
            existing = ProviderQuota(
                provider_id=provider_id, model_name=model_name, period=period
            )
            self.session.add(existing)
        existing.available_credits = available_credits
        existing.balance_source = balance_source
        existing.limit_credits = limit_credits
        existing.confidence = confidence
        existing.period = period
        existing.is_paid = is_paid
        existing.reset_at = reset_at
        existing.expires_at = expires_at
        existing.reset_timezone = reset_timezone
        self.session.flush()
        return existing

    def set_budget_policy(
        self,
        project_id: str,
        *,
        max_budget: float | None = None,
        allow_paid: bool = False,
        max_paid_spend: float | None = None,
    ) -> VideoProject:
        """Set a project's credit/paid-spend caps (section 27)."""
        project = self.session.get(VideoProject, project_id)
        if project is None:
            raise ValueError(f"unknown project {project_id!r}")
        project.max_budget = max_budget
        project.allow_paid = allow_paid
        project.max_paid_spend = max_paid_spend
        self.session.flush()
        return project

    # --- creative memory (sections 7, 15, 16) ---------------------------------
    def set_style(
        self,
        project_id: str,
        *,
        name: str,
        description: str = "",
        palette: list[str] | None = None,
        grade: str = "",
        motion_style: str = "",
        negative_prompts: list[str] | None = None,
    ) -> StyleProfile:
        """Create or replace the project's style sheet (the durable look)."""
        existing = self.session.execute(
            select(StyleProfile).where(StyleProfile.project_id == project_id)
        ).scalars().first()
        if existing is None:
            existing = StyleProfile(
                id=f"style-{uuid.uuid4().hex[:8]}", project_id=project_id, name=name
            )
            self.session.add(existing)
        existing.name = name
        # Structured fields are encoded in the description so the schema stays
        # provider-neutral; load_creative_memory parses them back out.
        parts = [description]
        if palette:
            parts.append("palette: " + ",".join(palette))
        if grade:
            parts.append(f"grade: {grade}")
        if motion_style:
            parts.append(f"motion: {motion_style}")
        if negative_prompts:
            parts.append("negative: " + ",".join(negative_prompts))
        existing.description = "\n".join(p for p in parts if p)
        self.session.flush()
        return existing

    def add_character(
        self,
        project_id: str,
        *,
        name: str,
        description: str = "",
        palette: list[str] | None = None,
        proportions: str = "",
        reference_uris: list[str] | None = None,
    ) -> Character:
        """Add a character to the project's character bible (section 16)."""
        character = Character(
            id=f"char-{uuid.uuid4().hex[:8]}",
            project_id=project_id,
            name=name,
            description=description,
            palette=",".join(palette or []),
            proportions=proportions,
        )
        self.session.add(character)
        self.session.flush()
        for i, uri in enumerate(reference_uris or []):
            self.session.add(
                CharacterReferenceAsset(
                    character_id=character.id,
                    view="front" if i == 0 else "three_quarter",
                    uri=uri,
                )
            )
        self.session.flush()
        return character

    # --- planning --------------------------------------------------------------
    def plan_storyboard(
        self,
        project_id: str,
        *,
        scene_count: int | None = None,
        narration: str = "",
    ) -> Storyboard:
        project = self.session.get(VideoProject, project_id)
        if project is None:
            raise ValueError(f"unknown project {project_id!r}")
        project.status = ProjectStatus.PLANNING.value
        plan = self.director.plan(
            title=project.title,
            concept=project.concept,
            target_duration_seconds=project.target_duration_seconds,
            style="",
            scene_count=scene_count,
            narration=narration,
        )
        storyboard = Storyboard(id=f"sb-{uuid.uuid4().hex[:8]}", project_id=project_id, version=1)
        self.session.add(storyboard)
        self.session.flush()
        for scene_plan in plan.scenes:
            self.session.add(
                VideoScene(
                    id=f"{project_id}-scene-{scene_plan.index:03d}",
                    storyboard_id=storyboard.id,
                    project_id=project_id,
                    index=scene_plan.index,
                    description=scene_plan.description,
                    duration_seconds=scene_plan.duration_seconds,
                    status=SceneStatus.PENDING.value,
                    prompt=scene_plan.prompt,
                    negative_prompt=scene_plan.negative_prompt,
                    camera=scene_plan.camera,
                    motion=scene_plan.motion,
                    lighting=scene_plan.lighting,
                    environment=scene_plan.environment,
                )
            )
        if narration:
            track = AudioTrack(
                id=f"audio-{uuid.uuid4().hex[:8]}",
                project_id=project_id,
                kind="narration",
                duration_seconds=project.target_duration_seconds,
                is_master=1,
            )
            self.session.add(track)
            self.session.add(
                NarrationSegment(
                    project_id=project_id,
                    audio_track_id=track.id,
                    text=narration,
                    start_seconds=0.0,
                    end_seconds=project.target_duration_seconds,
                )
            )
        project.status = ProjectStatus.READY.value
        self.session.flush()
        return storyboard
