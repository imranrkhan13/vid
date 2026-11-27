"""Post-process stage between provider download and QA (README section 21).

Providers (especially the free Space) return small, compressed clips. This
stage denoises, upscales to the project's target frame and adds local contrast
so composition consumes the best available pixels — and QA grades exactly what
composition will see.

Honesty rules:
  * The chain is interpolation + sharpening. It CANNOT invent detail; the QA
    report records the source resolution and says so.
  * A clip already at target resolution is passed through untouched.
  * Failures fall back to the raw clip (generation is never lost to a
    post-processing error).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from app.media import ffmpeg as fx


def postprocess(src: Path, dest: Path, *, width: int, height: int, fps: int) -> dict[str, Any]:
    """Denoise + upscale + sharpen `src` into `dest`. Returns provenance metadata.

    Returns {"applied": bool, "source_width", "source_height", ...}; when the
    clip is already at target, `applied` is False and `dest` is not written.
    """
    info = fx.probe(src)
    sw, sh = info.get("width"), info.get("height")
    if not sw or not sh:
        return {"applied": False, "source_width": None, "source_height": None, "note": "unreadable source; used raw"}
    if sw >= width and sh >= height:
        return {"applied": False, "source_width": sw, "source_height": sh, "note": "already at target resolution"}
    dest.parent.mkdir(parents=True, exist_ok=True)
    # hqdn3d: suppress provider compression noise before scaling (scaling noise
    # amplifies it). lanczos: highest-quality scaler ffmpeg ships. unsharp:
    # restores local contrast lost to upscaling — mild settings, no halos.
    vf = (f"hqdn3d=1.5:1.5:6:6,"
          f"scale={width}:{height}:force_original_aspect_ratio=decrease:flags=lanczos,"
          f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,"
          f"unsharp=5:5:0.6:3:3:0.4,fps={fps}")
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-vf", vf,
           "-c:v", "libx264", "-preset", "medium", "-crf", "17", "-pix_fmt", "yuv420p", str(dest)]
    fx.run(cmd, timeout=1800)
    return {"applied": True, "source_width": sw, "source_height": sh,
            "note": f"upscaled from {sw}x{sh}; detail is interpolated, not native"}
