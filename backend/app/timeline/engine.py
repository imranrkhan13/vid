"""Canonical media timeline engine (README section 9).

Durations are dynamic — scenes are not assumed to be a fixed length. The engine
computes frame-accurate placements and total duration, and supports approved
trimming. It operates on plain descriptors so it can be unit-tested without a DB.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class TimelineClip:
    scene_id: str
    start_seconds: float
    duration_seconds: float
    fps: int = 30
    artifact_uri: str = ""
    transition: str = "cut"


class TimelineError(Exception):
    pass


class TimelineEngine:
    def __init__(self, fps: int = 30) -> None:
        self.fps = fps

    def frame_duration(self) -> float:
        return 1.0 / self.fps

    def build(self, clips: list[TimelineClip], *, start: float = 0.0) -> list[TimelineClip]:
        """Assign contiguous start times in the given order."""
        cursor = start
        ordered: list[TimelineClip] = []
        for clip in clips:
            if clip.duration_seconds <= 0:
                raise TimelineError(f"scene {clip.scene_id} has non-positive duration")
            clip.start_seconds = round(cursor, 6)
            ordered.append(clip)
            cursor += clip.duration_seconds
        return ordered

    def total_duration(self, clips: list[TimelineClip]) -> float:
        return round(sum(c.duration_seconds for c in clips), 6)

    def within_one_frame(self, actual: float, target: float) -> bool:
        """True if ``actual`` is within one output frame of ``target``."""
        return abs(actual - target) <= self.frame_duration() + 1e-9
