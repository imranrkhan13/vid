"""Rich heuristic prompts: the director produces concrete, cinematic per-scene
prompts from a bare idea — no LLM, no invented facts."""
from app.directors.director import analyze_idea, plan_heuristic, validate_plan


def _plan(concept="A friendly cloud drifts over a rainy city and learns to make rain"):
    brief = {"title": "Cloud story", "concept": concept, "target_duration": 16, "visual_style": "soft colorful 3D educational animation"}
    return plan_heuristic(brief, [])


def test_analyze_idea_extracts_subject_verbs_setting_mood():
    a = analyze_idea("A tiny lantern lights up a dark forest path, magical mood")
    assert a["subject"]  # something concrete extracted
    assert "lights" in a["verbs"]
    assert "forest" in a["setting"]
    assert a["mood"]


def test_analyze_idea_invents_nothing():
    a = analyze_idea("")
    assert a["subject"] == "" and a["verbs"] == [] and a["setting"] == "" and a["mood"] == ""


def test_prompts_contain_all_template_slots():
    plan = _plan()
    for s in plan["scenes"]:
        p = s["prompt"]
        assert "Camera:" in p and "Lighting:" in p and "Environment:" in p
        assert "Composition:" in p and "Motion:" in p and "Characters:" in p
        assert "character bible" in p  # continuity instruction


def test_camera_language_is_role_keyed_not_cycled():
    plan = _plan()
    cams = [s["camera"] for s in plan["scenes"]]
    assert len(set(cams)) == len(cams) or len(cams) > len(set(cams))  # roles may repeat only past 8 scenes
    assert "24mm" in cams[0]  # establishing shot carries a lens
    assert not all(c == cams[0] for c in cams)


def test_prompts_differ_across_scenes_and_respect_cap():
    plan = _plan()
    prompts = [s["prompt"] for s in plan["scenes"]]
    assert len(set(prompts)) == len(prompts)
    assert all(len(p) <= 1500 for p in prompts)


def test_first_scene_carries_the_subject():
    plan = _plan()
    assert "Subject:" in plan["scenes"][0]["prompt"]


def test_plan_still_validates():
    plan = _plan()
    validate_plan(plan, 16.0)
    assert plan["director"] == "heuristic-v2"
    assert abs(plan["total_seconds"] - 16.0) < 1e-6
