"""Timeline duration math and QA decisions (sections 9, 18)."""

from __future__ import annotations

import pytest

from app.quality.qa import QualityPipeline
from app.timeline.engine import TimelineClip, TimelineEngine, TimelineError


def test_timeline_contiguous_and_total():
    engine = TimelineEngine(fps=30)
    clips = [
        TimelineClip("s1", 0.0, 4.0),
        TimelineClip("s2", 0.0, 4.0),
        TimelineClip("s3", 0.0, 4.0),
    ]
    ordered = engine.build(clips)
    assert [c.start_seconds for c in ordered] == [0.0, 4.0, 8.0]
    assert engine.total_duration(ordered) == 12.0


def test_within_one_frame():
    engine = TimelineEngine(fps=30)
    assert engine.within_one_frame(32.0, 32.0)
    assert engine.within_one_frame(32.0 + 1 / 30, 32.0)
    assert not engine.within_one_frame(32.0 + 0.1, 32.0)


def test_non_positive_duration_rejected():
    engine = TimelineEngine()
    with pytest.raises(TimelineError):
        engine.build([TimelineClip("s1", 0.0, 0.0)])


def test_qa_accepts_good_clip():
    qa = QualityPipeline()
    result = qa.evaluate(
        character_consistency=91,
        style_consistency=94,
        motion_quality=87,
        scene_continuity=92,
        prompt_adherence=95,
        audio_alignment=90,
    )
    assert result.decision == "accepted"


def test_qa_rejects_watermark():
    qa = QualityPipeline()
    result = qa.evaluate(
        character_consistency=95,
        style_consistency=95,
        motion_quality=90,
        scene_continuity=90,
        prompt_adherence=95,
        visible_watermark=True,
    )
    assert result.decision == "rejected"


def test_qa_needs_retry_on_low_consistency():
    qa = QualityPipeline()
    result = qa.evaluate(
        character_consistency=50,
        style_consistency=90,
        motion_quality=90,
        scene_continuity=90,
        prompt_adherence=95,
    )
    assert result.decision == "needs_retry"
