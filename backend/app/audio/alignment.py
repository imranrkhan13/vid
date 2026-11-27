"""Audio and narration alignment (README section 17).

One master audio timeline per project. Narration is preserved across provider
switches and actions align to narration timestamps. No independently generated
narration per scene.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class NarrationCue:
    text: str
    start_seconds: float
    end_seconds: float
    scene_id: str | None = None


class AudioTimeline:
    """A single master narration timeline."""

    def __init__(self, total_duration_seconds: float = 0.0) -> None:
        self.total_duration_seconds = total_duration_seconds
        self.cues: list[NarrationCue] = []

    def add_cue(self, cue: NarrationCue) -> None:
        self.cues.append(cue)

    def align_to_scenes(
        self, scenes: list[tuple[str, float, float]]
    ) -> list[NarrationCue]:
        """Assign cues to scenes by proportional placement.

        ``scenes`` is a list of ``(scene_id, start_seconds, duration_seconds)``.
        This is a deterministic placeholder for forced alignment / speech
        timestamps; real alignment supplies exact timestamps from the audio.
        """
        total = sum(d for _, _, d in scenes)
        if total <= 0:
            return self.cues
        cues: list[NarrationCue] = []
        for cue in self.cues:
            midpoint = (cue.start_seconds + cue.end_seconds) / 2.0
            cursor = 0.0
            chosen = scenes[-1][0] if scenes else None
            for scene_id, start, duration in scenes:
                if cursor <= midpoint < cursor + duration:
                    chosen = scene_id
                    break
                cursor += duration
            cues.append(
                NarrationCue(cue.text, cue.start_seconds, cue.end_seconds, chosen)
            )
        self.cues = cues
        return cues

    def scene_timestamps(self, scene_id: str) -> list[tuple[float, float]]:
        return [
            (c.start_seconds, c.end_seconds) for c in self.cues if c.scene_id == scene_id
        ]

    @classmethod
    def from_segments(cls, segments, total_duration_seconds: float = 0.0) -> "AudioTimeline":
        """Build a timeline from persisted ``NarrationSegment`` rows."""
        timeline = cls(total_duration_seconds=total_duration_seconds)
        for seg in segments:
            timeline.add_cue(
                NarrationCue(
                    text=seg.text,
                    start_seconds=seg.start_seconds,
                    end_seconds=seg.end_seconds,
                    scene_id=seg.scene_id,
                )
            )
        return timeline

    def aligned_within(
        self, scenes: list[tuple[str, float, float]], *, tolerance_seconds: float = 0.05
    ) -> bool:
        """True if every cue's span fits inside its assigned scene's span.

        Used to verify that narration was placed on the correct scene within a
        tolerance — the audio counterpart of the frame-accuracy check.
        """
        spans = {sid: (start, duration) for sid, start, duration in scenes}
        for cue in self.cues:
            if cue.scene_id is None or cue.scene_id not in spans:
                return False
            start, duration = spans[cue.scene_id]
            if cue.start_seconds < start - tolerance_seconds:
                return False
            if cue.end_seconds > start + duration + tolerance_seconds:
                return False
        return True
