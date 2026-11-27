"""Cross-model context transfer (section 14)."""

from __future__ import annotations

import json

from app.continuity.handoff import build_handoff


def test_handoff_contains_required_fields():
    pkg = build_handoff(
        project_id="p1",
        scene_id="scene_004",
        scene_description="Robot continues walking",
        previous_provider="mock_a",
        handoff_reason="quota_exhausted",
        remaining_project_seconds=20.0,
        requested_clip_seconds=4.0,
        previous_frame="scene_003_last.png",
        character_references=["front.png", "side.png"],
        motion="Continue walking forward",
        camera="Smooth side-tracking shot",
        lighting="Warm afternoon light",
        style="Soft colorful 3D educational animation",
    )
    data = json.loads(pkg.to_json())
    assert data["scene_id"] == "scene_004"
    assert data["handoff_reason"] == "quota_exhausted"
    assert data["previous_provider"] == "mock_a"
    assert data["remaining_project_seconds"] == 20.0
    assert data["requested_clip_seconds"] == 4.0
    cont = data["continuity"]
    assert cont["previous_frame"] == "scene_003_last.png"
    assert cont["character_references"] == ["front.png", "side.png"]
    assert cont["motion"] == "Continue walking forward"
    assert cont["camera"] == "Smooth side-tracking shot"


def test_handoff_excludes_non_transferable_state():
    """Neural latents, hidden memory and seeds must not be in the package."""
    pkg = build_handoff(
        project_id="p1",
        scene_id="s",
        scene_description="d",
        previous_provider="a",
        handoff_reason="timeout",
        remaining_project_seconds=1.0,
        requested_clip_seconds=1.0,
        previous_frame=None,
        character_references=[],
    )
    data = json.loads(pkg.to_json())
    flat = json.dumps(data).lower()
    for forbidden in ("latent", "hidden_memory", "seed"):
        assert forbidden not in flat
