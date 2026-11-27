"""Continuity & consistency: style sheet, character bible, drift detection."""

from __future__ import annotations

from app.continuity.consistency import StyleSignature, detect_drift, palette_similarity
from app.continuity.memory import ContinuityManager, load_creative_memory
from app.services.projects import ProjectService
from tests.conftest import make_provider


# --- palette similarity ----------------------------------------------------

def test_palette_similarity_identical():
    assert palette_similarity(["#ff0000", "#00ff00"], ["#ff0000"]) == 100.0


def test_palette_similarity_unknown_when_empty():
    assert palette_similarity([], ["#ff0000"]) is None
    assert palette_similarity(["not-a-color"], ["#ff0000"]) is None


def test_palette_similarity_lower_for_distant_colors():
    near = palette_similarity(["#ff0000"], ["#fe0101"])
    far = palette_similarity(["#ff0000"], ["#00ffff"])
    assert near is not None and far is not None
    assert near > far


# --- drift detection -------------------------------------------------------

def test_no_signal_means_no_drift_claim():
    report = detect_drift(None, None)
    assert report.drifted is False
    assert report.reasons == []


def test_palette_drift_detected():
    ref = StyleSignature(palette=["#ff0000", "#00ff00"])
    cand = StyleSignature(palette=["#0000ff", "#00ffff"])
    report = detect_drift(ref, cand)
    assert report.drifted is True
    assert any("palette drift" in r for r in report.reasons)


def test_motion_style_change_detected():
    ref = StyleSignature(motion_style="hand-drawn")
    cand = StyleSignature(motion_style="3d")
    report = detect_drift(ref, cand)
    assert report.drifted is True
    assert any("motion style" in r for r in report.reasons)


def test_style_score_drop_detected():
    report = detect_drift(
        None, None, reference_style_score=90.0, candidate_style_score=60.0
    )
    assert report.drifted is True
    assert any("style score dropped" in r for r in report.reasons)


def test_consistent_scene_not_flagged():
    ref = StyleSignature(palette=["#ff0000"], motion_style="hand-drawn", grade="warm")
    cand = StyleSignature(palette=["#ff0000"], motion_style="hand-drawn", grade="warm")
    report = detect_drift(ref, cand, reference_style_score=90.0, candidate_style_score=88.0)
    assert report.drifted is False


# --- creative memory -------------------------------------------------------

def test_set_style_and_character_round_trip(db, registry, make_32s_project):
    project_id = make_32s_project()
    service = ProjectService(db, registry)
    service.set_style(
        project_id,
        name="Storybook",
        description="soft flat illustration",
        palette=["#ffcc00", "#2244aa"],
        grade="warm",
        motion_style="hand-drawn",
        negative_prompts=["photorealistic"],
    )
    service.add_character(
        project_id,
        name="Robo",
        description="a friendly round robot",
        palette=["#888888"],
        proportions="stubby limbs, big head",
        reference_uris=["file:///refs/robo_front.png"],
    )

    memory = load_creative_memory(db, project_id)
    assert memory.style is not None
    assert memory.style.palette == ["#ffcc00", "#2244aa"]
    assert memory.style.grade == "warm"
    assert memory.style.motion_style == "hand-drawn"
    assert memory.style.negative_prompts == ["photorealistic"]
    assert len(memory.characters) == 1
    assert memory.characters[0].name == "Robo"
    assert memory.characters[0].reference_uris == ["file:///refs/robo_front.png"]

    contract = memory.style_contract()
    assert "STYLE:" in contract
    assert "CHARACTER: Robo" in contract


def test_style_contract_applied_to_prompt(db, registry, make_32s_project):
    project_id = make_32s_project()
    service = ProjectService(db, registry)
    service.set_style(project_id, name="Noir", palette=["#101010"], grade="cool",
                      negative_prompts=["colorful"])
    manager = ContinuityManager(db)
    memory = manager.memory(project_id)
    prompt, negative = manager.compose_prompt("a robot walks", memory, extra_negative="blurry")
    assert "a robot walks" in prompt
    assert "STYLE: Noir" in prompt
    assert "colorful" in negative
    assert "blurry" in negative


def test_empty_memory_leaves_prompt_unchanged(db, registry, make_32s_project):
    project_id = make_32s_project()
    manager = ContinuityManager(db)
    memory = manager.memory(project_id)
    assert memory.is_empty()
    prompt, negative = manager.compose_prompt("plain prompt", memory)
    assert prompt == "plain prompt"
    assert negative == ""


def test_handoff_carries_creative_memory(db, registry, make_32s_project):
    """A provider switch mid-project must carry style + cast (the twist)."""
    import asyncio

    from app.orchestration.orchestrator import Orchestrator
    from app.core.errors import ErrorCode
    from app.providers.mock import MockScript

    project_id = make_32s_project()
    service = ProjectService(db, registry)
    service.set_style(project_id, name="Storybook", palette=["#ffcc00"], grade="warm")
    service.add_character(project_id, name="Robo", description="friendly robot")

    # A completes 2 scenes then exhausts credits; B takes over.
    a = make_provider(
        "mock_a",
        script=MockScript(succeed_first=2, fail_forever=ErrorCode.CREDITS_EXHAUSTED),
        quality=96.0,
        consistency=96.0,
    )
    b = make_provider("mock_b", quality=94.0, consistency=94.0)
    for p in (a, b):
        service.register_provider(p)
        service.set_quota(p.provider_id, available_credits=1000.0, balance_source="estimated",
                          model_name=f"{p.provider_id}-model")

    async def _go():
        report = await Orchestrator(db, registry).run(project_id)
        db.commit()
        return report

    report = asyncio.run(_go())
    assert report.final_status == "COMPLETED"
    assert report.failovers >= 1

    from app.models.generation import ContextHandoff

    handoffs = db.execute(
        __import__("sqlalchemy").select(ContextHandoff).where(
            ContextHandoff.project_id == project_id
        )
    ).scalars().all()
    assert handoffs, "expected a recorded handoff on provider switch"
    import json

    pkg = json.loads(handoffs[0].package_json)
    assert "STYLE: Storybook" in pkg["style_contract"]
    assert any(c["name"] == "Robo" for c in pkg["character_bible"])
