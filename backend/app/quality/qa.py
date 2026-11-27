"""Quality assurance pipeline (README section 18).

Heuristic scoring across the section-18 dimensions. Scores must be calibrated
against approved/rejected clips before being trusted; the thresholds here are
explicit defaults, not claims of accuracy.

Provider success alone is not acceptance. On failure the caller records the
reason, adjusts instructions, checks the retry budget and retries or switches.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from pydantic import BaseModel, Field


class QAScores(BaseModel):
    character_consistency: float = 0.0
    style_consistency: float = 0.0
    motion_quality: float = 0.0
    scene_continuity: float = 0.0
    prompt_adherence: float = 0.0
    audio_alignment: float = 0.0
    visible_watermark_detected: bool = False
    unexpected_text_detected: bool = False
    corrupt_media: bool = False
    resolution_ok: bool = True
    aspect_ratio_ok: bool = True
    duration_ok: bool = True
    safety_ok: bool = True
    decision: str = "pending"


@dataclass
class QAThresholds:
    min_character_consistency: float = 80.0
    min_style_consistency: float = 80.0
    min_motion_quality: float = 70.0
    min_scene_continuity: float = 70.0
    min_prompt_adherence: float = 75.0
    min_audio_alignment: float = 70.0
    reject_visible_watermark: bool = True


@dataclass
class QAResult:
    scores: QAScores
    decision: str
    reasons: list[str] = field(default_factory=list)


class QualityPipeline:
    def __init__(self, thresholds: QAThresholds | None = None) -> None:
        self.thresholds = thresholds or QAThresholds()

    def evaluate(
        self,
        *,
        character_consistency: float,
        style_consistency: float,
        motion_quality: float,
        scene_continuity: float,
        prompt_adherence: float,
        audio_alignment: float = 90.0,
        visible_watermark: bool = False,
        unexpected_text: bool = False,
        corrupt_media: bool = False,
        resolution_ok: bool = True,
        aspect_ratio_ok: bool = True,
        duration_ok: bool = True,
        safety_ok: bool = True,
        is_mock: bool = False,
    ) -> QAResult:
        t = self.thresholds
        reasons: list[str] = []
        scores = QAScores(
            character_consistency=character_consistency,
            style_consistency=style_consistency,
            motion_quality=motion_quality,
            scene_continuity=scene_continuity,
            prompt_adherence=prompt_adherence,
            audio_alignment=audio_alignment,
            visible_watermark_detected=visible_watermark,
            unexpected_text_detected=unexpected_text,
            corrupt_media=corrupt_media,
            resolution_ok=resolution_ok,
            aspect_ratio_ok=aspect_ratio_ok,
            duration_ok=duration_ok,
            safety_ok=safety_ok,
        )

        hard_fail = False
        if corrupt_media:
            reasons.append("corrupt media")
            hard_fail = True
        if t.reject_visible_watermark and visible_watermark:
            reasons.append("visible watermark")
            hard_fail = True
        if not safety_ok:
            reasons.append("safety failure")
            hard_fail = True
        if not (resolution_ok and aspect_ratio_ok and duration_ok):
            reasons.append("technical spec mismatch")
            hard_fail = True

        soft_fail = False
        for value, floor, label in (
            (character_consistency, t.min_character_consistency, "character consistency"),
            (style_consistency, t.min_style_consistency, "style consistency"),
            (motion_quality, t.min_motion_quality, "motion quality"),
            (scene_continuity, t.min_scene_continuity, "scene continuity"),
            (prompt_adherence, t.min_prompt_adherence, "prompt adherence"),
            (audio_alignment, t.min_audio_alignment, "audio alignment"),
        ):
            if value < floor:
                reasons.append(f"{label} {value:.0f} < {floor:.0f}")
                soft_fail = True

        if hard_fail:
            decision = "rejected"
        elif soft_fail:
            decision = "needs_retry"
        else:
            decision = "accepted"

        scores.decision = decision
        return QAResult(scores=scores, decision=decision, reasons=reasons)
