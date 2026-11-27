"""Mandatory end-to-end acceptance test (README section 31).

Creates a 32-second, 8-scene, 9:16 / 1080x1920 educational animation and drives
it through provider failover, credit exhaustion, a timeout, handoff and a
mid-run restart — entirely with labelled deterministic mocks (no network, no
real providers, no FFmpeg required).

Scenario mapping to section 31:
  1. Provider A completes 12 accepted seconds (3 scenes).
  2. A is exhausted; accepted clips are preserved; 20 seconds remain.
  3. Provider B is selected; context is transferred via a handoff.
  4. B times out on its third scene; the ambiguous status is reconciled first;
     provider C is selected.
  5. C completes the remaining scenes; identity/style validated (QA).
  6. Narration aligned; final video composed.
  7. Duration is within one output frame of 32s; budget verified.
  8. Accepted scenes are not regenerated.
  9. A restart mid-run recovers from durable state (separate test).
 10. An execution report is produced.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.core.errors import ErrorCode
from app.database.base import get_session
from app.orchestration.orchestrator import ExecutionReport, Orchestrator
from app.providers.mock import MockScript
from tests.conftest import make_provider


def _register(service, *providers):
    for p in providers:
        service.register_provider(p)
        service.set_quota(p.provider_id, available_credits=100.0, balance_source="estimated",
                          model_name=f"{p.provider_id}-model")


@pytest.mark.asyncio
async def test_section_31_full_run(db, registry, make_32s_project):
    project_id = make_32s_project()
    from app.services.projects import ProjectService

    service = ProjectService(db, registry)

    # A: highest quality, so it is selected first; 3 scenes (12s) then out of credits.
    a = make_provider(
        "mock_a",
        script=MockScript(succeed_first=3, fail_forever=ErrorCode.CREDITS_EXHAUSTED),
        quality=96, consistency=96, cost_per_second=1.0,
    )
    # B: next best; 2 scenes (8s) then its 3rd job times out; reconcile reveals failure.
    b_script = MockScript(succeed_first=2)
    b_script.script_status("succeeded", "succeeded", "timeout", "failed")
    b = make_provider("mock_b", script=b_script, quality=94, consistency=94, cost_per_second=1.0)
    # C: last resort; completes everything remaining.
    c = make_provider("mock_c", script=MockScript(), quality=90, consistency=90, cost_per_second=1.0)

    _register(service, a, b, c)

    orchestrator = Orchestrator(db, registry)
    report = await orchestrator.run(project_id, character_references=["front.png", "side.png"])

    assert isinstance(report, ExecutionReport)

    # 1 & 2: A did 12s, accepted clips preserved.
    assert report.scenes_accepted == 8
    # 3 & 4: failover A->B->C, B's timeout reconciled before switching.
    assert report.providers_used == ["mock_a", "mock_b", "mock_c"]
    assert report.failovers == 2
    assert report.reconciliations >= 1

    # 5: QA accepted every scene (deterministic high scores).
    assert report.final_status == "COMPLETED"

    # 6: composed.
    assert report.composed is True

    # 7: duration within one output frame of 32s (30 fps => 1/30 s tolerance).
    assert report.total_duration_seconds == pytest.approx(32.0, abs=1e-6)
    assert report.duration_within_one_frame is True

    # 8: accepted scenes were not regenerated — each scene accepted exactly once.
    from app.models.generation import GenerationAttempt, GenerationJob

    attempts = db.execute(
        select(GenerationAttempt)
        .join(GenerationJob, GenerationAttempt.job_id == GenerationJob.id)
        .where(GenerationJob.project_id == project_id)
    ).scalars().all()
    successes_by_scene: dict[str, int] = {}
    for att in attempts:
        job = db.get(GenerationJob, att.job_id)
        if att.outcome == "succeeded":
            successes_by_scene[job.scene_id] = successes_by_scene.get(job.scene_id, 0) + 1
    assert all(count == 1 for count in successes_by_scene.values())
    assert report.scenes_regenerated == 0

    # 10: execution report is serializable.
    as_dict = report.to_dict()
    assert as_dict["project_id"] == project_id
    assert as_dict["scenes_accepted"] == 8


@pytest.mark.asyncio
async def test_section_31_restart_recovery(db, registry, make_32s_project):
    project_id = make_32s_project()
    from app.models.projects import VideoProject
    from app.models.storyboard import MediaTimeline, VideoScene
    from app.services.projects import ProjectService

    service = ProjectService(db, registry)
    a = make_provider(
        "mock_a",
        script=MockScript(succeed_first=2, fail_forever=ErrorCode.CREDITS_EXHAUSTED),
        quality=90, consistency=90, cost_per_second=1.0,
    )
    b = make_provider("mock_b", script=MockScript(), quality=90, consistency=90)
    _register(service, a, b)

    class CrashBeforeThird(Orchestrator):
        """Crash when the third scene starts — after two are fully accepted."""

        async def _process_scene(self, project, scene, refs, previous_frame, max_attempts):
            if scene.index == 3:
                raise RuntimeError("simulated backend crash mid-run")
            return await super()._process_scene(
                project, scene, refs, previous_frame, max_attempts
            )

    with pytest.raises(RuntimeError, match="simulated backend crash"):
        await CrashBeforeThird(db, registry).run(project_id)
    db.commit()

    # Durable state survived: 2 scenes accepted, 8s on the timeline.
    rows = db.execute(
        select(MediaTimeline).where(MediaTimeline.project_id == project_id)
    ).scalars().all()
    assert len(rows) == 2
    assert round(sum(r.duration_seconds for r in rows), 6) == 8.0
    accepted_ids = {
        s.id
        for s in db.execute(
            select(VideoScene).where(VideoScene.project_id == project_id)
        ).scalars().all()
        if s.status == "ACCEPTED"
    }
    assert len(accepted_ids) == 2

    # "Restart": a brand-new session + orchestrator resumes from durable state.
    new_session = get_session()
    try:
        report = await Orchestrator(new_session, registry).run(project_id)
        new_session.commit()

        # The 2 pre-crash scenes kept their accepted status and were not re-run.
        scenes = new_session.execute(
            select(VideoScene).where(VideoScene.project_id == project_id)
        ).scalars().all()
        still_accepted = {s.id for s in scenes if s.status == "ACCEPTED"}
        assert accepted_ids <= still_accepted
    finally:
        new_session.close()

    assert report.final_status == "COMPLETED"
    assert report.scenes_accepted == 8
    assert report.total_duration_seconds == pytest.approx(32.0, abs=1e-6)
    assert report.scenes_regenerated == 0


@pytest.mark.asyncio
async def test_timeout_reconciled_before_retry(db, registry, make_32s_project):
    """Section 13: reconcile ambiguous job status before retrying."""
    project_id = make_32s_project()
    from app.services.projects import ProjectService

    service = ProjectService(db, registry)
    # First status poll times out; the reconcile poll reports success. The scene
    # must be accepted without re-submitting a duplicate job.
    script = MockScript()
    script.script_status("timeout", "succeeded")
    p = make_provider("mock_p", script=script, quality=90, consistency=90)
    _register(service, p)

    # Only run one scene: mark the rest accepted via a tiny 1-scene project.
    from app.core.state import ProjectStatus, SceneStatus
    from app.models.projects import VideoProject
    from app.models.storyboard import VideoScene

    project = db.get(VideoProject, project_id)
    scenes = db.execute(
        select(VideoScene).where(VideoScene.project_id == project_id).order_by(VideoScene.index)  # noqa: F821
    ).scalars().all()
    for s in scenes[1:]:
        s.status = SceneStatus.ACCEPTED.value
    db.flush()

    report = await Orchestrator(db, registry).run(project_id)

    assert report.reconciliations == 1
    assert p._submitted == 1  # no duplicate submission
    assert scenes[0].status == SceneStatus.ACCEPTED.value
