"""Quality assurance (README sections 15, 16, 18).

Every score here is a HEURISTIC computed from pixels with OpenCV/numpy. They are uncalibrated until the user
approves/rejects clips (calibration data is stored in quality_reports). A provider reporting success is never
sufficient: the clip must decode, match the requested format, be non-degenerate, show motion without flicker,
look like the character/style bible, and continue from the previous accepted frame.

NOT implemented (reported as null, never invented): visible-watermark detection, multimodal prompt adherence,
speech/lip-sync alignment, extra-limb detection."""
from __future__ import annotations
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from app.media import ffmpeg as fx

DEFAULT_THRESHOLDS = {"min_overall": 60, "min_consistency": 55, "min_motion": 50, "min_continuity": 40}


def _sample_frames(path: Path, n: int = 12) -> tuple[list[np.ndarray], float]:
    cap = cv2.VideoCapture(str(path))
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30
    frames = []
    if total <= 0:
        return [], fps
    idxs = sorted({int(i) for i in np.linspace(0, total - 1, min(n, total))})
    for i in idxs:
        cap.set(cv2.CAP_PROP_POS_FRAMES, i)
        ok, f = cap.read()
        if ok:
            frames.append(f)
    cap.release()
    return frames, fps


def _palette_match(frame: np.ndarray, colors: list[str]) -> float:
    """Fraction of requested palette colors found (within tolerance) somewhere in the frame, 0..1."""
    if not colors:
        return 1.0
    small = cv2.resize(frame, (96, 96), interpolation=cv2.INTER_AREA).reshape(-1, 3).astype(np.int32)
    hit = 0
    for c in colors:
        c = c.lstrip("#")
        bgr = np.array([int(c[4:6], 16), int(c[2:4], 16), int(c[0:2], 16)], np.int32)
        d = np.abs(small - bgr).sum(axis=1)
        if (d < 60).sum() >= 6:
            hit += 1
    return hit / len(colors)


def _hist_sim(a: np.ndarray, b: np.ndarray) -> float:
    ha = cv2.calcHist([cv2.resize(a, (160, 160))], [0, 1, 2], None, [8, 8, 8], [0, 256] * 3)
    hb = cv2.calcHist([cv2.resize(b, (160, 160))], [0, 1, 2], None, [8, 8, 8], [0, 256] * 3)
    cv2.normalize(ha, ha)
    cv2.normalize(hb, hb)
    return float(max(0.0, cv2.compareHist(ha, hb, cv2.HISTCMP_CORREL)))


