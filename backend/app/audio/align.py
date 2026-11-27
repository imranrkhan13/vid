"""Narration timeline (README section 17). One master audio track for the whole project.

Alignment modes recorded per segment:
  user_supplied    start/end given by the user
  even_split       narration sentences spread evenly across their scene window (NOT speech alignment)
Forced alignment against the audio is not implemented and is never claimed."""
from __future__ import annotations
from typing import Any

from app.directors.director import split_beats
from app.media.ffmpeg import fmt_srt_time


def segments_from_scenes(scenes: list[Any]) -> list[dict[str, Any]]:
    out = []
    n = 0
    for sc in scenes:
        sents = split_beats(sc.narration or "")
        if not sents:
            continue
        step = sc.duration / len(sents)
        for k, s in enumerate(sents):
            out.append({"idx": n, "scene_id": sc.id, "text": s, "start_time": round(sc.start_time + k * step, 3),
                        "end_time": round(sc.start_time + (k + 1) * step - 0.05, 3), "alignment": "even_split"})
            n += 1
    return out


def to_srt(segments: list[Any]) -> str:
    lines = []
    for i, s in enumerate(segments, 1):
        get = (lambda k: s[k]) if isinstance(s, dict) else (lambda k: getattr(s, k))
        lines += [str(i), f"{fmt_srt_time(get('start_time'))} --> {fmt_srt_time(get('end_time'))}", get("text"), ""]
    return "\n".join(lines)
