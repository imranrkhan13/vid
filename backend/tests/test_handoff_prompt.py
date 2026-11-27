from app.continuity.handoff import provider_prompt


def test_continuity_is_folded_into_text_only_prompt_without_losing_scene_prompt():
    pkg = {"scene_index": 2, "continuity": {"previous_scene_id": "scn_1", "character_positions": {"Mia": "left, holding cup"}, "camera_cut": True},
           "corrective_notes": ["keep the same red dress"]}
    out = provider_prompt(pkg, "A girl drinks water.", 1400)
    assert out.startswith("A girl drinks water.")
    assert "continues directly" in out and "Mia: left, holding cup" in out and "red dress" in out and "camera cut" in out


def test_limit_trims_continuity_first_and_first_scene_unchanged():
    pkg = {"scene_index": 1, "continuity": {"previous_scene_id": "x", "character_positions": {"A": "z" * 500}}, "corrective_notes": []}
    base = "p" * 100
    out = provider_prompt(pkg, base, 150)
    assert out.startswith(base) and len(out) <= 150
    assert provider_prompt({"scene_index": 0, "continuity": {}}, "hello", 100) == "hello"
    assert provider_prompt(pkg, "q" * 200, 150) == "q" * 150