def evaluate_clip(path: Path, *, expected: dict[str, Any], previous_frame: Path | None, provider_caps: dict[str, Any],
                  thresholds: dict[str, float] | None = None, source_path: str | None = None) -> dict[str, Any]:
    th = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
    reasons: list[str] = []
    rep: dict[str, Any] = {"method": "heuristic-v1 (uncalibrated)", "visible_watermark_detected": None, "prompt_adherence": None,
                           "audio_alignment": None, "not_evaluated": ["visible watermark detection", "prompt adherence (needs a multimodal model)", "audio/lip-sync alignment"]}
    try:
        info = fx.probe(path)
    except fx.MediaError as e:
        return {**rep, "decision": "rejected", "reasons": [f"QUALITY_VALIDATION_FAILED: corrupt or unreadable media ({e})"], "corrupt": True}
    rep["probe"] = {k: info.get(k) for k in ("width", "height", "fps", "duration", "codec")}
    # Provenance: when the clip was post-processed (upscaled), record the raw
    # provider resolution too. The router's measured native-resolution factor
    # reads native_width/native_height from here.
    if source_path and Path(source_path).is_file():
        try:
            src_info = fx.probe(source_path)
            rep["probe"]["native_width"] = src_info.get("width")
            rep["probe"]["native_height"] = src_info.get("height")
        except fx.MediaError:
            pass
    if not info["has_video"]:
        return {**rep, "decision": "rejected", "reasons": ["no video stream"], "corrupt": True}
    # resolution / aspect / duration / fps
    ew, eh = expected["width"], expected["height"]
    asp_dev = abs(info["width"] / info["height"] - ew / eh)
    asp_ok = asp_dev < 0.02
    rep["aspect_ok"] = asp_ok
    if not asp_ok:
        # Composition scales and letterboxes every clip to the target frame, so
        # a small aspect deviation is corrected at export; it is a note, not a
        # rejection. A large deviation (wrong orientation) is a real mismatch.
        if asp_dev > 0.15:
            reasons.append(f"aspect ratio mismatch: got {info['width']}x{info['height']}")
        else:
            rep["aspect_note"] = f"aspect {info['width']}x{info['height']} differs from target; composition pads to {ew}x{eh}"
    rep["native_resolution_ok"] = info["width"] >= ew and info["height"] >= eh
    rep["upscale_needed"] = not rep["native_resolution_ok"]
    dur_err = abs(info["video_duration"] - expected["duration"])
    rep["duration_error_s"] = round(dur_err, 3)
    if dur_err > max(0.25, 2.0 / max(1.0, info.get("fps") or 30)):
        reasons.append(f"duration {info['video_duration']:.2f}s differs from requested {expected['duration']:.2f}s")
    frames, fps = _sample_frames(path)
    if len(frames) < 3:
        return {**rep, "decision": "rejected", "reasons": reasons + ["could not decode frames"], "corrupt": True}
    # non-degenerate: not black / not flat
    means = [float(f.mean()) for f in frames]
    stds = [float(f.std()) for f in frames]
    degenerate = max(means) < 8 or max(stds) < 6
    rep["mean_luma_range"] = [round(min(means), 1), round(max(means), 1)]
    if degenerate:
        reasons.append("clip is black or flat")
    # temporal: motion present, flicker low
    diffs = [float(np.abs(frames[i].astype(np.float32) - frames[i - 1].astype(np.float32)).mean()) for i in range(1, len(frames))]
    motion = float(np.mean(diffs)) if diffs else 0.0
    mean_shift = float(np.std(means))
    freeze = motion < 0.15
    flicker = mean_shift > 25
    motion_q = 100.0 * (0.0 if (freeze or degenerate) else 1.0) * max(0.0, 1.0 - max(0.0, mean_shift - 6) / 40.0) * min(1.0, 0.6 + motion / 6.0)
    if freeze and not degenerate:
        reasons.append("clip appears frozen (no motion)")
    if flicker:
        reasons.append("luminance flicker across the clip")
    rep.update(motion_quality=round(motion_q, 1), motion_mean_abs_diff=round(motion, 2), flicker_index=round(mean_shift, 2))
    # identity / style: palette from character + style bibles must appear
    cols = expected.get("palette_colors", [])
    pm = float(np.mean([_palette_match(f, cols) for f in frames])) if cols else 1.0
    rep["character_consistency"] = round(100 * (0.0 if degenerate else pm), 1)
    sty = expected.get("style_colors", [])
    sm = float(np.mean([_palette_match(f, sty) for f in frames])) if sty else 1.0
    rep["style_consistency"] = round(100 * (0.0 if degenerate else min(1.0, 0.35 + 0.65 * sm)), 1)
    # continuity with previous accepted last frame
    if previous_frame and previous_frame.exists():
        prev = cv2.imread(str(previous_frame))
        sim = _hist_sim(prev, frames[0]) if prev is not None else 0.0
        rep["scene_continuity"] = round(100 * sim, 1)
    else:
        rep["scene_continuity"] = None
    # watermark: only declared by the provider; we do not claim detection
    rep["watermark_declared_by_provider"] = provider_caps.get("watermark")
    if expected.get("require_no_watermark") and provider_caps.get("watermark") is not False:
        reasons.append("project requires no watermark and the provider does not confirm watermark-free output")
    parts = [rep["motion_quality"], rep["character_consistency"], rep["style_consistency"]]
    if rep["scene_continuity"] is not None:
        parts.append(rep["scene_continuity"])
    rep["overall"] = round(float(np.mean(parts)), 1)
    if rep["overall"] < th["min_overall"]:
        reasons.append(f"overall score {rep['overall']} below threshold {th['min_overall']}")
    if rep["character_consistency"] < th["min_consistency"]:
        reasons.append("character/style colors from the bible are not present")
    if rep["scene_continuity"] is not None and rep["scene_continuity"] < th["min_continuity"]:
        reasons.append("first frame does not continue from the previous accepted frame")
    rep["thresholds"] = th
    rep["reasons"] = reasons
    rep["decision"] = "accepted" if not reasons else "rejected"
    return rep
