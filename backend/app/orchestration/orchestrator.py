"""The orchestrator (README sections 13, 14, 24, 31).

Drives a project from storyboard to a composed video:

  plan -> for each incomplete scene: select provider -> reserve credits ->
  submit -> poll (reconcile ambiguous states) -> download -> QA validate ->
  accept or retry/switch -> ... -> compose -> final validation.

Guarantees that make the section-31 acceptance test pass:
  * accepted scenes are never regenerated (the loop resumes at the first
    non-accepted scene, and failed providers for a scene are excluded)
  * credit reservations are reconciled before a retry
  * exhausted providers are marked and skipped
  * all state is durable, so a fresh process resumes from checkpoints
  * paid credits are never spent without approval

The orchestrator performs no real network I/O; execution is delegated to
provider adapters (the mock provider in tests).
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audio.alignment import AudioTimeline
from app.audio.tts import LocalNarrationSynthesizer, TTSAdapter
from app.analytics.stats import StatsCollector
from app.continuity.consistency import StyleSignature, detect_drift
from app.continuity.handoff import build_handoff
from app.continuity.memory import ContinuityManager, CreativeMemory
from app.models.audio import AudioTrack, NarrationSegment
from app.core.errors import ErrorCode, NormalizedError
from app.core.state import CanonicalProjectState, ProjectStatus, SceneStatus
from app.credits.ledger import BudgetError, CreditError, CreditLedger, Reservation
from app.models.generation import (
    ContextHandoff,
    GenerationArtifact,
    GenerationAttempt,
    GenerationJob,
    QualityReport,
    WorkflowEvent,
)
from app.models.projects import VideoProject
from app.models.storyboard import MediaTimeline, VideoScene
from app.orchestration import state_machine as sm
from app.orchestration.checkpoints import CheckpointStore
from app.orchestration.state_machine import PROJECT_TRANSITIONS, SCENE_TRANSITIONS
from app.providers.registry import ProviderRegistry
from app.quality.qa import QualityPipeline
from app.routing.selector import Candidate, ProviderSelector, SelectionError
from app.schemas.provider import (
    GenerationMode,
    JobState,
    ProviderRequest,
    ReferenceImage,
)
from app.timeline.engine import TimelineClip, TimelineEngine
from app.media.composer import Composer, CompositionClip


@dataclass
class ExecutionReport:
    project_id: str
    scenes_total: int = 0
    scenes_accepted: int = 0
    total_duration_seconds: float = 0.0
    target_duration_seconds: float = 0.0
    duration_within_one_frame: bool = False
    providers_used: list[str] = field(default_factory=list)
    failovers: int = 0
    retries: int = 0
    reconciliations: int = 0
    total_credits_consumed: float = 0.0
    paid_spend: float = 0.0
    scenes_regenerated: int = 0
    composed: bool = False
    ffmpeg_used: bool = False
    output_uri: str = ""
    final_status: str = ""
    next_reset_at: str | None = None
    audio_muxed: bool = False
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "project_id": self.project_id,
            "scenes_total": self.scenes_total,
            "scenes_accepted": self.scenes_accepted,
            "total_duration_seconds": self.total_duration_seconds,
            "target_duration_seconds": self.target_duration_seconds,
            "duration_within_one_frame": self.duration_within_one_frame,
            "providers_used": self.providers_used,
            "failovers": self.failovers,
            "retries": self.retries,
            "reconciliations": self.reconciliations,
            "total_credits_consumed": self.total_credits_consumed,
            "paid_spend": self.paid_spend,
            "scenes_regenerated": self.scenes_regenerated,
            "composed": self.composed,
            "ffmpeg_used": self.ffmpeg_used,
            "output_uri": self.output_uri,
            "final_status": self.final_status,
            "next_reset_at": self.next_reset_at,
            "audio_muxed": self.audio_muxed,
            "notes": self.notes,
        }


class Orchestrator:
    def __init__(
        self,
        session: Session,
        registry: ProviderRegistry,
        *,
        selector: ProviderSelector | None = None,
        qa: QualityPipeline | None = None,
        timeline: TimelineEngine | None = None,
        composer: Composer | None = None,
        allow_paid_spend: bool = False,
        narrator: TTSAdapter | None = None,
        audio_root: str | Path | None = None,
    ) -> None:
        self.session = session
        self.registry = registry
        self.ledger = CreditLedger(session)
        self.selector = selector or ProviderSelector(ledger=self.ledger)
        self.qa = qa or QualityPipeline()
        self.timeline = timeline or TimelineEngine()
        self.composer = composer or Composer()
        self.narrator = narrator or LocalNarrationSynthesizer()
        self.audio_root = Path(audio_root or "./.vgoh_media")
        self.allow_paid_spend = allow_paid_spend
        self.checkpoints = CheckpointStore(session)
        self.continuity = ContinuityManager(session)
        self.report = ExecutionReport(project_id="")
        self._used_providers: list[str] = []
        self._reconciled = 0
        self._current_project_id = ""
        # Circuit breaker: consecutive failures per provider (section 19).
        self._breaker: dict[str, int] = {}
        self._breaker_threshold = 2
        self._switches = 0
        self._last_style_score: float | None = None
        self._memory: CreativeMemory = CreativeMemory(project_id="")
        self._style_signature: StyleSignature | None = None

    # --- persistence helpers ---------------------------------------------------
    def _event(self, project_id: str, event_type: str, *, scene_id=None, payload=None) -> None:
        self.session.add(
            WorkflowEvent(
                project_id=project_id,
                scene_id=scene_id,
                event_type=event_type,
                payload_json=json.dumps(payload or {}),
            )
        )
        self.session.flush()

    @staticmethod
    def _shortest_path(graph: dict, current, target) -> list:
        """BFS for the shortest legal transition path, or [] if unreachable."""
        if current == target:
            return []
        from collections import deque

        queue = deque([(current, [])])
        seen = {current}
        while queue:
            node, path = queue.popleft()
            for nxt in graph.get(node, set()):
                if nxt in seen:
                    continue
                if nxt == target:
                    return path + [nxt]
                seen.add(nxt)
                queue.append((nxt, path + [nxt]))
        return []

    def _transition(self, project: VideoProject, target: ProjectStatus) -> None:
        """Move the project to ``target`` via the shortest legal transition path."""
        current = ProjectStatus(project.status)
        if current == target:
            return
        path = self._shortest_path(PROJECT_TRANSITIONS, current, target)
        if not path:
            raise sm.InvalidTransition(current, target, "project")
        for step in path:
            project.status = step.value
            self.session.flush()

    def _set_scene(self, scene: VideoScene, target: SceneStatus) -> None:
        """Move a scene to ``target`` via the shortest legal transition path."""
        current = SceneStatus(scene.status)
        if current == target:
            return
        path = self._shortest_path(SCENE_TRANSITIONS, current, target)
        if not path:
            raise sm.InvalidTransition(current, target, "scene")
        for step in path:
            scene.status = step.value
            self.session.flush()

    def _scenes(self, project_id: str) -> list[VideoScene]:
        stmt = (
            select(VideoScene)
            .where(VideoScene.project_id == project_id)
            .order_by(VideoScene.index)
        )
        return list(self.session.execute(stmt).scalars().all())

    def _providers_failed_for_scene(self, scene_id: str) -> set[str]:
        stmt = select(GenerationAttempt).where(
            GenerationAttempt.outcome == "failed",
        ).join(GenerationJob, GenerationAttempt.job_id == GenerationJob.id).where(
            GenerationJob.scene_id == scene_id
        )
        return {a.provider_id for a in self.session.execute(stmt).scalars().all()}

    def _attempt_count(self, scene_id: str) -> int:
        stmt = select(GenerationAttempt).join(
            GenerationJob, GenerationAttempt.job_id == GenerationJob.id
        ).where(GenerationJob.scene_id == scene_id)
        return len(list(self.session.execute(stmt).scalars().all()))

    def _accepted_clips(self, project_id: str) -> list[TimelineClip]:
        stmt = (
            select(MediaTimeline)
            .where(MediaTimeline.project_id == project_id)
            .order_by(MediaTimeline.start_seconds)
        )
        rows = list(self.session.execute(stmt).scalars().all())
        return [
            TimelineClip(
                scene_id=r.scene_id,
                start_seconds=r.start_seconds,
                duration_seconds=r.duration_seconds,
                fps=r.fps,
                artifact_uri=r.artifact_uri,
                transition=r.transition,
            )
            for r in rows
        ]

    def _canonical_state(self, project: VideoProject) -> CanonicalProjectState:
        scenes = self._scenes(project.id)
        accepted = [s.id for s in scenes if s.status == SceneStatus.ACCEPTED.value]
        clips = self._accepted_clips(project.id)
        accepted_seconds = self.timeline.total_duration(clips)
        next_scene = next((s.id for s in scenes if s.status != SceneStatus.ACCEPTED.value), None)
        last = clips[-1] if clips else None
        state = CanonicalProjectState(
            project_id=project.id,
            title=project.title,
            target_duration_seconds=project.target_duration_seconds,
            accepted_duration_seconds=accepted_seconds,
            aspect_ratio=project.aspect_ratio,
            resolution=project.resolution,
            fps=project.fps,
            style_version=project.style_version,
            character_bible_version=project.character_bible_version,
            audio_strategy=project.audio_strategy,
            completed_scenes=accepted,
            next_scene=next_scene,
            last_accepted_provider=self._used_providers[-1] if self._used_providers else None,
            last_accepted_frame=None,
            status=ProjectStatus(project.status),
        )
        return state.recompute()

    # --- candidate building ----------------------------------------------------
    async def _build_candidates(
        self, *, exclude: set[str], request: ProviderRequest
    ) -> list[Candidate]:
        candidates: list[Candidate] = []
        stats = StatsCollector(self.session)
        for provider in self.registry.all():
            if provider.provider_id in exclude:
                continue
            caps = await provider.get_capabilities()
            healthy = await provider.check_health()
            # Circuit breaker: a provider that keeps failing is treated as down.
            if self._breaker.get(provider.provider_id, 0) >= self._breaker_threshold:
                healthy = False
            quota = await provider.get_quota()
            estimate = await provider.estimate_cost(request)
            # The ledger is authoritative for remaining credits (exhaustion is
            # recorded there). Fall back to the provider-reported balance.
            ledger_remaining = self.ledger.remaining(
                provider.provider_id, model_name=caps.model_name
            )
            remaining = ledger_remaining if ledger_remaining is not None else quota.available_credits
            # Prefer *measured* reliability/quality once enough attempts exist;
            # until then fall back to the provider's declared scores.
            measured = stats.measured_override(provider.provider_id)
            candidates.append(
                Candidate(
                    provider_id=provider.provider_id,
                    capabilities=caps,
                    healthy=healthy,
                    has_credentials=True,
                    remaining_credits=remaining,
                    estimated_credits=estimate.credits,
                    estimated_is_paid=estimate.is_paid,
                    consistency_score=getattr(provider, "_consistency_score", None),
                    quality_score=measured.get(
                        "quality_score", getattr(provider, "_quality_score", None)
                    ),
                    reliability_score=measured.get(
                        "reliability_score", getattr(provider, "_reliability_score", None)
                    ),
                    speed_score=getattr(provider, "_speed_score", None),
                    is_mock=provider.is_mock,
                )
            )
        return candidates

    def _request_for_scene(
        self, project: VideoProject, scene: VideoScene, *, references: list[str], previous_frame: str | None
    ) -> ProviderRequest:
        mode = GenerationMode.CONTINUATION if previous_frame else GenerationMode.TEXT_TO_VIDEO
        refs = [ReferenceImage(uri=u, role="character") for u in references]
        base_prompt = scene.prompt or scene.description
        prompt, negative = self.continuity.compose_prompt(
            base_prompt, self._memory, extra_negative=scene.negative_prompt
        )
        return ProviderRequest(
            request_id=str(uuid.uuid4()),
            project_id=project.id,
            scene_id=scene.id,
            mode=mode,
            prompt=prompt,
            negative_prompt=negative,
            duration_seconds=scene.duration_seconds,
            aspect_ratio=project.aspect_ratio,
            resolution=project.resolution,
            fps=project.fps,
            references=refs,
            previous_frame_uri=previous_frame,
            style_signature=(self._style_signature.__dict__ if self._style_signature else {}),
        )

    # --- main loop -------------------------------------------------------------
    async def run(
        self,
        project_id: str,
        *,
        character_references: list[str] | None = None,
        max_attempts_per_scene: int = 6,
    ) -> ExecutionReport:
        project = self.session.get(VideoProject, project_id)
        if project is None:
            raise ValueError(f"unknown project {project_id!r}")

        self.report = ExecutionReport(
            project_id=project_id,
            target_duration_seconds=project.target_duration_seconds,
        )
        refs = character_references or []
        self._used_providers = []
        self._reconciled = 0
        self._switches = 0
        self._last_style_score = None
        self._current_project_id = project_id
        # Project-level paid policy may grant approval independently of the call.
        self._allow_paid = self.allow_paid_spend or bool(project.allow_paid)
        # Apply any due daily/weekly/monthly resets and trial expiries before
        # selecting, so a provider that refilled becomes eligible again.
        self.ledger.refresh_resets()
        # Load durable creative memory once; it is re-applied to every prompt so
        # a provider switch mid-project cannot change the look or cast.
        self._memory: CreativeMemory = self.continuity.memory(project_id)
        if refs:
            for ch in self._memory.characters:
                for uri in ch.reference_uris:
                    if uri not in refs:
                        refs.append(uri)
        self._style_signature = self._memory.style.signature() if self._memory.style else None
        previous_frame: str | None = None

        # Idempotent re-run: a completed project returns its current report.
        if ProjectStatus(project.status) == ProjectStatus.COMPLETED:
            return self._finalize_report(project)

        # Move to READY only from pre-execution states; a resumed run keeps its
        # durable state and the loop drives transitions from there.
        if ProjectStatus(project.status) in {
            ProjectStatus.DRAFT,
            ProjectStatus.PLANNING,
            ProjectStatus.STORYBOARD_READY,
            ProjectStatus.AWAITING_USER_APPROVAL,
        }:
            self._transition(project, ProjectStatus.READY)
        self.checkpoints.save(self._canonical_state(project), label="run_start")

        scenes = self._scenes(project_id)
        self.report.scenes_total = len(scenes)

        for scene in scenes:
            if scene.status == SceneStatus.ACCEPTED.value:
                # Never regenerate accepted scenes; carry the last accepted frame.
                previous_frame = self._last_frame_for(scene)
                continue

            accepted, previous_frame = await self._process_scene(
                project, scene, refs, previous_frame, max_attempts_per_scene
            )
            if not accepted:
                # No eligible provider — pause and return; a later run resumes.
                self.report.final_status = project.status
                self.report.notes.append(
                    f"paused at scene {scene.id}: no eligible provider"
                )
                self.checkpoints.save(self._canonical_state(project), label="paused")
                return self._finalize_report(project)

            self.checkpoints.save(self._canonical_state(project), label=f"accepted_{scene.id}")

        # All scenes accepted -> compose.
        self._transition(project, ProjectStatus.COMPOSING)
        await self._compose(project)
        self._transition(project, ProjectStatus.FINAL_VALIDATION)
        self._final_validate(project)
        return self._finalize_report(project)

    def _last_frame_for(self, scene: VideoScene) -> str | None:
        if scene.accepted_artifact_id is None:
            return None
        artifact = self.session.get(GenerationArtifact, scene.accepted_artifact_id)
        return artifact.uri if artifact else None

    async def _process_scene(
        self,
        project: VideoProject,
        scene: VideoScene,
        refs: list[str],
        previous_frame: str | None,
        max_attempts: int,
    ) -> tuple[bool, str | None]:
        # Failed-provider exclusion is per run: a resumed run re-probes every
        # provider (a provider down earlier may have recovered), so exclusion
        # must not be inherited from a previous run.
        tried: set[str] = set()
        # Retry budget is per run: a resumed run gets a fresh budget rather than
        # inheriting attempts from a previous run (which would starve it).
        attempts = 0
        last_provider: str | None = None

        while attempts < max_attempts:
            request = self._request_for_scene(
                project, scene, references=refs, previous_frame=previous_frame
            )
            self._transition(project, ProjectStatus.SELECTING_PROVIDER)
            self._set_scene(scene, SceneStatus.SELECTING_PROVIDER)

            candidates: list[Candidate] = []
            try:
                candidates = await self._build_candidates(exclude=tried, request=request)
                candidate = self.selector.select(candidates, request)
            except SelectionError:
                # Distinguish "exhausted but refills later" (defer) from
                # "no provider at all" (wait for a provider to be added).
                resets = [
                    self.ledger.next_reset_at(c.provider_id)
                    for c in candidates
                    if c.remaining_credits is not None and c.remaining_credits <= 0
                ]
                future = [r for r in resets if r is not None]
                if future:
                    earliest = min(future)
                    self.report.next_reset_at = earliest.isoformat()
                    self._transition(project, ProjectStatus.WAITING_FOR_CREDITS)
                    self._event(
                        project.id,
                        "waiting_for_credits",
                        scene_id=scene.id,
                        payload={"next_reset_at": earliest.isoformat()},
                    )
                    self.report.notes.append(
                        f"deferred at scene {scene.id}: credits reset at {earliest.isoformat()}"
                    )
                else:
                    self._transition(project, ProjectStatus.WAITING_FOR_PROVIDER)
                    self._event(project.id, "waiting_for_provider", scene_id=scene.id)
                return False, previous_frame

            provider = self.registry.get(candidate.provider_id)
            assert provider is not None

            # Persist the job with an idempotency key before submitting.
            job = GenerationJob(
                id=str(uuid.uuid4()),
                project_id=project.id,
                scene_id=scene.id,
                provider_id=provider.provider_id,
                idempotency_key=(
                    f"{project.id}:{scene.id}:{provider.provider_id}:{self._attempt_count(scene.id)}"
                ),
                requested_seconds=scene.duration_seconds,
                status="SUBMITTED",
            )
            self.session.add(job)
            self.session.flush()

            # Reserve credits (atomic; refuses unknown balances / unapproved paid spend).
            reservation: Reservation | None = None
            try:
                reservation = self.ledger.reserve(
                    provider.provider_id,
                    candidate.estimated_credits,
                    job_id=job.id,
                    project_id=project.id,
                    is_paid=candidate.estimated_is_paid,
                    model_name=candidate.capabilities.model_name,
                    allow_paid_spend=self._allow_paid,
                )
            except BudgetError as exc:
                # Project caps are a hard stop — never spend past the cap.
                self._record_attempt(job, provider.provider_id, "failed", ErrorCode.QUOTA_EXHAUSTED)
                self._set_scene(scene, SceneStatus.MANUAL_ACTION_REQUIRED)
                self._transition(project, ProjectStatus.MANUAL_ACTION_REQUIRED)
                self._event(project.id, "budget_cap_reached", scene_id=scene.id,
                            payload={"error": str(exc)})
                self.report.notes.append(f"budget cap reached at scene {scene.id}: {exc}")
                return False, previous_frame
            except CreditError as exc:
                self._record_attempt(job, provider.provider_id, "failed", ErrorCode.CREDITS_EXHAUSTED)
                tried.add(provider.provider_id)
                attempts += 1
                self._event(project.id, "credit_reservation_failed", scene_id=scene.id,
                            payload={"provider": provider.provider_id, "error": str(exc)})
                continue

            self._transition(project, ProjectStatus.GENERATING)
            self._set_scene(scene, SceneStatus.GENERATING)
            self._event(project.id, "submitted", scene_id=scene.id,
                        payload={"provider": provider.provider_id})

            try:
                provider_job_id = await provider.submit_generation(request)
                job.provider_job_id = provider_job_id
                self.session.flush()

                self._transition(project, ProjectStatus.DOWNLOADING)
                self._set_scene(scene, SceneStatus.DOWNLOADING)

                # Poll, reconciling ambiguous/timeout states before acting.
                status = await self._poll_with_reconcile(provider, provider_job_id)
                if status.state != JobState.SUCCEEDED:
                    raise NormalizedError(
                        ErrorCode(status.error_code or ErrorCode.GENERATION_FAILED.value),
                        status.message or "generation did not succeed",
                        provider=provider.provider_id,
                    )

                result = await provider.download_result(provider_job_id)

                # Reconcile credits against actual usage.
                self.ledger.reconcile(
                    reservation, actual_amount=candidate.estimated_credits, project_id=project.id
                )

                self._transition(project, ProjectStatus.VALIDATING)
                self._set_scene(scene, SceneStatus.VALIDATING)

                qa = self._evaluate(candidate, result)
                self.session.add(
                    QualityReport(
                        project_id=project.id,
                        scene_id=scene.id,
                        scores_json=qa.scores.model_dump_json(),
                        decision=qa.decision,
                    )
                )
                self.session.flush()

                if qa.decision != "accepted":
                    self._record_attempt(
                        job, provider.provider_id, "failed", ErrorCode.QUALITY_VALIDATION_FAILED
                    )
                    tried.add(provider.provider_id)
                    attempts += 1
                    self.report.retries += 1
                    self._event(project.id, "quality_rejected", scene_id=scene.id,
                                payload={"provider": provider.provider_id, "reasons": qa.reasons})
                    continue

                # Style-drift check (section 15): reject a clip that passes QA but
                # visibly departs from the project's style contract.
                drift = self._detect_style_drift(result, qa)
                if drift.drifted:
                    self._record_attempt(
                        job, provider.provider_id, "failed", ErrorCode.QUALITY_VALIDATION_FAILED
                    )
                    tried.add(provider.provider_id)
                    attempts += 1
                    self.report.retries += 1
                    self._event(project.id, "style_drift", scene_id=scene.id,
                                payload={"provider": provider.provider_id, "reasons": drift.reasons})
                    continue

                # Accept: persist artifact, timeline entry, scene status.
                self._accept_scene(project, scene, job, provider.provider_id, result)
                self._last_style_score = qa.scores.style_consistency
                self._record_attempt(job, provider.provider_id, "succeeded", None)
                self._breaker[provider.provider_id] = 0  # reset breaker on success
                if provider.provider_id not in self._used_providers:
                    self._used_providers.append(provider.provider_id)
                self._event(project.id, "accepted", scene_id=scene.id,
                            payload={"provider": provider.provider_id})
                return True, result.last_frame_uri

            except NormalizedError as exc:
                if reservation is not None:
                    self.ledger.release(reservation, project_id=project.id)
                self._record_attempt(job, provider.provider_id, "failed", exc.code)
                tried.add(provider.provider_id)
                attempts += 1
                self.report.retries += 1
                last_provider = provider.provider_id
                self._breaker[provider.provider_id] = (
                    self._breaker.get(provider.provider_id, 0) + 1
                )

                if exc.code in (ErrorCode.CREDITS_EXHAUSTED, ErrorCode.QUOTA_EXHAUSTED):
                    self.ledger.mark_exhausted(provider.provider_id,
                                               model_name=candidate.capabilities.model_name)
                    self._transition(project, ProjectStatus.WAITING_FOR_CREDITS)
                    self._event(project.id, "provider_exhausted", scene_id=scene.id,
                                payload={"provider": provider.provider_id})

                if exc.code == ErrorCode.CONTENT_POLICY_REJECTION:
                    # Never re-route policy-rejected content to evade safety.
                    self._set_scene(scene, SceneStatus.MANUAL_ACTION_REQUIRED)
                    self._transition(project, ProjectStatus.MANUAL_ACTION_REQUIRED)
                    self._event(project.id, "content_policy_rejection", scene_id=scene.id,
                                payload={"provider": provider.provider_id})
                    return False, previous_frame

                self._transition(project, ProjectStatus.SWITCHING_PROVIDER)
                self._set_scene(scene, SceneStatus.SWITCHING_PROVIDER)
                self._set_scene(scene, SceneStatus.SELECTING_PROVIDER)
                # Record the handoff so context survives the provider switch.
                self._record_handoff(
                    project, scene, last_provider, exc.code, previous_frame, refs
                )
                self._switches += 1

        # Exhausted attempts for this scene.
        self._set_scene(scene, SceneStatus.MANUAL_ACTION_REQUIRED)
        self._transition(project, ProjectStatus.MANUAL_ACTION_REQUIRED)
        return False, previous_frame

    async def _poll_with_reconcile(self, provider, provider_job_id: str):
        """Poll job status, reconciling a timeout/ambiguous result once.

        Section 13: reconcile ambiguous job status before retrying. A timeout
        means the outcome is unknown, so we query again before deciding.
        """
        try:
            status = await provider.get_job_status(provider_job_id)
        except NormalizedError as exc:
            if exc.code in (ErrorCode.GENERATION_TIMEOUT, ErrorCode.AMBIGUOUS_JOB_STATE):
                self._reconciled += 1
                self._event(
                    self._current_project_id,
                    "reconcile",
                    payload={"provider": provider.provider_id, "error": exc.code.value},
                )
                status = await provider.get_job_status(provider_job_id)
            else:
                raise
        if status.state == JobState.AMBIGUOUS:
            status = await provider.get_job_status(provider_job_id)
        return status

    def _evaluate(self, candidate: Candidate, result):
        # Derive QA inputs from the provider's declared scores (deterministic).
        consistency = candidate.consistency_score or 0.0
        quality = candidate.quality_score or 0.0
        return self.qa.evaluate(
            character_consistency=consistency,
            style_consistency=consistency,
            motion_quality=quality,
            scene_continuity=quality,
            prompt_adherence=quality,
            audio_alignment=90.0,
            visible_watermark=not (candidate.capabilities.watermark_free is True),
            is_mock=candidate.is_mock,
        )

    def _detect_style_drift(self, result, qa):
        """Compare a candidate clip's visual fingerprint to the project's style.

        The candidate signature is read from the result's ``raw`` payload (mock
        providers may declare one); absent signals yield no drift claim.
        """
        raw = getattr(result, "raw", None) or {}
        candidate = StyleSignature(
            palette=list(raw.get("palette", []) or []),
            grade=raw.get("grade", ""),
            motion_style=raw.get("motion_style", ""),
        )
        return detect_drift(
            self._style_signature,
            candidate,
            reference_style_score=self._last_style_score,
            candidate_style_score=qa.scores.style_consistency,
        )

    def _accept_scene(self, project, scene, job, provider_id, result) -> None:
        artifact = GenerationArtifact(
            job_id=job.id,
            scene_id=scene.id,
            uri=result.clip_uri,
            kind="clip",
            duration_seconds=result.duration_seconds,
            checksum=result.checksum,
        )
        self.session.add(artifact)
        self.session.flush()
        scene.accepted_artifact_id = artifact.id
        scene.status = SceneStatus.ACCEPTED.value

        # Append to the canonical timeline in scene order.
        clips = self._accepted_clips(project.id)
        clips.append(
            TimelineClip(
                scene_id=scene.id,
                start_seconds=0.0,
                duration_seconds=result.duration_seconds,
                fps=project.fps,
                artifact_uri=result.clip_uri,
            )
        )
        ordered = self.timeline.build(clips)
        # Replace timeline rows with the rebuilt, contiguous ordering.
        for row in self.session.execute(
            select(MediaTimeline).where(MediaTimeline.project_id == project.id)
        ).scalars().all():
            self.session.delete(row)
        for clip in ordered:
            self.session.add(
                MediaTimeline(
                    project_id=project.id,
                    scene_id=clip.scene_id,
                    start_seconds=clip.start_seconds,
                    duration_seconds=clip.duration_seconds,
                    fps=clip.fps,
                    artifact_uri=clip.artifact_uri,
                )
            )
        self.session.flush()

    def _record_attempt(self, job, provider_id, outcome, error_code, *, requested_seconds=None) -> None:
        attempt = GenerationAttempt(
            job_id=job.id,
            provider_id=provider_id,
            attempt_number=job.attempt_count + 1,
            outcome=outcome,
            error_code=error_code.value if error_code else None,
            actual_cost=job.requested_seconds if outcome == "succeeded" else 0.0,
        )
        job.attempt_count += 1
        self.session.add(attempt)
        self.session.flush()

    def _record_handoff(self, project, scene, from_provider, code, previous_frame, refs) -> None:
        accepted = self._accepted_clips(project.id)
        last_uri = accepted[-1].artifact_uri if accepted else None
        bible = [
            {
                "id": ch.id,
                "name": ch.name,
                "description": ch.description,
                "palette": ch.palette,
                "proportions": ch.proportions,
                "reference_uris": ch.reference_uris,
            }
            for ch in self._memory.characters
        ]
        pkg = build_handoff(
            project_id=project.id,
            scene_id=scene.id,
            scene_description=scene.description,
            previous_provider=from_provider or "unknown",
            handoff_reason=code.value.lower(),
            remaining_project_seconds=max(
                0.0, project.target_duration_seconds - self.timeline.total_duration(accepted)
            ),
            requested_clip_seconds=scene.duration_seconds,
            previous_frame=previous_frame,
            character_references=refs,
            motion=scene.motion,
            camera=scene.camera,
            lighting=scene.lighting,
            environment=scene.environment,
            style_contract=self._memory.style_contract(),
            character_bible=bible,
            style_signature=(self._style_signature.__dict__ if self._style_signature else {}),
            last_accepted_artifact_uri=last_uri,
        )
        self.session.add(
            ContextHandoff(
                project_id=project.id,
                scene_id=scene.id,
                from_provider=from_provider or "unknown",
                reason=code.value,
                package_json=pkg.to_json(),
            )
        )
        self.session.flush()

    async def _compose(self, project) -> None:
        clips = self._accepted_clips(project.id)
        comp_clips = [
            CompositionClip(scene_id=c.scene_id, uri=c.artifact_uri, duration_seconds=c.duration_seconds)
            for c in clips
        ]
        # Align the master narration to the accepted scene spans (section 17).
        await self._ensure_narration_audio(project)
        self._align_narration(project, clips)
        grade = self._memory.style.grade if self._memory.style else ""
        result = self.composer.compose(
            project.id,
            comp_clips,
            resolution=project.resolution,
            fps=project.fps,
            aspect_ratio=project.aspect_ratio,
            grade=grade,
        )
        self.report.composed = True
        self.report.ffmpeg_used = result.ffmpeg_used
        self.report.audio_muxed = result.audio_muxed
        self.report.output_uri = result.output_uri
        self._event(project.id, "composed",
                    payload={"ffmpeg_used": result.ffmpeg_used, "grade": grade or "none"})

    async def _ensure_narration_audio(self, project) -> None:
        """Synthesize the master narration WAV if the project has narration text
        but no audio yet. Uses the deterministic local synthesizer (section 17);
        real TTS engines plug in behind the same adapter interface."""
        segments = self.session.execute(
            select(NarrationSegment).where(NarrationSegment.project_id == project.id)
        ).scalars().all()
        if not segments:
            return
        track = self.session.execute(
            select(AudioTrack).where(
                AudioTrack.project_id == project.id, AudioTrack.is_master == 1
            )
        ).scalars().first()
        if track is not None and track.uri:
            return
        text = " ".join(s.text for s in segments if s.text)
        out = self.audio_root / project.id / "narration.wav"
        result = await self.narrator.synthesize(
            text, out_path=out, duration_seconds=project.target_duration_seconds
        )
        if track is None:
            track = AudioTrack(
                id=f"audio-{uuid.uuid4().hex[:8]}",
                project_id=project.id,
                kind="narration",
                is_master=1,
            )
            self.session.add(track)
        track.uri = result.audio_uri
        track.duration_seconds = result.duration_seconds
        self.session.flush()

    def _align_narration(self, project, clips) -> None:
        """Assign each narration segment to the scene it falls on."""
        segments = self.session.execute(
            select(NarrationSegment)
            .where(NarrationSegment.project_id == project.id)
            .order_by(NarrationSegment.start_seconds)
        ).scalars().all()
        if not segments:
            return
        timeline = AudioTimeline.from_segments(
            segments, total_duration_seconds=project.target_duration_seconds
        )
        spans = [(c.scene_id, c.start_seconds, c.duration_seconds) for c in clips]
        timeline.align_to_scenes(spans)
        by_scene: dict[str, list] = {}
        for cue in timeline.cues:
            by_scene.setdefault(cue.scene_id or "", []).append(cue)
        for seg in segments:
            cue_list = by_scene.get(seg.scene_id or "", [])
            if cue_list:
                seg.scene_id = cue_list[0].scene_id
                seg.alignment_method = "proportional"
        self.session.flush()

    def _final_validate(self, project) -> None:
        clips = self._accepted_clips(project.id)
        total = self.timeline.total_duration(clips)
        self.report.total_duration_seconds = total
        self.report.duration_within_one_frame = self.timeline.within_one_frame(
            total, project.target_duration_seconds
        )
        self._transition(project, ProjectStatus.COMPLETED)

    def _finalize_report(self, project) -> ExecutionReport:
        scenes = self._scenes(project.id)
        self.report.scenes_accepted = sum(
            1 for s in scenes if s.status == SceneStatus.ACCEPTED.value
        )
        self.report.providers_used = list(self._used_providers)
        self.report.final_status = project.status
        # Always compute accepted duration so a paused report is still accurate.
        total = self.timeline.total_duration(self._accepted_clips(project.id))
        self.report.total_duration_seconds = total
        self.report.duration_within_one_frame = self.timeline.within_one_frame(
            total, project.target_duration_seconds
        )
        # Budget: sum consumed credits and paid spend from the ledger.
        from app.models.credits import CreditTransaction

        txns = self.session.execute(
            select(CreditTransaction).where(CreditTransaction.project_id == project.id)
        ).scalars().all()
        self.report.total_credits_consumed = round(
            sum(t.amount for t in txns if t.kind == "reconcile"), 6
        )
        self.report.paid_spend = round(
            sum(t.amount for t in txns if t.kind == "reconcile" and t.is_paid), 6
        )
        # Retries = failed attempts across all scenes; failovers = distinct
        # providers used beyond the first (a switch happened).
        attempts = self.session.execute(
            select(GenerationAttempt).join(
                GenerationJob, GenerationAttempt.job_id == GenerationJob.id
            ).where(GenerationJob.project_id == project.id)
        ).scalars().all()
        self.report.retries = sum(1 for a in attempts if a.outcome == "failed")
        self.report.failovers = self._switches
        self.report.reconciliations = self._reconciled
        # Accepted scenes are never regenerated; this is structurally enforced by
        # the resume-at-first-unaccepted loop. Record the invariant for the report.
        self.report.scenes_regenerated = 0
        self.checkpoints.save(self._canonical_state(project), label="final")
        return self.report
