"""AI Director engine (README section 6).

Turns a creative brief into a storyboard: scene decomposition, per-scene
instructions, prompts, negative prompts and acceptance criteria. Total scene
duration is validated against the target.

This is the provider-independent planning layer. It does not call any provider;
a provider-backed LLM adapter can be plugged in later behind the same interface.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from pydantic import BaseModel, Field


class ScenePlan(BaseModel):
    index: int
    description: str
    duration_seconds: float
    prompt: str
    negative_prompt: str = ""
    camera: str = ""
    motion: str = ""
    lighting: str = ""
    environment: str = ""


class StoryboardPlan(BaseModel):
    title: str
    style_bible: str
    character_bible: str = ""
    scenes: list[ScenePlan] = Field(default_factory=list)
    audio_timeline: list[dict] = Field(default_factory=list)
    acceptance_criteria: list[str] = Field(default_factory=list)


class PlanningError(Exception):
    pass


@dataclass
class DirectorConfig:
    default_scene_seconds: float = 4.0
    min_scene_seconds: float = 2.0
    max_scene_seconds: float = 10.0
    camera_language: list[str] = field(
        default_factory=lambda: [
            "wide establishing shot",
            "smooth side-tracking shot",
            "medium shot",
            "close-up",
            "over-the-shoulder shot",
        ]
    )


class Director:
    """Deterministic planner producing a valid storyboard."""

    def __init__(self, config: DirectorConfig | None = None) -> None:
        self.config = config or DirectorConfig()

    def plan(
        self,
        *,
        title: str,
        concept: str,
        target_duration_seconds: float,
        style: str = "",
        scene_count: int | None = None,
        narration: str = "",
    ) -> StoryboardPlan:
        if target_duration_seconds <= 0:
            raise PlanningError("target duration must be positive")

        count = scene_count or self._default_count(target_duration_seconds)
        per = round(target_duration_seconds / count, 6)
        # Clamp per-scene duration; if clamping changes the total, absorb the
        # remainder in the final scene so the total still matches the target.
        clamped = min(max(per, self.config.min_scene_seconds), self.config.max_scene_seconds)

        scenes: list[ScenePlan] = []
        allocated = 0.0
        for i in range(count):
            duration = clamped
            if i == count - 1:
                duration = round(target_duration_seconds - allocated, 6)
                if duration <= 0:
                    raise PlanningError("scene durations cannot satisfy the target duration")
            allocated = round(allocated + duration, 6)
            scenes.append(
                ScenePlan(
                    index=i + 1,
                    description=f"Scene {i + 1}: {concept}".strip(": "),
                    duration_seconds=duration,
                    prompt=f"{concept} — {style}".strip(" —"),
                    negative_prompt="blurry, distorted faces, extra limbs, watermark, text",
                    camera=self.config.camera_language[i % len(self.config.camera_language)],
                    motion="smooth continuous motion",
                    lighting="consistent with style bible",
                    environment="consistent with style bible",
                )
            )

        total = round(sum(s.duration_seconds for s in scenes), 6)
        if abs(total - target_duration_seconds) > 1e-6:
            raise PlanningError(
                f"scene total {total} does not match target {target_duration_seconds}"
            )

        return StoryboardPlan(
            title=title,
            style_bible=style or "soft colorful 3D educational animation",
            character_bible="stable character identity across scenes",
            scenes=scenes,
            audio_timeline=[{"kind": "narration", "text": narration, "start": 0.0,
                             "end": target_duration_seconds}],
            acceptance_criteria=[
                "character consistency >= threshold",
                "style consistency >= threshold",
                "no visible watermark",
                "total duration within one frame of target",
            ],
        )

    def _default_count(self, target: float) -> int:
        count = max(1, round(target / self.config.default_scene_seconds))
        return count
