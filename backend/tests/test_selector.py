"""Provider selection engine (section 11)."""

from __future__ import annotations

from app.routing.selector import Candidate, ProviderSelector, SelectionError
from app.schemas.provider import GenerationMode, ProviderCapabilities, ProviderRequest


def _caps(**overrides):
    base = dict(
        model_name="m",
        text_to_video=True,
        image_to_video=True,
        video_to_video=True,
        extension=True,
        first_last_frame=True,
        multiple_references=True,
        character_references=True,
        max_duration_seconds=10.0,
        supported_aspect_ratios=["9:16", "16:9"],
        commercial_rights=True,
        watermark_free=True,
    )
    base.update(overrides)
    return ProviderCapabilities(**base)


def _request(**overrides):
    base = dict(
        request_id="r",
        project_id="p",
        scene_id="s",
        mode=GenerationMode.TEXT_TO_VIDEO,
        duration_seconds=4.0,
        aspect_ratio="9:16",
        resolution="1080x1920",
    )
    base.update(overrides)
    return ProviderRequest(**base)


def _candidate(pid, **overrides):
    base = dict(
        provider_id=pid,
        capabilities=_caps(),
        healthy=True,
        has_credentials=True,
        remaining_credits=100.0,
        estimated_credits=0.0,
        estimated_is_paid=False,
        consistency_score=80.0,
        quality_score=80.0,
        reliability_score=90.0,
        speed_score=70.0,
    )
    base.update(overrides)
    return Candidate(**base)


def test_hard_filter_excludes_unhealthy():
    selector = ProviderSelector()
    assert not selector.passes_hard_filters(_candidate("a", healthy=False), _request())


def test_hard_filter_excludes_unknown_capability():
    # Unknown (None) mode support must fail — never assumed present.
    selector = ProviderSelector()
    c = _candidate("a", capabilities=_caps(text_to_video=None))
    assert not selector.passes_hard_filters(c, _request())


def test_hard_filter_excludes_insufficient_credits():
    selector = ProviderSelector()
    c = _candidate("a", remaining_credits=1.0, estimated_credits=5.0)
    assert not selector.passes_hard_filters(c, _request())


def test_hard_filter_excludes_unknown_balance_for_paid():
    selector = ProviderSelector()
    c = _candidate("a", remaining_credits=None, estimated_credits=2.0)
    assert not selector.passes_hard_filters(c, _request())


def test_hard_filter_excludes_unapproved_paid():
    selector = ProviderSelector(allow_paid_spend=False)
    c = _candidate("a", estimated_is_paid=True, estimated_credits=2.0)
    assert not selector.passes_hard_filters(c, _request())


def test_hard_filter_duration_and_aspect():
    selector = ProviderSelector()
    c = _candidate("a")
    assert not selector.passes_hard_filters(c, _request(duration_seconds=20.0))
    assert not selector.passes_hard_filters(c, _request(aspect_ratio="4:3"))


def test_select_prefers_higher_consistency():
    selector = ProviderSelector()
    low = _candidate("low", consistency_score=60.0, quality_score=60.0)
    high = _candidate("high", consistency_score=95.0, quality_score=95.0)
    chosen = selector.select([low, high], _request())
    assert chosen.provider_id == "high"


def test_never_selects_on_free_credits_alone():
    # Both are free; the one with any quality signal must win over a
    # free-but-unmeasured provider.
    selector = ProviderSelector()
    free_unmeasured = _candidate("free", consistency_score=None, quality_score=None,
                                 reliability_score=None, speed_score=None)
    measured = _candidate("measured", consistency_score=70.0, quality_score=70.0)
    chosen = selector.select([free_unmeasured, measured], _request())
    assert chosen.provider_id == "measured"


def test_no_eligible_raises():
    selector = ProviderSelector()
    with __import__("pytest").raises(SelectionError):
        selector.select([_candidate("a", healthy=False)], _request())
